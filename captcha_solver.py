import requests
import base64
import time
import asyncio
from browser_controller import BrowserController

class CaptchaSolver:
    def __init__(self, api_key):
        self.api_key = api_key
        self.api_url = "https://api.capsolver.com/createTask"

    async def solve_tiktok_slide(self, background_b64, piece_b64):
        """
        Giải Slide Captcha TikTok qua Capsolver.
        """
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": "SlideCaptchaTask",
                "image": background_b64,
                "puzzle": piece_b64
            }
        }

        try:
            response = requests.post(self.api_url, json=payload, timeout=20)
            result = response.json()
            
            if result.get("errorId") == 0:
                # Trả về tọa độ X
                return result.get("solution", {}).get("x")
            else:
                print(f"Lỗi Capsolver: {result.get('errorCode')}")
                return None
        except Exception as e:
            print(f"Lỗi kết nối API giải captcha: {e}")
            return None

    async def drag_slider(self, page, slider_selector, distance):
        """
        Sử dụng hàm di chuyển chuột Bezier để kéo thanh trượt.
        """
        # Lấy vị trí của thanh trượt
        slider = await page.wait_for_selector(slider_selector)
        box = await slider.bounding_box()
        
        start_x = box['x'] + box['width'] / 2
        start_y = box['y'] + box['height'] / 2
        end_x = start_x + distance
        end_y = start_y + (box['height'] / 2) # Giữ nguyên Y hoặc lệch nhẹ

        # Nhấn giữ
        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        
        # Di chuyển theo đường cong (reuse logic từ BrowserController)
        controller = BrowserController("") # Chỉ dùng để gọi hàm
        await controller.human_mouse_move(page, start_x, start_y, end_x, end_y)
        
        # Thả chuột
        await page.mouse.up()
        print(f"Đã kéo thanh trượt đi {distance}px.")

# Ví dụ logic tích hợp:
# x_offset = await solver.solve_tiktok_slide(bg_b64, piece_b64)
# if x_offset:
#     await solver.drag_slider(page, ".tiktok-slide-button", x_offset)
