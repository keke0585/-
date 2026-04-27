import cv2
import numpy as np

def create_icon():
    # 尺寸 1024x1024
    size = 1024
    img = np.zeros((size, size, 3), dtype=np.uint8)

    # 1. 绘制背景：深灰色圆角矩形 (带微弱渐变)
    # 简单模拟渐变：从 (36, 40, 48) 到 (15, 17, 21)
    for i in range(size):
        color = 36 - int(i * (36 - 15) / size)
        color_g = 40 - int(i * (40 - 17) / size)
        color_b = 48 - int(i * (48 - 21) / size)
        img[i, :] = [color_b, color_g, color] # BGR

    # 添加圆角裁切 (其实打包图标时系统会自动处理，但我们做个完美的)
    mask = np.zeros((size, size), dtype=np.uint8)
    radius = 200
    cv2.rectangle(mask, (radius, 0), (size - radius, size), 255, -1)
    cv2.rectangle(mask, (0, radius), (size, size - radius), 255, -1)
    cv2.circle(mask, (radius, radius), radius, 255, -1)
    cv2.circle(mask, (size - radius, radius), radius, 255, -1)
    cv2.circle(mask, (radius, size - radius), radius, 255, -1)
    cv2.circle(mask, (size - radius, size - radius), radius, 255, -1)
    
    img = cv2.bitwise_and(img, img, mask=mask)

    # 2. 绘制镜头模组：厚实的深黑圆
    center = (size // 2, size // 2)
    cv2.circle(img, center, 280, (23, 29, 26), -1) # BGR: #1A1D23 -> (35, 29, 26)
    cv2.circle(img, center, 280, (35, 29, 26), 5)  # 边缘加深

    # 3. 绘制核心：青色发光核心 (#22D3EE)
    cyan_color = (238, 211, 34) # BGR: #22D3EE -> (238, 211, 34)
    core_radius = 60
    
    # 模拟发光：多层高斯模糊
    glow = np.zeros_like(img)
    cv2.circle(glow, center, 100, cyan_color, -1)
    glow = cv2.GaussianBlur(glow, (101, 101), 30)
    img = cv2.addWeighted(img, 1.0, glow, 0.6, 0)
    
    # 实心核心
    cv2.circle(img, center, core_radius, cyan_color, -1)

    # 4. 绘制对焦标记 (L 型)
    thick = 24
    gap = 260
    l_len = 80
    
    # 左上
    cv2.line(img, (center[0]-gap, center[1]-gap), (center[0]-gap+l_len, center[1]-gap), cyan_color, thick, cv2.LINE_AA)
    cv2.line(img, (center[0]-gap, center[1]-gap), (center[0]-gap, center[1]-gap+l_len), cyan_color, thick, cv2.LINE_AA)
    # 右上
    cv2.line(img, (center[0]+gap, center[1]-gap), (center[0]+gap-l_len, center[1]-gap), cyan_color, thick, cv2.LINE_AA)
    cv2.line(img, (center[0]+gap, center[1]-gap), (center[0]+gap, center[1]-gap+l_len), cyan_color, thick, cv2.LINE_AA)
    # 左下
    cv2.line(img, (center[0]-gap, center[1]+gap), (center[0]-gap+l_len, center[1]+gap), cyan_color, thick, cv2.LINE_AA)
    cv2.line(img, (center[0]-gap, center[1]+gap), (center[0]-gap, center[1]+gap-l_len), cyan_color, thick, cv2.LINE_AA)
    # 右下
    cv2.line(img, (center[0]+gap, center[1]+gap), (center[0]+gap-l_len, center[1]+gap), cyan_color, thick, cv2.LINE_AA)
    cv2.line(img, (center[0]+gap, center[1]+gap), (center[0]+gap, center[1]+gap-l_len), cyan_color, thick, cv2.LINE_AA)

    # 5. 保存
    cv2.imwrite('app_icon.png', img)
    print("Icon generated as app_icon.png")

if __name__ == "__main__":
    create_icon()
