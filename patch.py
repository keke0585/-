import sys
import re
import os

with open("eyes_modern.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Add STATE properties
if "'playlist': []" not in code:
    code = code.replace(
        "'video_path': None,",
        "'video_path': None,\n    'playlist': [],\n    'playlist_index': 0,\n    'out_dir': None,"
    )

# 2. Add _save_photo function
if "def _save_photo" not in code:
    save_fn = """
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

def _push_event"""
    code = code.replace("def _push_event", save_fn)

# 3. Add to push_event body saving
body_push = "_push_event(f\"BODY ID {int(track_id)}\", cur_str, person_roi)"
if body_push in code:
    code = code.replace(
        body_push,
        body_push + "\n                                        _save_photo(person_roi, video_path, STATE.get('out_dir'), f\"BODY_{int(track_id)}_{cur_str.replace(':', '-')}.jpg\")"
    )

face_push = "_push_event(f\"FACE {reported_faces}\", cur_str, face_roi)"
if face_push in code:
    code = code.replace(
        face_push,
        face_push + "\n                                        _save_photo(face_roi, video_path, STATE.get('out_dir'), f\"FACE_{reported_faces}_{cur_str.replace(':', '-')}.jpg\")"
    )

# 4. Modify action == 'import'
old_import = """                if action == 'import':
                    import subprocess
                    cmd = [sys.executable, '-c', 'import tkinter as tk; from tkinter import filedialog; root=tk.Tk(); root.withdraw(); root.attributes("-topmost", True); print(filedialog.askopenfilename(filetypes=[("Video files", "*.mp4 *.avi *.mov")]))']
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    file_path = res.stdout.strip()
                    if file_path:
                        STATE['video_path'] = file_path
                        cap = cv2.VideoCapture(file_path)
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
                        self.send_json({'success': True, 'video': file_path, 'filename': os.path.basename(file_path)})
                    else:
                        self.send_json({'success': False})"""

new_import = """                if action == 'import':
                    import subprocess
                    script = 'import tkinter as tk, json, os; from tkinter import filedialog; root=tk.Tk(); root.withdraw(); root.attributes("-topmost", True); files=filedialog.askopenfilenames(title="选择一个或多个视频", filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv")]); out_dir="" if not files else filedialog.askdirectory(title="选择照片输出目录(取消则默认在视频同级创建)"); print(json.dumps({"files": list(files) if isinstance(files, tuple) else (root.tk.splitlist(files) if files else []), "out": out_dir}))'
                    cmd = [sys.executable, '-c', script]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    try:
                        import json
                        data = json.loads(res.stdout.strip())
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
                            
                        import os
                        fnames = [os.path.basename(f) for f in files]
                        display_name = f"{fnames[0]} (共{len(fnames)}个视频)" if len(fnames)>1 else fnames[0]
                        self.send_json({'success': True, 'video': files[0], 'filename': display_name})
                    else:
                        self.send_json({'success': False})"""
if old_import in code:
    code = code.replace(old_import, new_import)

# 5. Modify analysis_worker to handle playlist looping

analysis_body_start = """        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            STATE['is_running'] = False
            return"""

analysis_body_end = """            # 极速研判模式，将休眠降至最低以榨干硬件性能进行快速分析
            time.sleep(0.001)

        cap.release()"""

if analysis_body_start in code and analysis_body_end in code:
    start_idx = code.find(analysis_body_start)
    end_idx = code.find(analysis_body_end) + len(analysis_body_end)
    
    body = code[start_idx:end_idx]
    
    # indented body
    indented_body = "    " + body.replace("\n", "\n    ")
    
    new_loop = """        while True:
            video_path = STATE.get('video_path')
            if not video_path: break

""" + indented_body + """
            
            if STATE['request_stop']: break
            
            idx = STATE.get('playlist_index', 0) + 1
            playlist = STATE.get('playlist', [])
            if idx < len(playlist):
                STATE['playlist_index'] = idx
                STATE['video_path'] = playlist[idx]
                STATE['progress'] = 0
                STATE['seek_to'] = None
            else:
                break"""
    
    # fix the returns inside the indentation
    new_loop = new_loop.replace("            STATE['is_running'] = False\n                return", "            break")
    new_loop = new_loop.replace("                if not ret: return", "                if not ret: break")
    new_loop = new_loop.replace("            STATE['is_running'] = False\n            return", "            break")
    
    code = code[:start_idx] + new_loop + code[end_idx:]

with open("eyes_modern.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Patch applied successfully.")
