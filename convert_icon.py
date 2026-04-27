
import os
from PIL import Image

def create_ico():
    source_png = "app_icon.png"
    target_ico = "app.ico"
    
    print(f"Loading source: {source_png}")
    try:
        img = Image.open(source_png)
        
        # 转换为 ICO (包含多尺寸以适配 Windows 各种显示模式)
        icon_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img.save(target_ico, format='ICO', sizes=icon_sizes)
        print(f"Successfully created {target_ico}")
        
    except Exception as e:
        print(f"Error during conversion: {e}")

if __name__ == "__main__":
    create_ico()
