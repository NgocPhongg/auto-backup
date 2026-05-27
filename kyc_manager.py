import os
import asyncio

async def auto_upload_kyc(page, username, base_folder_path):
    """
    Tự động tải ảnh KYC (mặt trước/mặt sau) từ thư mục cục bộ.
    Cấu trúc: {base_folder_path}/{username}/front.jpg và back.jpg
    """
    user_folder = os.path.join(base_folder_path, username)
    front_path = os.path.join(user_folder, "front.jpg")
    back_path = os.path.join(user_folder, "back.jpg")

    # Kiểm tra file có tồn tại không
    if not os.path.exists(front_path) or not os.path.exists(back_path):
        print(f"Lỗi: Thiếu ảnh KYC cho {username} tại {user_folder}")
        return False

    try:
        print(f"Đang thực hiện KYC cho {username}...")
        await page.goto("https://www.tiktok.com/setting/kyc") # URL giả định trang KYC

        # 1. Tải ảnh Mặt trước
        # Tìm input file đầu tiên hoặc theo label
        front_input = page.locator("input[type='file']").first
        await front_input.set_input_files(front_path)
        print("Đã tải ảnh mặt trước.")
        await asyncio.sleep(2) # Chờ load

        # 2. Tải ảnh Mặt sau
        # Tìm input file thứ hai
        back_input = page.locator("input[type='file']").nth(1)
        await back_input.set_input_files(back_path)
        print("Đã tải ảnh mặt sau.")
        await asyncio.sleep(2)

        # 3. Bấm Submit
        submit_btn = page.locator("button:has-text('Submit'), button:has-text('Gửi')")
        await submit_btn.click()

        # Kiểm tra kết quả
        try:
            await page.wait_for_selector("text=Success, text=Thành công", timeout=10000)
            print(f"KYC {username} THÀNH CÔNG!")
            return True
        except:
            print(f"KYC {username} có thể đã thất bại hoặc cần kiểm duyệt thủ công.")
            return False

    except Exception as e:
        print(f"Lỗi quy trình KYC: {e}")
        return False
