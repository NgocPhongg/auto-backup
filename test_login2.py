import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        print("Goto login page directly")
        await page.goto("https://www.tiktok.com/login/phone-or-email/email")
        await asyncio.sleep(3)
        try:
            # Let's check if the input exists
            user_input = await page.wait_for_selector('input[name="username"]', timeout=5000)
            if user_input:
                print("Found username input directly!")
            else:
                print("Username input not found")
        except Exception as e:
            print("No username input found", e)
        await browser.close()

asyncio.run(main())
