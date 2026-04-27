import win7_compat_patch
win7_compat_patch.apply()
import os
import sys
import cv2
import time
import json
import base64
import numpy as np
import threading
from datetime import timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import tkinter as tk
from tkinter import filedialog
import urllib.parse
import mimetypes
import logging
import socket
import traceback

# ===============================================
# 0. 资源路径处理 (兼容本地开发与 PyInstaller 打包)
# ===============================================
def get_resource_path(relative_path):
    """ 获取文件的绝对路径，兼容 PyInstaller 的临时释放目录 """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller 在运行时会把文件释放到 _MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

logging.basicConfig(
    filename='error.log', 
    level=logging.ERROR, 
    format='%(asctime)s [%(levelname)s] %(message)s'
)

torch = None
YOLO = None
mp = None
TORCH_IMPORT_ERROR = None
YOLO_IMPORT_ERROR = None
MEDIAPIPE_IMPORT_ERROR = None
TORCH_DLL_HANDLES = []


def _configure_torch_dll_search_paths():
    """在 Windows 打包环境中补齐 torch/lib 的 DLL 搜索路径。"""
    global TORCH_DLL_HANDLES

    if sys.platform != 'win32':
        return
    if TORCH_DLL_HANDLES:
        return

    candidate_dirs = []
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))

    if hasattr(sys, '_MEIPASS'):
        candidate_dirs.extend([
            os.path.join(sys._MEIPASS, 'torch', 'lib'),
            os.path.join(sys._MEIPASS, '_internal', 'torch', 'lib'),
        ])

    candidate_dirs.extend([
        os.path.join(exe_dir, '_internal', 'torch', 'lib'),
        os.path.join(exe_dir, 'torch', 'lib'),
    ])

    seen_dirs = set()
    resolved_dirs = []
    for candidate in candidate_dirs:
        normalized = os.path.abspath(candidate)
        if normalized in seen_dirs or not os.path.isdir(normalized):
            continue
        seen_dirs.add(normalized)
        resolved_dirs.append(normalized)

    if not resolved_dirs:
        return

    current_path = os.environ.get('PATH', '')
    path_entries = current_path.split(os.pathsep) if current_path else []

    for dll_dir in resolved_dirs:
        if dll_dir not in path_entries:
            path_entries.insert(0, dll_dir)
        if hasattr(os, 'add_dll_directory'):
            try:
                TORCH_DLL_HANDLES.append(os.add_dll_directory(dll_dir))
            except OSError:
                logging.exception("注册 torch DLL 搜索目录失败: %s", dll_dir)

    os.environ['PATH'] = os.pathsep.join(path_entries)

def _load_mediapipe_runtime():
    """延迟加载 MediaPipe，失败时允许自动降级到 OpenCV 方案。"""
    global mp, MEDIAPIPE_IMPORT_ERROR

    if mp is not None:
        return True
    if MEDIAPIPE_IMPORT_ERROR is not None:
        return False

    try:
        import mediapipe as imported_mp
        mp = imported_mp
        return True
    except Exception as exc:
        MEDIAPIPE_IMPORT_ERROR = exc
        logging.exception("加载 mediapipe 失败")
        return False

