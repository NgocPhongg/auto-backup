import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://www.tiktok.com/")
        print("Wait for top login button")
        try:
            btn = await page.wait_for_selector('button[data-e2e="top-login-button"]', timeout=10000)
            if btn:
                await btn.click()
                print("Clicked top login button")
        except Exception as e:
            print("No top login button found", e)
        
        await asyncio.sleep(2)
        print("Finding Use phone / email / username")
        try:
            # Let's dump all text in the modal
            modal_text = await page.evaluate("document.body.innerText")
            print("Modal text snippet:", modal_text[:200])
        except Exception as e:
            print("Error", e)
        await browser.close()

asyncio.run(main())
