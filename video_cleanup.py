import asyncio

async def mass_delete_or_private_videos(page, action='delete'):
    """
    Tự động xóa hoặc ẩn toàn bộ video trên kênh.
    action: 'delete' (Xóa) hoặc 'private' (Chuyển sang Chỉ mình tôi)
    """
    try:
        print(f"Bắt đầu quy trình {action} toàn bộ video...")
        await page.goto("https://www.tiktok.com/profile")
        
        # Chờ video grid hiện ra
        video_selector = "div[data-e2e='user-post-item']"
        await page.wait_for_selector(video_selector, timeout=10000)
        
        while True:
            # Luôn chọn video đầu tiên trong grid
            videos = page.locator(video_selector)
            count = await videos.count()
            
            if count == 0:
                print("Không còn video nào để xử lý.")
                break
                
            print(f"Còn {count} video. Đang xử lý video đầu tiên...")
            await videos.first.click()
            
            # Đợi video mở ra (player mode)
            await page.wait_for_selector("button[data-e2e='browse-video-more-post']", timeout=5000)
            
            # Click nút Options (3 chấm)
            # Selector có thể thay đổi, thường là biểu tượng cạnh nút Share
            await page.click("button[data-e2e='browse-video-more-post']")
            
            if action == 'delete':
                # Click Xóa
                delete_btn = page.locator("button:has-text('Delete'), button:has-text('Xóa')")
                await delete_btn.click()
                # Xác nhận xóa trên popup
                confirm_btn = page.locator("button:has-text('Confirm'), button:has-text('Delete')").last
                await confirm_btn.click()
                print("Đã xóa 1 video.")
            
            elif action == 'private':
                # Click Quyền riêng tư
                privacy_btn = page.locator("button:has-text('Privacy settings'), button:has-text('Cài đặt quyền riêng tư')")
                await privacy_btn.click()
                # Chọn 'Only me'
                await page.click("text='Only me', text='Chỉ mình tôi'")
                # Đóng popup hoặc lưu
                await page.keyboard.press("Escape")
                print("Đã chuyển 1 video sang chế độ riêng tư.")
                # Cần đóng video player để quay lại grid (hoặc sang video tiếp theo)
                await page.click("button[data-e2e='browse-close']")
            
            # Chờ một lát để DOM cập nhật
            await asyncio.sleep(2)
            
            # Nếu là delete, trang sẽ tự load lại grid hoặc mất element. 
            # Nếu là private, ta đóng player rồi lặp lại.
            if action == 'delete':
                # Reload để chắc chắn grid đã cập nhật
                await page.reload()
                await page.wait_for_selector(video_selector, timeout=5000).catch(lambda e: None)

    except Exception as e:
        print(f"Lỗi khi dọn dẹp video: {e}")

# Ví dụ:
# await mass_delete_or_private_videos(page, action='private')
