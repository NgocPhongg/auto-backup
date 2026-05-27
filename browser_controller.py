import asyncio
import random

from playwright.async_api import async_playwright

from gologin_config import get_gologin_api_key

try:
    from gologin import GoLogin
except ImportError:
    GoLogin = None


class BrowserController:
    def __init__(self, api_token=""):
        self.api_token = (api_token or get_gologin_api_key()).strip()
        self.gl = None

    def start_gologin_profile(self, profile_id, embed_token="", restore_last_session=True):
        if not self.api_token:
            raise ValueError("Missing GoLogin API key.")
        if GoLogin is None:
            raise ImportError("Missing gologin package. Install with: pip install gologin")

        config = {
            "token": self.api_token,
            "profile_id": profile_id,
            "restore_last_session": bool(restore_last_session),
            "spawn_browser": True,
        }
        if embed_token:
            config["extra_params"] = [f"--ssmatool-embed-token={embed_token}"]

        self.gl = GoLogin(config)
        return self.gl.start()

    def stop_gologin_profile(self):
        if not self.gl:
            return
        try:
            self.gl.stop()
        except Exception:
            pass
        self.gl = None

    async def apply_stealth(self, page):
        _ = page

    async def human_mouse_move(self, page, start_x, start_y, end_x, end_y):
        control_x1 = start_x + (end_x - start_x) * random.uniform(0.1, 0.4) + random.randint(-50, 50)
        control_y1 = start_y + (end_y - start_y) * random.uniform(0.1, 0.4) + random.randint(-50, 50)
        control_x2 = start_x + (end_x - start_x) * random.uniform(0.6, 0.9) + random.randint(-50, 50)
        control_y2 = start_y + (end_y - start_y) * random.uniform(0.6, 0.9) + random.randint(-50, 50)

        steps = random.randint(15, 30)
        for i in range(steps + 1):
            t = i / steps
            t_adjusted = t * t * (3 - 2 * t)
            x = (1 - t_adjusted) ** 3 * start_x + \
                3 * (1 - t_adjusted) ** 2 * t_adjusted * control_x1 + \
                3 * (1 - t_adjusted) * t_adjusted ** 2 * control_x2 + \
                t_adjusted ** 3 * end_x
            y = (1 - t_adjusted) ** 3 * start_y + \
                3 * (1 - t_adjusted) ** 2 * t_adjusted * control_y1 + \
                3 * (1 - t_adjusted) * t_adjusted ** 2 * control_y2 + \
                t_adjusted ** 3 * end_y

            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.01, 0.05))

    async def human_scroll(self, page):
        scroll_distance = random.randint(300, 800)
        steps = scroll_distance // random.randint(10, 20)

        current_scroll = 0
        for _ in range(steps):
            step_size = random.randint(10, 20)
            await page.mouse.wheel(0, step_size)
            current_scroll += step_size
            if current_scroll >= scroll_distance:
                break
            await asyncio.sleep(random.uniform(0.005, 0.015))


async def example_usage():
    profile_id = "YOUR_PROFILE_ID"
    controller = BrowserController()
    debugger_address = controller.start_gologin_profile(profile_id)
    print(f"GoLogin started at: {debugger_address}")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://{debugger_address}")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://bot.sannysoft.com/")
        await controller.human_mouse_move(page, 100, 100, 500, 500)
        await controller.human_scroll(page)
        await asyncio.sleep(5)


if __name__ == "__main__":
    print("BrowserController ready.")
