import asyncio
import subprocess
import os
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

async def test_playwright_access():
    """
    Kiểm tra xem Playwright có truy cập được TikTok mà không bị chặn không.
    """
    print("\n--- BƯỚC 1: KIỂM TRA TRUY CẬP TIKTOK ---")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True) # Chạy ngầm để test
        context = await browser.new_context()
        page = await context.new_page()
        await stealth_async(page)
        
        try:
            print("Đang truy cập TikTok...")
            await page.goto("https://www.tiktok.com", timeout=30000)
            title = await page.title()
            print(f"Tiêu đề trang: {title}")
            if "TikTok" in title:
                print("=> THÀNH CÔNG: Đã truy cập TikTok mượt mà.")
            else:
                print("=> THẤT BẠI: Có vẻ bị chặn hoặc load không đúng.")
        except Exception as e:
            print(f"=> LỖI: {e}")
        finally:
            await browser.close()

def test_ffmpeg_flip():
    """
    Kiểm tra FFmpeg lật ngược video (hflip).
    """
    print("\n--- BƯỚC 2: KIỂM TRA FFMPEG (LẬT VIDEO) ---")
    # Tạo một file video giả lập nếu không có file thật để test lệnh
    # Ở đây ta giả định có file 'test.mp4' hoặc chỉ in ra câu lệnh
    input_file = "test.mp4"
    output_file = "flipped_test.mp4"
    
    # Câu lệnh lật ngang (hflip)
    command = [
        'ffmpeg', '-y',
        '-i', input_file,
        '-vf', 'hflip',
        '-c:a', 'copy',
        output_file
    ]
    
    print(f"Lệnh dự kiến: {' '.join(command)}")
    print("Mẹo: hflip sẽ lật ngược video theo chiều ngang để lách bản quyền.")
    # Lưu ý: Hàm này chỉ in ra logic vì không chắc chắn có file test.mp4
    if os.path.exists(input_file):
        try:
            subprocess.run(command, check=True, capture_output=True)
            print("=> THÀNH CÔNG: Đã lật video.")
        except Exception as e:
            print(f"=> LỖI FFmpeg: {e}")
    else:
        print("=> THÔNG BÁO: Bỏ qua chạy thật vì không có file 'test.mp4'. Logic lệnh đã sẵn sàng.")

if __name__ == "__main__":
    asyncio.run(test_playwright_access())
    test_ffmpeg_flip()