def _create_face_detector():
    """优先使用 MediaPipe；失败时降级到 OpenCV Haar 级联。"""
    if _load_mediapipe_runtime():
        try:
            mp_face_detection = mp.solutions.face_detection
            detector = mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.35)
            return {'backend': 'mediapipe', 'detector': detector, 'label': 'MediaPipe'}
        except Exception:
            logging.exception("初始化 MediaPipe 人脸检测失败")

    cascade_candidates = []
    try:
        cascade_candidates.append(os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml'))
    except Exception:
        pass
    cascade_candidates.append(get_resource_path('haarcascade_frontalface_default.xml'))

    for cascade_path in cascade_candidates:
        if not cascade_path or not os.path.exists(cascade_path):
            continue
        try:
            detector = cv2.CascadeClassifier(cascade_path)
            if not detector.empty():
                return {'backend': 'opencv_haar', 'detector': detector, 'label': 'OpenCV Haar'}
        except Exception:
            logging.exception("初始化 OpenCV Haar 人脸检测失败: %s", cascade_path)

    return {'backend': 'none', 'detector': None, 'label': '不可用'}

def detect_faces_in_roi(face_runtime, frame, roi_x1, roi_y1, roi_x2, roi_y2):
    """在人体 ROI 内做人脸检测，统一返回绝对坐标框。"""
    backend = face_runtime.get('backend')
    detector = face_runtime.get('detector')
    body_roi = frame[roi_y1:roi_y2, roi_x1:roi_x2]
    if detector is None or body_roi.size == 0:
        return []

    face_boxes = []

    try:
        if backend == 'mediapipe':
            body_roi_rgb = cv2.cvtColor(cv2.resize(body_roi, (320, 320)), cv2.COLOR_BGR2RGB)
            mp_results = detector.process(body_roi_rgb)
            if not mp_results or not mp_results.detections:
                return []

            roi_w = max(roi_x2 - roi_x1, 1)
            roi_h = max(roi_y2 - roi_y1, 1)
            for detection in mp_results.detections:
                bboxC = detection.location_data.relative_bounding_box
                fx = int(bboxC.xmin * roi_w) + roi_x1
                fy = int(bboxC.ymin * roi_h) + roi_y1
                fw = int(bboxC.width * roi_w)
                fh = int(bboxC.height * roi_h)
                face_boxes.append((fx, fy, fw, fh))
            return face_boxes

        if backend == 'opencv_haar':
            gray = cv2.cvtColor(body_roi, cv2.COLOR_BGR2GRAY)
            min_side = max(24, min(gray.shape[:2]) // 6)
            faces = detector.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=4,
                minSize=(min_side, min_side)
            )
            for fx, fy, fw, fh in faces:
                face_boxes.append((int(fx) + roi_x1, int(fy) + roi_y1, int(fw), int(fh)))
            return face_boxes
    except Exception:
        logging.exception("执行人脸检测失败")

    return []

def _load_ai_runtime():
    """延迟加载 AI 依赖，避免在不兼容系统上启动即崩溃。"""
    global torch, YOLO, TORCH_IMPORT_ERROR, YOLO_IMPORT_ERROR

    if torch is not None and YOLO is not None:
        return True
    if TORCH_IMPORT_ERROR is not None or YOLO_IMPORT_ERROR is not None:
        return False

    try:
        _configure_torch_dll_search_paths()
        import torch as imported_torch
        torch = imported_torch
    except Exception as exc:
        TORCH_IMPORT_ERROR = exc
        logging.exception("加载 torch 失败")
        return False

    try:
        from ultralytics import YOLO as imported_yolo
        YOLO = imported_yolo
    except Exception as exc:
        YOLO_IMPORT_ERROR = exc
        logging.exception("加载 ultralytics 失败")
        return False

    return True

def get_ai_runtime_error():
    """返回 AI 运行库错误信息；无错误时返回 None。"""
    if _load_ai_runtime():
        return None

    if TORCH_IMPORT_ERROR is not None:
        return (
            "PyTorch 运行库加载失败。\n"
            f"底层错误: {TORCH_IMPORT_ERROR}\n"
            "当前交付包大概率不是在 Windows 10/11 + Python 3.8.10 的兼容环境下打包，"
            "或打包时使用了错误版本的 Torch。\n"
            "请在 Windows 10/11 + Python 3.8.10 环境中重新打包，并固定使用：\n"
            "torch==2.0.1 / torchvision==0.15.2 / torchaudio==2.0.2"
        )

    if YOLO_IMPORT_ERROR is not None:
        return (
            "Ultralytics 运行库加载失败。\n"
            f"底层错误: {YOLO_IMPORT_ERROR}\n"
            "请检查打包环境中的 ultralytics 与 torch 是否安装完整。"
        )

    return "AI 运行库加载失败。"

def has_acceleration_support():
    """统一检测 GPU/MPS 能力。"""
    if not _load_ai_runtime() or torch is None:
        return False

    try:
        if torch.cuda.is_available():
            return True
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            return True
    except Exception:
        return False

    return False

# ===============================================
# 1. 全局状态与锁 (用于前后端解耦通信)
# ===============================================
STATE = {
    'video_path': None,
    'playlist': [],
    'playlist_index': 0,
    'out_dir': None,
    'is_running': False,
    'is_paused': False,
    'request_stop': False,
    'seek_to': None,
    'frame_current': None,
    'frame_lock': threading.Lock(),
    'progress': 0,
    'time_str': '00:00:00 / 00:00:00',
    'elapsed': '0s',
    'hardware': '等待启动...',
    'events': [],
    'event_lock': threading.Lock(),
    'last_event_id': 0,
    'config': {}
}

analysis_thread_handle = None

# 辅助函数: base64 编码图片
def encode_img_to_b64_uri(cv2_img):
    _, buffer = cv2.imencode('.jpg', cv2_img)
    base64_str = base64.b64encode(buffer).decode('utf-8')
    return f"data:image/jpeg;base64,{base64_str}"

# ===============================================
# 2. 核心 AI 引擎线程 (完全从 PyQt5 剥离)
# ===============================================
def adjust_gamma(image, gamma=1.5):
    invGamma = 1.0 / gamma
    table = np.array([((i / 255.0) ** invGamma) * 255 for i in np.arange(0, 256)]).astype("uint8")
    return cv2.LUT(image, table)

def is_box_moving(fgMask, x, y, w, h, scale_w, scale_h):
    sx = max(0, int(x * scale_w))
    sy = max(0, int(y * scale_h))
    sw = min(int(w * scale_w), fgMask.shape[1] - sx)
    sh = min(int(h * scale_h), fgMask.shape[0] - sy)
    if sw <= 0 or sh <= 0: return False
    roi = fgMask[sy:sy + sh, sx:sx + sw]
    motion_ratio = cv2.countNonZero(roi) / (sw * sh + 1)
    return motion_ratio > 0.10

def get_global_motion(fgMask):
    total = fgMask.shape[0] * fgMask.shape[1]
    return cv2.countNonZero(fgMask) / total

def analysis_worker(initial_config):
    global STATE
    STATE['is_running'] = True
    STATE['request_stop'] = False

    runtime_error = get_ai_runtime_error()
    if runtime_error:
        STATE['hardware'] = "[ERROR] AI 运行库加载失败"
        log_to_gui(f"[ERROR] {runtime_error}")
        STATE['is_running'] = False
        STATE['request_stop'] = False
        return
    
    video_path = STATE['video_path']
    if not video_path:
        STATE['is_running'] = False
        return

    model_name = initial_config.get('model', 'yolo11n.pt')
    frame_interval = 2 

    has_gpu = has_acceleration_support()

    try:
        backSub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=25, detectShadows=False)
        model = YOLO(model_name)
        
        face_runtime = _create_face_detector()
        face_detection = face_runtime.get('detector')
        face_backend_label = face_runtime.get('label', '不可用')
        if face_runtime.get('backend') == 'mediapipe':
            log_to_gui("[INFO] 人脸检测引擎: MediaPipe")
        elif face_runtime.get('backend') == 'opencv_haar':
            log_to_gui("[WARN] MediaPipe 不可用，已自动降级为 OpenCV Haar 人脸检测。")
        else:
            log_to_gui("[WARN] 未检测到可用的人脸检测引擎，双模联动将自动关闭。")

        while True:
            video_path = STATE.get('video_path')
            if not video_path: break

            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                idx = STATE.get('playlist_index', 0) + 1
                playlist = STATE.get('playlist', [])
                if idx < len(playlist):
                    STATE['playlist_index'] = idx
                    STATE['video_path'] = playlist[idx]
                    STATE['progress'] = 0
                    STATE['seek_to'] = None
                    continue
                else:
                    break
    
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0: fps = 25.0
    
            for _ in range(5):
                ret, frame = cap.read()
                if ret:
                    small = cv2.resize(frame, (320, 180))
                    backSub.apply(small)
    
            track_history = {}
            reported_ids = set()
            reported_faces = 0
            recent_face_events = []
            recent_body_events = []
    
            current_pos = 0
            start_time = time.time()
            total_paused_time = 0
    
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, first_frame = cap.read()
            if not ret or first_frame is None or first_frame.shape[0] == 0 or first_frame.shape[1] == 0:
                cap.release()
                idx = STATE.get('playlist_index', 0) + 1
                playlist = STATE.get('playlist', [])
                if idx < len(playlist):
                    STATE['playlist_index'] = idx
                    STATE['video_path'] = playlist[idx]
                    STATE['progress'] = 0
                    STATE['seek_to'] = None
                    continue
                else:
                    break
            
            orig_h, orig_w = first_frame.shape[:2]
    
            inference_size = int(initial_config.get('speed', '640'))
            real_inference_size = inference_size if inference_size > 0 else max(orig_w, orig_h)
            process_w = 320
            process_h = int(orig_h * (320 / orig_w))
            scale_w = process_w / orig_w
            scale_h = process_h / orig_h
    
            tracking_cooldown = 0
            
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0) # 重新设回0
    
            while cap.isOpened() and not STATE['request_stop']:
                if STATE.get('video_path') != video_path:
                    break

                # 实时读取最新配置
                config = STATE.get('config', initial_config)
                smart_mode = config.get('smart', True)
                save_crops = config.get('save', True)
                drawbox = config.get('drawbox', True)
                use_gpu = config.get('use_gpu', True)
                use_dual_mode = (config.get('strategy') == 'dual')
                if not face_detection: use_dual_mode = False
                
                inference_size = int(config.get('speed', '640'))
                real_inference_size = inference_size if inference_size > 0 else max(orig_w, orig_h)
    
                target_device = 'cpu'
                if use_gpu and has_gpu:
                    if torch.cuda.is_available(): target_device = '0'
                    else: target_device = 'mps'
    
                if target_device == '0':
                    STATE['hardware'] = f"[GPU加速] {torch.cuda.get_device_name(0)} | 人脸:{face_backend_label}"
                elif target_device == 'mps':
                    STATE['hardware'] = f"[GPU加速] Apple Silicon (MPS) | 人脸:{face_backend_label}"
                else:
                    STATE['hardware'] = f"[CPU模式] | 人脸:{face_backend_label}"
    
                if STATE.get('seek_to') is not None:
                    trg_pos = int((STATE['seek_to'] / 100.0) * total_frames)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, trg_pos - 1))
                    ret, frame = cap.read()
                    if ret:
                        current_pos = trg_pos
                        current_video_time = current_pos / fps
                        STATE['progress'] = STATE['seek_to']
                        tot_str = str(timedelta(seconds=int(total_frames/fps)))
                        STATE['time_str'] = f"{str(timedelta(seconds=int(current_video_time)))} / {tot_str}"
                        # 绘制首帧
                        with STATE['frame_lock']:
                            h, w = frame.shape[:2]
                            scale = min(1.0, 800 / max(w, 1))
                            small_render = cv2.resize(frame, (int(w*scale), int(h*scale)))
                            _, buffer = cv2.imencode('.jpg', small_render, [cv2.IMWRITE_JPEG_QUALITY, 70])
                            STATE['frame_current'] = buffer.tobytes()
                    STATE['seek_to'] = None
    
                if STATE['is_paused']:
                    pause_start = time.time()
                    while STATE['is_paused'] and not STATE['request_stop']:
                        if STATE.get('seek_to') is not None:
                            break
                        time.sleep(0.1)
                    total_paused_time += (time.time() - pause_start)
                    if STATE.get('seek_to') is not None:
                        continue
                    if STATE['request_stop']: break
    
                ret, frame = cap.read()
                if not ret: break
    
                current_pos += 1
                current_video_time = current_pos / fps
    
                # 更新 UI 状态
                elapsed_seconds = int(time.time() - start_time - total_paused_time)
                STATE['elapsed'] = str(timedelta(seconds=elapsed_seconds))
                if total_frames > 0:
                    STATE['progress'] = int((current_pos / total_frames) * 100)
                    cur_str = str(timedelta(seconds=int(current_video_time)))
                    tot_str = str(timedelta(seconds=int(total_frames/fps)))
                    STATE['time_str'] = f"{cur_str} / {tot_str}"
    
                # 智能跳频
                should_run_detection = False
                if smart_mode:
                    frame_small = cv2.resize(frame, (process_w, process_h))
                    fgMask = backSub.apply(frame_small)
                    motion_score = get_global_motion(fgMask)
    
                    if tracking_cooldown > 0:
                        current_imgsz = real_inference_size
                        if current_pos % max(1, frame_interval//2) == 0: should_run_detection = True
                        tracking_cooldown -= 1
                    elif motion_score > 0.005:
                        current_imgsz = real_inference_size
                        should_run_detection = True
                    else:
                        current_imgsz = 320
                        if current_pos % (frame_interval*4) == 0: should_run_detection = True
                else:
                    current_imgsz = real_inference_size
                    if current_pos % frame_interval == 0: should_run_detection = True
    
                # 无论是否检测，更新推流画面以保持 UI 视频播放效果
                render_frame = frame.copy()
    
                if should_run_detection:
                    if not smart_mode:
                        fgMask = backSub.apply(cv2.resize(frame, (process_w, process_h)))
    
                    enhanced_frame = adjust_gamma(frame, gamma=1.4)
                    if enhanced_frame is not None:
                        # 运行 YOLO
                        target_mode = config.get('target', 'both')
                        if target_mode == 'person':
                            classes_list = [0]
                        elif target_mode == 'vehicle':
                            classes_list = [2, 3, 5, 7]
                        else:
                            classes_list = [0, 2, 3, 5, 7]

                        use_half = False if target_device == 'cpu' else True
                        results = model.track(enhanced_frame, persist=True, classes=classes_list, conf=0.25, iou=0.8, verbose=False, device=target_device, imgsz=current_imgsz, half=use_half, max_det=50)
    
                        current_body_boxes = []
                        person_detected_this_frame = False
    
                        if results[0].boxes.id is not None:
                            boxes = results[0].boxes.xyxy.cpu().numpy()
                            track_ids = results[0].boxes.id.cpu().numpy()
                            cls_ids = results[0].boxes.cls.cpu().numpy()
    
                            for box, track_id, cls_id in zip(boxes, track_ids, cls_ids):
                                x1, y1, x2, y2 = map(int, box)
                                w, h = x2 - x1, y2 - y1

                                is_person = (int(cls_id) == 0)
                                if is_person and w > h * 1.1: continue

                                if not is_box_moving(fgMask, x1, y1, w, h, scale_w, scale_h): continue
    
                                if is_person:
                                    current_body_boxes.append([x1, y1, x2, y2])
                                person_detected_this_frame = True
                                
                                track_history[track_id] = track_history.get(track_id, 0) + 1
                                if track_history[track_id] >= 2:
                                    label_name = "Body" if is_person else "Vehicle"
                                    color = (0, 255, 0) if is_person else (0, 165, 255)

                                    if drawbox:
                                        cv2.rectangle(render_frame, (x1, y1), (x2, y2), color, 2)
                                        cv2.putText(render_frame, f"{label_name}:{int(track_id)}", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    
                                    if track_id not in reported_ids and save_crops:
                                        body_center = (x1 + w // 2, y1 + h // 2)
                                        is_duplicate = False
                                        recent_body_events = [e for e in recent_body_events if current_video_time - e['time'] < 5.0]
                                        for e in recent_body_events:
                                            dist = np.sqrt((body_center[0]-e['center'][0])**2 + (body_center[1]-e['center'][1])**2)
                                            if dist < 120: is_duplicate = True; break
                                        
                                        if not is_duplicate:
                                            recent_body_events.append({'center': body_center, 'time': current_video_time})
                                            reported_ids.add(track_id)
                                            person_roi = enhanced_frame[y1:max(y1+1,y2), x1:max(x1+1,x2)].copy()
                                            _push_event(f"{label_name} ID {int(track_id)}", cur_str, person_roi)
                                            _save_photo(person_roi, video_path, STATE.get('out_dir'), f"{label_name}_{int(track_id)}_{cur_str.replace(':', '-')}.jpg")
    
                        if use_dual_mode and current_body_boxes:
                            for body_box in current_body_boxes:
                                bx1, by1, bx2, by2 = body_box
                                crop_h, crop_w = by2 - by1, bx2 - bx1
                                roi_y1 = max(0, by1 - int(crop_h * 0.1))
                                roi_y2 = min(orig_h, by1 + int(crop_h * 0.6))
                                roi_x1 = max(0, bx1 - int(crop_w * 0.1))
                                roi_x2 = min(orig_w, bx2 + int(crop_w * 0.1))
    
                                detected_faces = detect_faces_in_roi(face_runtime, frame, roi_x1, roi_y1, roi_x2, roi_y2)
                                if detected_faces:
                                    for fx, fy, fw, fh in detected_faces:
                                        face_center = (fx + fw // 2, fy + fh // 2)

                                        # 简单重复过滤
                                        is_duplicate = False
                                        recent_face_events = [e for e in recent_face_events if current_video_time - e['time'] < 3.0]
                                        for e in recent_face_events:
                                            dist = np.sqrt((face_center[0]-e['center'][0])**2 + (face_center[1]-e['center'][1])**2)
                                            if dist < 100: is_duplicate = True; break
                                        
                                        if is_duplicate: continue
    
                                        recent_face_events.append({'center': face_center, 'time': current_video_time})
                                        reported_faces += 1
                                        person_detected_this_frame = True
    
                                        if drawbox:
                                            cv2.rectangle(render_frame, (fx, fy), (fx + fw, fy + fh), (255, 0, 0), 2)
                                            cv2.putText(render_frame, "Face Captured", (fx, fy - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
    
                                        if save_crops:
                                            safe_x1, safe_y1 = max(0, fx - fw), max(0, fy - fh)
                                            safe_x2, safe_y2 = min(orig_w, fx + 2 * fw), min(orig_h, fy + 3 * fh)
                                            face_roi = enhanced_frame[safe_y1:safe_y2, safe_x1:safe_x2].copy()
                                            _push_event(f"FACE {reported_faces}", cur_str, face_roi)
                                            _save_photo(face_roi, video_path, STATE.get('out_dir'), f"FACE_{reported_faces}_{cur_str.replace(':', '-')}.jpg")
    
                        if smart_mode and person_detected_this_frame:
                            tracking_cooldown = 15
    
                # 存入全局缓存提供给 HTTP MJPEG 轮询
                with STATE['frame_lock']:
                    # 统一转为高宽较小的图来推送以便流畅无卡顿
                    h, w = render_frame.shape[:2]
                    scale = min(1.0, 800 / w)
                    small_render = cv2.resize(render_frame, (int(w*scale), int(h*scale)))
                    _, buffer = cv2.imencode('.jpg', small_render, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    STATE['frame_current'] = buffer.tobytes()
    
                # 极速研判模式，将休眠降至最低以榨干硬件性能进行快速分析
                time.sleep(0.001)
    
            cap.release()
            
            if STATE['request_stop']: break
            
            if STATE.get('video_path') != video_path:
                continue
            
            idx = STATE.get('playlist_index', 0) + 1
            playlist = STATE.get('playlist', [])
            if idx < len(playlist):
                STATE['playlist_index'] = idx
                STATE['video_path'] = playlist[idx]
                STATE['progress'] = 0
                STATE['seek_to'] = None
            else:
                break
    except Exception as e:
        logging.exception("Analysis worker runtime error")
        STATE['hardware'] = f"[ERROR] 崩溃了: {str(e)}"
    finally:
        STATE['is_running'] = False
        STATE['request_stop'] = False


def _save_photo(img, current_video_path, custom_out_dir, filename):
    import os, cv2
    try:
        base_dir = custom_out_dir if custom_out_dir else os.path.dirname(current_video_path)
        video_name = os.path.splitext(os.path.basename(current_video_path))[0]
        full_dir = os.path.join(base_dir, f"{video_name}_照片结果")
        if not os.path.exists(full_dir):
            os.makedirs(full_dir)
        cv2.imwrite(os.path.join(full_dir, filename), img)
    except: pass

def _push_event(label, time_str, roi_img):
    if roi_img is None or roi_img.size == 0: return
    b64_uri = encode_img_to_b64_uri(roi_img)
    with STATE['event_lock']:
        STATE['last_event_id'] += 1
        STATE['events'].append({
            'id': STATE['last_event_id'],
            'timeStr': time_str,
            'label': label,
            'imageBlob': b64_uri
        })
        # 只保留最近 50 条防止内存爆炸
        if len(STATE['events']) > 50:
            STATE['events'].pop(0)

# ===============================================
# 3. HTTP 服务器适配器 (提供前端 API 与流服务)
# ===============================================

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    pass

class WebUIHandler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        try:
            self.send_response(status)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))
        except Exception as e:
            log_to_gui(f"[ERROR] 发送 JSON 失败: {e}")

    def log_message(self, format, *args):
        # 重写此方法，防止在 PyInstaller --windowed 模式下因 sys.stderr 为 None 而崩溃
        pass

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == '/api/status':
                has_gpu = False
                try:
                    if torch.cuda.is_available(): has_gpu = True
                    elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available(): has_gpu = True
                except: pass
                
                self.send_json({
                    'isRunning': STATE['is_running'],
                    'isPaused': STATE['is_paused'],
                    'hardware': STATE['hardware'],
                    'progress': STATE['progress'],
                    'timeStr': STATE['time_str'],
                    'elapsed': STATE['elapsed'],
                    'hasGpu': has_gpu,
                    'playlist': STATE['playlist'],
                    'playlistIndex': STATE['playlist_index']
                })
                return

            if path == '/api/events':
                query = urllib.parse.parse_qs(parsed.query)
                since = int(query.get('since', ['-1'])[0])
                with STATE['event_lock']:
                    new_evts = [e for e in STATE['events'] if e['id'] > since]
                self.send_json({'events': new_evts})
                return

            if path == '/video_feed':
                # MJPEG Stream
                self.send_response(200)
                self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
                self.end_headers()
                try:
                    while True:
                        with STATE['frame_lock']:
                            frame = STATE['frame_current']
                        if frame:
                            self.wfile.write(b'--frame\r\n')
                            self.send_header('Content-Type', 'image/jpeg')
                            self.send_header('Content-Length', len(frame))
                            self.end_headers()
                            self.wfile.write(frame)
                            self.wfile.write(b'\r\n')
                        if not STATE['is_running'] and not STATE.get('video_path'):
                            # 既没运行也没视频，降低轮询频率
                            time.sleep(0.5)
                        else:
                            time.sleep(0.04) # 约 25fps
                except Exception:
                    pass # Client disconnected
                return

            # 静态文件路由
            if path == '/':
                path = '/index.html'

            # 统一资源发现
            rel_path = os.path.join('web', path.lstrip('/'))
            file_path = get_resource_path(rel_path)

            if os.path.exists(file_path) and not os.path.isdir(file_path):
                self.send_response(200)
                mime_type, _ = mimetypes.guess_type(file_path)
                self.send_header('Content-type', mime_type or 'application/octet-stream')
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                if 'placeholder.' in path or 'favicon.ico' in path:
                    self.send_response(200)
                    self.send_header('Content-type', 'image/png')
                    self.end_headers()
                    self.wfile.write(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='))
                    return
                log_to_gui(f"[WARN] 404 资源不存在: {rel_path}")
                self.send_error(404, f"File Not Found: {rel_path}")
        except Exception as e:
            err_msg = traceback.format_exc()
            log_to_gui(f"[ERROR] 服务器异常: {e}\n{err_msg}")
            try: self.send_error(500, str(e))
            except: pass

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == '/api/action':
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                req = json.loads(post_data.decode('utf-8'))
                action = req.get('action')

                global analysis_thread_handle
                
                if action == 'import':
                    import subprocess
                    script = 'import tkinter as tk, json, os; from tkinter import filedialog; root=tk.Tk(); root.withdraw(); root.attributes("-topmost", True); files=filedialog.askopenfilenames(title="选择一个或多个视频", filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv")]); out_dir="" if not files else filedialog.askdirectory(title="选择照片输出目录(取消则默认在视频同级创建)"); print("<<OUTPUT>>" + json.dumps({"files": list(files) if isinstance(files, tuple) else (root.tk.splitlist(files) if files else []), "out": out_dir}) + "<<OUTPUT>>")'
                    cmd = [sys.executable, '-c', script]
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                    try:
                        out_str = res.stdout
                        if "<<OUTPUT>>" in out_str:
                            out_str = out_str.split("<<OUTPUT>>")[1]
                        data = json.loads(out_str.strip())
                        files = data.get('files', [])
                        out_dir = data.get('out', '')
                    except:
                        files = []
                        out_dir = ""
                    
                    if files:
                        STATE['playlist'] = files
                        STATE['playlist_index'] = 0
                        STATE['video_path'] = files[0]
                        STATE['out_dir'] = out_dir if out_dir else None
                        
                        cap = cv2.VideoCapture(files[0])
                        if cap.isOpened():
                            ret, frame = cap.read()
                            if ret:
                                with STATE['frame_lock']:
                                    h, w = frame.shape[:2]
                                    scale = min(1.0, 800 / max(w, 1))
                                    small_render = cv2.resize(frame, (int(w*scale), int(h*scale)))
                                    _, buffer = cv2.imencode('.jpg', small_render, [cv2.IMWRITE_JPEG_QUALITY, 70])
                                    STATE['frame_current'] = buffer.tobytes()
                            cap.release()
                            
                        fnames = [os.path.basename(f) for f in files]
                        display_name = f"{fnames[0]} (共{len(fnames)}个视频)" if len(fnames)>1 else fnames[0]
                        self.send_json({'success': True, 'video': files[0], 'filename': display_name})
                    else:
                        self.send_json({'success': False})

                elif action == 'play':
                    if not STATE['is_running']:
                        config = req.get('config', {})
                        STATE['config'] = config
                        analysis_thread_handle = threading.Thread(target=analysis_worker, args=(config,))
                        analysis_thread_handle.daemon = True
                        analysis_thread_handle.start()
                        self.send_json({'success': True, 'state': 'started'})
                    elif STATE['is_paused']:
                        STATE['is_paused'] = False
                        self.send_json({'success': True, 'state': 'resumed'})
                    else:
                        self.send_json({'success': True, 'state': 'already_running'})

                elif action == 'pause':
                    if STATE['is_running']:
                        STATE['is_paused'] = True
                    self.send_json({'success': True, 'state': 'paused'})

                elif action == 'seek':
                    if STATE['video_path']:
                        STATE['seek_to'] = req.get('progress', 0.0)
                    self.send_json({'success': True})

                elif action == 'next':
                    if STATE['video_path']:
                        idx = STATE.get('playlist_index', 0) + 1
                        playlist = STATE.get('playlist', [])
                        if idx < len(playlist):
                            STATE['playlist_index'] = idx
                            STATE['video_path'] = playlist[idx]
                            STATE['progress'] = 0
                            STATE['seek_to'] = None
                        else:
                            STATE['request_stop'] = True
                            STATE['progress'] = 0
                    self.send_json({'success': True})

                elif action == 'stop':
                    if STATE['is_running']:
                        STATE['request_stop'] = True
                        STATE['progress'] = 0
                    self.send_json({'success': True})

                elif action == 'update_config':
                    if 'config' in req:
                        STATE['config'] = req['config']
                    self.send_json({'success': True})
                else:
                    self.send_json({'success': False, 'msg': 'unknown action'})
        except Exception as e:
            err_msg = traceback.format_exc()
            log_to_gui(f"[ERROR] API 异常: {e}\n{err_msg}")
            try: self.send_json({'success': False, 'error': str(e)}, 500)
            except: pass

# ===============================================
# 4. 可视化启动器 (Tkinter GUI, 专为 Win7 兼容设计)
# ===============================================
http_server_instance = None
PORT = 28666

def get_local_ips():
    ips = ["127.0.0.1"]
    try:
        # 获取本机所有地址
        hostname = socket.gethostname()
        addr_infos = socket.getaddrinfo(hostname, None)
        for info in addr_infos:
            ip = info[4][0]
            if ":" not in ip and ip != "127.0.0.1": # 过滤 IPv6 和环回
                if ip not in ips: ips.append(ip)
    except:
        pass
    
    # 常用连接测试方法
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        main_ip = s.getsockname()[0]
        if main_ip not in ips: ips.append(main_ip)
        s.close()
    except:
        pass
    return ips

def start_server():
    global http_server_instance
    try:
        http_server_instance = ThreadedHTTPServer(('0.0.0.0', PORT), WebUIHandler)
        log_to_gui(f"[OK] 视频分析引擎启动，服务端口: {PORT}")
        http_server_instance.serve_forever()
    except Exception as e:
        log_to_gui(f"[ERR] 引擎启动失败: {e}")

# 用于重定向日志到GUI的全局变量
gui_log_text = None
gui_status_var = None

def log_to_gui(message):
    try:
        print(message)
    except:
        pass # 防止 GBK 环境下 print 包含无法编码字符时崩溃
    
    if gui_log_text:
        try:
            gui_log_text.config(state=tk.NORMAL)
            gui_log_text.insert(tk.END, message + "\n")
            gui_log_text.see(tk.END)
            gui_log_text.config(state=tk.DISABLED)
        except:
            pass

def on_start_engine_clicked():
    global http_server_instance
    if http_server_instance is not None:
        log_to_gui("[WARN] 引擎已经在运行中，无需重复启动。")
        return

    runtime_error = get_ai_runtime_error()
    if runtime_error:
        gui_status_var.set("状态: AI 依赖异常 (OFF)")
        log_to_gui("[ERROR] AI 运行库未就绪，已阻止启动。")
        log_to_gui(f"[ERROR] {runtime_error}")
        try:
            from tkinter import messagebox
            messagebox.showerror("AI 运行库加载失败", runtime_error)
        except Exception:
            pass
        return
        
    log_to_gui("[WAIT] 正在启动后台 HTTP 分析服务...")
    gui_status_var.set("状态: 引擎运行中 (ON)")
    
    srv_thread = threading.Thread(target=start_server)
    srv_thread.daemon = True
    srv_thread.start()

def on_open_browser_clicked():
    import webbrowser
    if http_server_instance is None:
        log_to_gui("[WARN] 请先点击『启动核心引擎』后再打开界面！")
    else:
        log_to_gui(f"[INFO] 正在浏览器中加载系统控制台 http://127.0.0.1:{PORT}")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        # 如果有局域网 IP，额外提示一下
        ips = get_local_ips()
        if len(ips) > 1:
            log_to_gui(f"[HINT] 若宿主机无法打开，请尝试访问: http://{ips[-1]}:{PORT}")

def on_closing(root):
    log_to_gui("[EXIT] 正在截断分析流并安全释放所有系统资源...")
    STATE['request_stop'] = True
    STATE['is_running'] = False
    
    # 停止 HTTP Server
    if http_server_instance:
        try:
            http_server_instance.shutdown()
        except:
            pass
            
    root.destroy()
    os._exit(0)

if __name__ == '__main__':
    from tkinter import scrolledtext
    import sys
    
    # 防止在 PyInstaller --windowed 模式下由于 sys.stdout/stderr 为 None 导致的崩溃
    if sys.stdout is None: sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None: sys.stderr = open(os.devnull, 'w')
    
    root = tk.Tk()
    root.title("智能视频研判系统 - 离线控制面板 (Win7)")
    root.geometry("600x480")
    root.resizable(False, False)
    root.configure(padx=20, pady=15)
    
    # 获取系统默认背景色以便样式融合
    bg_color = root.cget('bg')
    
    # 顶部状态
    gui_status_var = tk.StringVar(value="状态: 引擎未启动 (OFF)")
    lbl_status = tk.Label(root, textvariable=gui_status_var, font=("Microsoft YaHei", 12, "bold"))
    lbl_status.pack(pady=(0, 10))
    
    # 地址栏指示区
    frame_addr = tk.Frame(root)
    frame_addr.pack(fill=tk.X, pady=5)
    lbl_addr = tk.Label(frame_addr, text="服务地址(若未自动跳转可复制):", font=("Microsoft YaHei", 9))
    lbl_addr.pack(side=tk.LEFT)
    entry_addr = tk.Entry(frame_addr, font=("Consolas", 10), width=35)
    
    # 智能显示地址
    all_ips = get_local_ips()
    display_addr = f"http://127.0.0.1:{PORT}"
    if len(all_ips) > 1:
        display_addr += f"  (外访: http://{all_ips[-1]}:{PORT})"
    
    entry_addr.insert(0, display_addr)
    entry_addr.config(state="readonly")
    entry_addr.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

    # 控制按钮区
    frame_btn = tk.Frame(root)
    frame_btn.pack(fill=tk.X, pady=10)
    
    btn_start = tk.Button(frame_btn, text="[RUN] 启动核心引擎", width=16, height=2, font=("Microsoft YaHei", 10), command=on_start_engine_clicked)
    btn_start.pack(side=tk.LEFT, expand=True, padx=5)
    
    btn_browser = tk.Button(frame_btn, text="[WEB] 打开研判界面", width=16, height=2, font=("Microsoft YaHei", 10), command=on_open_browser_clicked)
    btn_browser.pack(side=tk.LEFT, expand=True, padx=5)
    
    btn_stop = tk.Button(frame_btn, text="[STOP] 关闭系统", width=16, height=2, font=("Microsoft YaHei", 10), command=lambda: on_closing(root))
    btn_stop.pack(side=tk.LEFT, expand=True, padx=5)
    
    # 日志输出区
    lbl_log = tk.Label(root, text="系统运行日志:", font=("Microsoft YaHei", 9))
    lbl_log.pack(anchor=tk.W, pady=(5, 5))
    
    gui_log_text = scrolledtext.ScrolledText(root, height=10, width=50, state=tk.DISABLED, bg="#F0F0F0", font=("Consolas", 9))
    gui_log_text.pack(fill=tk.BOTH, expand=True)
    
    # 初始化环境并防呆捕捉关闭
    root.protocol("WM_DELETE_WINDOW", lambda: on_closing(root))
    
    log_to_gui("[INFO] 欢迎使用智能视频研判系统！")
    log_to_gui(f"[INFO] 检测到运行环境: {sys.platform}")

    runtime_error = get_ai_runtime_error()
    if runtime_error:
        gui_status_var.set("状态: AI 依赖异常 (OFF)")
        log_to_gui(f"[ERROR] {runtime_error}")
    elif not has_acceleration_support():
        log_to_gui("[WARN] 未检测到兼容 GPU 加速，核心引擎将采用 CPU 运算。")
    else:
        log_to_gui("[INFO] 已检测到 GPU，核心引擎支持硬件加速。")
        
    log_to_gui("[INFO] 请点击【启动核心引擎】")
    
    # 进入 Tkinter 主循环，阻塞黑框
    root.mainloop()
