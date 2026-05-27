import asyncio
import random
import re

async def scrape_account_financial_info(page):
    """
    Cào thông tin Follow, Quốc gia và Tiền tệ từ Settings và Creator Tools.
    """
    info = {
        "Followers": "N/A",
        "Country": "N/A",
        "Currency": "N/A"
    }
    
    try:
        # 1. Lấy Follow từ trang profile
        await page.goto("https://www.tiktok.com/profile")
        follow_element = page.locator("strong[data-e2e='follower-count']")
        if await follow_element.is_visible():
            info["Followers"] = await follow_element.text_content()

        # 2. Lấy Quốc gia từ trang Account Settings
        await page.goto("https://www.tiktok.com/setting/account")
        # Tìm text chứa "Region" hoặc "Country"
        region_locator = page.locator("div:has-text('Region'), div:has-text('Country')").last
        if await region_locator.is_visible():
            text = await region_locator.text_content()
            # Trích xuất phần text sau nhãn
            match = re.search(r"(Region|Country)\s*(.*)", text)
            if match:
                info["Country"] = match.group(2).strip()

        # 3. Lấy Tiền tệ từ trang Balance
        await page.goto("https://www.tiktok.com/setting/balance")
        balance_locator = page.locator(".balance-amount, .currency-symbol")
        if await balance_locator.first.is_visible():
            text = await balance_locator.first.text_content()
            # Tìm ký hiệu hoặc mã tiền tệ ($, €, VND, v.v.)
            info["Currency"] = text.strip()

    except Exception as e:
        print(f"Lỗi khi cào thông tin tài chính: {e}")
    
    return info

async def auto_apply_monetization(page):
    """
    Tự động nộp đơn đăng ký Creator Rewards Program (CRP).
    """
    try:
        # URL giả định của chương trình CRP
        await page.goto("https://www.tiktok.com/creator-center/rewards")
        
        apply_button = page.locator("button:has-text('Apply'), button:has-text('Đăng ký')")
        
        if not await apply_button.is_visible():
            return "Không tìm thấy nút Đăng ký"
        
        if await apply_button.is_disabled():
            return "Chưa đủ điều kiện"
        
        await apply_button.click()
        print("Đã click nút Apply.")

        # Xử lý xác nhận tuổi (nếu hiện popup)
        age_popup = page.locator(".age-verification-popup, text='Verify your age'")
        if await age_popup.is_visible():
            # Chọn ngày sinh ngẫu nhiên trước năm 2000
            year = random.randint(1985, 1999)
            month = random.randint(1, 12)
            day = random.randint(1, 28)
            
            # Giả định các dropdown selector
            await page.select_option("select.year-select", str(year))
            await page.select_option("select.month-select", str(month))
            await page.select_option("select.day-select", str(day))
            
            await page.click("button:has-text('Confirm'), button:has-text('Xác nhận')")
        
        # Chờ thông báo thành công
        await page.wait_for_selector("text=Success, text=Submitted", timeout=10000)
        return "Đã nộp đơn"

    except Exception as e:
        return f"Lỗi đăng ký: {str(e)}"

async def auto_appeal_and_check_kyc(page, appeal_text):
    """
    Kháng nghị khi bị đình chỉ và kiểm tra trạng thái thuế (KYC).
    """
    status = {"Appeal": "N/A", "KYC": "Unknown"}
    
    try:
        # 1. Check Suspension & Appeal
        await page.goto("https://www.tiktok.com/creator-center/monetization-status")
        content = await page.content()
        
        if any(word in content for word in ["Suspended", "Disqualified", "Bị đình chỉ"]):
            appeal_btn = page.locator("button:has-text('Appeal'), button:has-text('Kháng cáo')")
            if await appeal_btn.is_visible():
                await appeal_btn.click()
                await page.fill("textarea", appeal_text)
                await page.click("button[type='submit']")
                status["Appeal"] = "Đã gửi kháng nghị"
            else:
                status["Appeal"] = "Bị khóa nhưng không thấy nút kháng"
        else:
            status["Appeal"] = "Bình thường"

        # 2. Check KYC/Tax info
        await page.goto("https://www.tiktok.com/setting/tax-information")
        tax_status = await page.locator(".status-label, .kyc-status").text_content()
        
        if "Completed" in tax_status or "Đã hoàn thành" in tax_status:
            status["KYC"] = "Completed"
        elif "Action Required" in tax_status or "Cần xử lý" in tax_status:
            status["KYC"] = "Action Required"
        else:
            status["KYC"] = tax_status.strip()

    except Exception as e:
        print(f"Lỗi kiểm tra trạng thái/KYC: {e}")
    
    return status
