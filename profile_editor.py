import asyncio
import os

async def update_tiktok_profile(page, new_bio, avatar_path):
    """
    Tự động cập nhật Bio và Avatar trên TikTok.
    """
    try:
        print("Đang truy cập trang profile...")
        await page.goto("https://www.tiktok.com/profile")
        
        # Đợi và Click 'Sửa hồ sơ'
        # Lưu ý: Selector có thể thay đổi theo giao diện TikTok
        edit_button_selector = "//button[contains(., 'Sửa hồ sơ')] | //button[contains(., 'Edit profile')]"
        await page.wait_for_selector(edit_button_selector, timeout=15000)
        await page.click(edit_button_selector)
        
        print("Đã mở bảng chỉnh sửa.")

        # 1. Cập nhật Bio
        bio_selector = "textarea[placeholder='Tiểu sử'], textarea[placeholder='Bio']"
        await page.wait_for_selector(bio_selector)
        # Xóa dữ liệu cũ bằng cách chọn tất cả và nhấn Backspace
        await page.click(bio_selector)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.fill(bio_selector, new_bio)
        print(f"Đã điền Bio mới: {new_bio}")

        # 2. Cập nhật Avatar
        if os.path.exists(avatar_path):
            # Tìm input type='file' để upload
            file_input_selector = "input[type='file']"
            await page.set_input_files(file_input_selector, avatar_path)
            print(f"Đã tải lên ảnh: {avatar_path}")
            # Chờ ảnh được preview/xử lý (nếu có modal cắt ảnh thì cần thêm logic ở đây)
            await asyncio.sleep(2) 
        else:
            print(f"Cảnh báo: Không tìm thấy ảnh tại {avatar_path}")

        # 3. Bấm Lưu
        save_button_selector = "button:has-text('Lưu'), button:has-text('Save')"
        await page.click(save_button_selector)
        
        # Kiểm tra thông báo thành công
        try:
            # TikTok thường hiện toast hoặc modal đóng lại
            await page.wait_for_selector("text=Cập nhật thành công, text=Success", timeout=5000)
            print("Cập nhật thông tin profile THÀNH CÔNG!")
            return True
        except:
            # Nếu không thấy text success, kiểm tra xem modal đã đóng chưa
            if not await page.is_visible(bio_selector):
                print("Cập nhật thành công (Modal đã đóng).")
                return True
            else:
                print("Có vẻ như chưa lưu được thông tin.")
                return False

    except Exception as e:
        print(f"Lỗi khi cập nhật profile: {e}")
        return False

# Ví dụ tích hợp với BrowserController:
# await update_tiktok_profile(page, "Hello World from Python", "C:/avatar.jpg")
