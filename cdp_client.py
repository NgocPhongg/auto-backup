"""
CDP Client nhẹ — Kết nối Chrome DevTools Protocol trực tiếp qua WebSocket.
Thay thế Playwright (~50MB) bằng websockets (~100KB).

Usage:
    async with CDPClient(port=9222) as cdp:
        await cdp.navigate("https://www.tiktok.com/")
        title = await cdp.evaluate("document.title")
        await cdp.click_at(400, 300)
        await cdp.type_text("hello", delay=50)
"""
import asyncio
import json
import base64
import urllib.request
from typing import Optional, Callable, Any

import websockets
from websockets.exceptions import ConnectionClosed


class CDPClient:
    """CDP Client kết nối Chrome qua WebSocket — nhẹ và nhanh."""

    def __init__(self, port: int = 9222):
        self.port = port
        self._ws = None
        self._msg_id = 0
        self._pending = {}       # id → Future
        self._event_handlers = {}  # method → [callbacks]
        self._listener_task = None
        self._closed = False

    # ─── Kết nối ────────────────────────────────────────

    async def connect(self, timeout: float = 10):
        """Kết nối WebSocket tới Chrome CDP."""
        ws_url = await self._get_ws_url(timeout)
        self._ws = await websockets.connect(
            ws_url,
            max_size=16 * 1024 * 1024,  # 16MB cho screencast frames
            ping_interval=None,
        )
        self._closed = False
        self._listener_task = asyncio.create_task(self._listen())
        # Enable các domain cần thiết
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self.send("Network.enable")

    async def _get_ws_url(self, timeout: float) -> str:
        """Lấy WebSocket URL từ CDP endpoint."""
        url = f"http://127.0.0.1:{self.port}/json"
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    targets = json.loads(resp.read())
                    for target in targets:
                        if target.get("type") == "page":
                            ws_url = target.get("webSocketDebuggerUrl", "")
                            if ws_url:
                                return ws_url
            except Exception:
                pass
            await asyncio.sleep(0.3)
        raise ConnectionError(f"CDP không phản hồi sau {timeout}s (port {self.port})")

    async def disconnect(self):
        """Ngắt kết nối."""
        self._closed = True
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    # ─── Gửi/Nhận CDP Commands ──────────────────────────

    async def send(self, method: str, params: dict = None, timeout: float = 30) -> dict:
        """Gửi CDP command và chờ response."""
        if self._closed or not self._ws:
            raise ConnectionError("CDP websocket is closed")

        self._msg_id += 1
        msg_id = self._msg_id
        msg = {"id": msg_id, "method": method}
        if params:
            msg["params"] = params

        future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        try:
            await self._ws.send(json.dumps(msg))
        except ConnectionClosed as e:
            self._pending.pop(msg_id, None)
            self._closed = True
            raise ConnectionError(f"CDP websocket closed: {e}") from e

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"CDP timeout: {method}")
        except ConnectionClosed as e:
            self._pending.pop(msg_id, None)
            self._closed = True
            raise ConnectionError(f"CDP websocket closed: {e}") from e

    async def _listen(self):
        """Background task nhận messages từ WebSocket."""
        try:
            async for raw in self._ws:
                if self._closed:
                    break
                data = json.loads(raw)

                # Response cho command
                if "id" in data:
                    future = self._pending.pop(data["id"], None)
                    if future and not future.done():
                        if "error" in data:
                            future.set_exception(
                                RuntimeError(f"CDP error: {data['error'].get('message', '')}")
                            )
                        else:
                            future.set_result(data.get("result", {}))

                # Event (screencast frame, network, etc.)
                elif "method" in data:
                    method = data["method"]
                    if method in self._event_handlers:
                        params = data.get("params", {})
                        for handler in self._event_handlers[method]:
                            try:
                                result = handler(params)
                                if asyncio.iscoroutine(result):
                                    asyncio.create_task(result)
                            except Exception:
                                pass
        except websockets.ConnectionClosed as e:
            self._closed = True
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(ConnectionError(f"CDP websocket closed: {e}"))
            self._pending.clear()
        except asyncio.CancelledError:
            pass

    def on(self, event: str, handler: Callable):
        """Đăng ký event handler (ví dụ: Page.screencastFrame)."""
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    # ─── Navigation ─────────────────────────────────────

    async def navigate(self, url: str, timeout: float = 60):
        """Điều hướng tới URL, chờ load xong."""
        load_event = asyncio.get_event_loop().create_future()

        def on_load(params):
            if not load_event.done():
                load_event.set_result(True)

        self.on("Page.loadEventFired", on_load)

        await self.send("Page.navigate", {"url": url})

        try:
            await asyncio.wait_for(load_event, timeout=timeout)
        except asyncio.TimeoutError:
            pass  # Timeout navigate không fatal
        finally:
            # Dọn handler
            if "Page.loadEventFired" in self._event_handlers:
                handlers = self._event_handlers["Page.loadEventFired"]
                if on_load in handlers:
                    handlers.remove(on_load)

    async def wait_for_navigation(self, timeout: float = 30):
        """Chờ trang load xong (dùng sau click gây chuyển trang)."""
        load_event = asyncio.get_event_loop().create_future()

        def on_load(params):
            if not load_event.done():
                load_event.set_result(True)

        self.on("Page.loadEventFired", on_load)
        try:
            await asyncio.wait_for(load_event, timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            if "Page.loadEventFired" in self._event_handlers:
                handlers = self._event_handlers["Page.loadEventFired"]
                if on_load in handlers:
                    handlers.remove(on_load)

    # ─── JavaScript ─────────────────────────────────────

    async def evaluate(self, expression: str) -> Any:
        """Chạy JavaScript, trả về kết quả."""
        result = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        remote_obj = result.get("result", {})
        if remote_obj.get("type") == "undefined":
            return None
        return remote_obj.get("value")

    async def query_selector(self, selector: str) -> Optional[int]:
        """Tìm element bằng CSS selector, trả về nodeId."""
        doc = await self.send("DOM.getDocument")
        root_id = doc["root"]["nodeId"]
        try:
            result = await self.send("DOM.querySelector", {
                "nodeId": root_id,
                "selector": selector,
            })
            node_id = result.get("nodeId", 0)
            return node_id if node_id > 0 else None
        except Exception:
            return None

    async def wait_for_selector(self, selector: str, timeout: float = 15) -> bool:
        """Chờ element xuất hiện (poll mỗi 500ms)."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            found = await self.evaluate(f"""
                (() => {{
                    const el = document.querySelector('{selector}');
                    return el ? true : false;
                }})()
            """)
            if found:
                return True
            await asyncio.sleep(0.5)
        return False

    async def is_visible(self, selector: str) -> bool:
        """Kiểm tra element có hiển thị không."""
        return await self.evaluate(f"""
            (() => {{
                const el = document.querySelector('{selector}');
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            }})()
        """) or False

    # ─── Input Events ───────────────────────────────────

    async def click_at(self, x: int, y: int):
        """Click tại tọa độ (x, y)."""
        for event_type in ["mousePressed", "mouseReleased"]:
            await self.send("Input.dispatchMouseEvent", {
                "type": event_type,
                "x": x, "y": y,
                "button": "left",
                "clickCount": 1,
            })
            await asyncio.sleep(0.05)

    async def click_selector(self, selector: str):
        """Click vào element bằng selector (lấy vị trí center rồi click)."""
        pos = await self.evaluate(f"""
            (() => {{
                const el = document.querySelector('{selector}');
                if (!el) return null;
                const rect = el.getBoundingClientRect();
                return {{x: rect.x + rect.width/2, y: rect.y + rect.height/2}};
            }})()
        """)
        if pos:
            await self.click_at(int(pos["x"]), int(pos["y"]))
        else:
            raise RuntimeError(f"Không tìm thấy: {selector}")

    async def type_text(self, text: str, delay: int = 50):
        """Gõ text từng ký tự (giống người thật)."""
        for char in text:
            await self.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "text": char,
                "key": char,
            })
            await self.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": char,
            })
            await asyncio.sleep(delay / 1000)

    async def press_key(self, key: str):
        """Nhấn phím đặc biệt (Enter, Tab, Escape...)."""
        key_map = {
            "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
            "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
            "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
            "Backspace": {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
        }
        info = key_map.get(key, {"key": key, "code": key})
        await self.send("Input.dispatchKeyEvent", {"type": "keyDown", **info})
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", **info})

    async def mouse_move(self, x: int, y: int):
        """Di chuyển chuột."""
        await self.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": x, "y": y,
        })

    async def scroll(self, x: int, y: int, delta_x: int = 0, delta_y: int = -300):
        """Scroll tại vị trí (x, y)."""
        await self.send("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": x, "y": y,
            "deltaX": delta_x,
            "deltaY": delta_y,
        })

    # ─── Cookies ────────────────────────────────────────

    async def set_cookies(self, cookies: list):
        """Bơm cookies (format: [{"name": ..., "value": ..., "domain": ...}])."""
        await self.send("Network.setCookies", {"cookies": cookies})

    async def get_cookies(self) -> list:
        """Lấy tất cả cookies."""
        result = await self.send("Network.getCookies")
        return result.get("cookies", [])

    # ─── Screencast (Live Preview) ──────────────────────

    async def start_screencast(self, on_frame: Callable, quality: int = 50,
                                max_width: int = 840, max_height: int = 600,
                                every_nth_frame: int = 2):
        """
        Bắt đầu stream screenshot liên tục từ browser.
        on_frame(data_bytes): callback nhận ảnh JPEG dạng bytes.
        """
        self._screencast_callback = on_frame

        async def handle_frame(params):
            # Decode base64 → bytes
            raw_data = params.get("data", "")
            session_id = params.get("sessionId")
            metadata = params.get("metadata", {})

            if raw_data:
                try:
                    data = base64.b64decode(raw_data)
                    # Gọi callback (hiển thị frame)
                    self._screencast_callback(data)
                except Exception as e:
                    print(f"[Screencast] Frame decode error: {e}")

            # ACK để nhận frame tiếp (BẮT BUỘC!)
            if session_id is not None:
                try:
                    await self.send("Page.screencastFrameAck",
                                    {"sessionId": session_id}, timeout=5)
                except Exception:
                    pass

        self.on("Page.screencastFrame", handle_frame)
        await self.send("Page.startScreencast", {
            "format": "jpeg",
            "quality": quality,
            "maxWidth": max_width,
            "maxHeight": max_height,
            "everyNthFrame": every_nth_frame,
        })

    async def stop_screencast(self):
        """Dừng screencast."""
        try:
            await self.send("Page.stopScreencast", timeout=5)
        except Exception:
            pass

    # ─── Utilities ──────────────────────────────────────

    async def set_viewport(self, width: int, height: int):
        """Đặt kích thước viewport."""
        await self.send("Emulation.setDeviceMetricsOverride", {
            "width": width,
            "height": height,
            "deviceScaleFactor": 1,
            "mobile": False,
        })

    async def take_screenshot(self) -> bytes:
        """Chụp screenshot 1 lần, trả về bytes PNG."""
        result = await self.send("Page.captureScreenshot", {"format": "png"})
        return base64.b64decode(result.get("data", ""))

    async def get_url(self) -> str:
        """Lấy URL hiện tại."""
        result = await self.evaluate("window.location.href")
        return result or ""

    async def get_title(self) -> str:
        """Lấy title trang."""
        result = await self.evaluate("document.title")
        return result or ""
