"""
hotmail_otp.py — Module độc lập lấy mã OTP từ email (Hotmail/Gmail/Khác).
══════════════════════════════════════════════════════════════════
Tách riêng khỏi gologin_worker / cdp_worker để dùng độc lập trong Bảng Đăng Ký.

Hỗ trợ:
  • Hotmail / Outlook / Live / MSN  → OAuth2 (MSAL) — bắt buộc
  • Gmail                           → Basic Auth (App Password)
  • Provider khác                   → Basic Auth

Trả về dict chuẩn:
  {
      "otp":               "123456" hoặc "",
      "new_refresh_token": "..." hoặc "",
      "status":            "success" | "error" | "no_mail",
      "message":           "Mô tả chi tiết cho UI"
  }
"""

import re
import time
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════
DEFAULT_CLIENT_ID = "08162f7c-0fd2-4200-a84a-f25a4db0b584"
AUTHORITY = "https://login.microsoftonline.com/consumers"

# Danh sách scope thử lần lượt (Client ID khác nhau đăng ký trên resource khác nhau)
SCOPES_TO_TRY = [
    ["https://outlook.office365.com/IMAP.AccessAsUser.All"],  # Thunderbird, nhiều tool
    ["https://outlook.office.com/IMAP.AccessAsUser.All"],      # Azure portal mới
    ["https://graph.microsoft.com/IMAP.AccessAsUser.All"],     # Microsoft Graph
    ["https://graph.microsoft.com/Mail.ReadWrite"],            # Fallback: Graph Mail API
]

# Domains thuộc hệ Microsoft
MICROSOFT_DOMAINS = ('hotmail.', 'outlook.', 'live.', 'msn.', 'passport.')

# IMAP server mapping cho các provider phổ biến
IMAP_SERVERS = {
    'gmail.com':      'imap.gmail.com',
    'yahoo.com':      'imap.mail.yahoo.com',
    'yahoo.co.jp':    'imap.mail.yahoo.co.jp',
    'aol.com':        'imap.aol.com',
    'yandex.com':     'imap.yandex.com',
    'yandex.ru':      'imap.yandex.ru',
    'mail.ru':        'imap.mail.ru',
    'zoho.com':       'imap.zoho.com',
    'protonmail.com': 'imap.protonmail.ch',  # Cần Bridge
    'icloud.com':     'imap.mail.me.com',
}

# Microsoft error codes → thông báo tiếng Việt thân thiện
MS_ERROR_MAP = {
    'AADSTS50126': 'Sai mật khẩu email',
    'AADSTS50034': 'Tài khoản email không tồn tại',
    'AADSTS50053': 'Tài khoản bị khóa (quá nhiều lần sai)',
    'AADSTS50057': 'Tài khoản bị vô hiệu hóa',
    'AADSTS50076': 'Tài khoản bật MFA — cần Refresh Token',
    'AADSTS50079': 'Tài khoản bật MFA — cần Refresh Token',
    'AADSTS65001': 'App chưa được cấp quyền (consent)',
    'AADSTS700082': 'Refresh Token đã hết hạn (>90 ngày)',
    'AADSTS700084': 'Refresh Token đã hết hạn',
    'AADSTS50173': 'Refresh Token bị thu hồi (đổi pass?)',
    'AADSTS7000218': 'Client ID không hỗ trợ ROPC',
    'AADSTS90002': 'Tenant không hợp lệ',
    'AADSTS9002313': 'Request bị chặn (rate limit)',
    'interaction_required': 'Cần đăng nhập thủ công trên trình duyệt',
    'invalid_grant': 'Token không hợp lệ hoặc đã hết hạn',
}


def _make_result(otp="", new_refresh_token="", status="error", message=""):
    """Helper tạo dict kết quả chuẩn."""
    return {
        "otp": otp,
        "new_refresh_token": new_refresh_token,
        "status": status,
        "message": message,
    }


# ═══════════════════════════════════════════════════════════
# VALIDATE
# ═══════════════════════════════════════════════════════════
def validate_email_format(email: str) -> bool:
    """Kiểm tra email có đúng format cơ bản không."""
    if not email or not isinstance(email, str):
        return False
    # Regex đơn giản: có @ và có domain
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email.strip()))


def detect_email_provider(email: str) -> str:
    """Phân loại provider: 'microsoft', 'gmail', 'other'."""
    email_lower = email.lower().strip()
    if any(d in email_lower for d in MICROSOFT_DOMAINS):
        return 'microsoft'
    if 'gmail.com' in email_lower:
        return 'gmail'
    return 'other'


def get_imap_server(email: str) -> str:
    """Trả về IMAP server phù hợp cho email."""
    domain = email.lower().strip().split('@')[-1]

    # Microsoft — luôn dùng server OAuth2
    if any(d in email.lower() for d in MICROSOFT_DOMAINS):
        return 'imap-mail.outlook.com'

    # Tra bảng mapping
    if domain in IMAP_SERVERS:
        return IMAP_SERVERS[domain]

    # Fallback: đoán từ domain
    return f'imap.{domain}'


# ═══════════════════════════════════════════════════════════
# OAUTH2 — MICROSOFT
# ═══════════════════════════════════════════════════════════
def _parse_ms_error(result: dict) -> str:
    """Phân tích lỗi từ MSAL result → thông báo thân thiện."""
    error_desc = result.get('error_description', '') or ''
    error_code = result.get('error', '') or ''

    # Tìm mã lỗi AADSTS trong description
    for code, msg in MS_ERROR_MAP.items():
        if code in error_desc or code in error_code:
            return msg

    # Fallback: cắt ngắn error description
    if error_desc:
        # Lấy dòng đầu tiên (trước \r\n)
        first_line = error_desc.split('\r\n')[0].split('\n')[0]
        return first_line[:120]

    return error_code or "Lỗi OAuth2 không xác định"


def get_microsoft_oauth_token(email: str, password: str = "",
                               refresh_token: str = "", client_id: str = "",
                               progress_callback=None) -> dict:
    """Lấy OAuth2 access_token cho Microsoft (Hotmail/Outlook/Live/MSN).

    Flow:
      Với mỗi client_id (custom → default):
        Với mỗi scope (office365 → office.com → graph):
          1. Thử refresh_token
          2. Thử ROPC (nếu có password)

    Returns:
        {
            "access_token":  "..." hoặc "",
            "refresh_token": "..." hoặc "",
            "error":         "" hoặc "mô tả lỗi"
        }
    """
    def _log(msg):
        if progress_callback:
            progress_callback(msg)

    # Import msal
    try:
        import msal
    except ImportError:
        return {"access_token": "", "refresh_token": "", 
                "error": "Thiếu thư viện msal — chạy: pip install msal"}

    user_client_id = (client_id or "").strip()
    
    # Danh sách client_id sẽ thử: user's → default (nếu khác)
    client_ids_to_try = []
    if user_client_id:
        client_ids_to_try.append(("custom", user_client_id))
    if not user_client_id or user_client_id != DEFAULT_CLIENT_ID:
        client_ids_to_try.append(("default", DEFAULT_CLIENT_ID))

    last_error = ""
    rt_expired = False  # Đánh dấu nếu RT chết (70000 = hết hạn/thu hồi)

    for cid_label, cid in client_ids_to_try:
        _log(f"🔑 Client ID [{cid_label}]: {cid[:16]}...")

        try:
            app = msal.PublicClientApplication(cid, authority=AUTHORITY)
        except Exception as e:
            last_error = f"Lỗi khởi tạo MSAL ({cid_label}): {str(e)[:150]}"
            _log(f"⚠️ {last_error}")
            continue

        # ═══ Cách 1: Refresh Token — thử từng scope ═══
        if refresh_token and refresh_token.strip():
            for scope in SCOPES_TO_TRY:
                scope_short = scope[0].split('/')[-1]  # VD: "IMAP.AccessAsUser.All"
                resource = scope[0].split('/')[2]        # VD: "outlook.office365.com"
                
                try:
                    result = app.acquire_token_by_refresh_token(
                        refresh_token=refresh_token.strip(),
                        scopes=scope
                    )
                    if "access_token" in result:
                        new_rt = result.get("refresh_token", "")
                        _log(f"✅ OAuth OK! (RT + {resource}/{scope_short})")
                        return {
                            "access_token": result["access_token"],
                            "refresh_token": new_rt if new_rt else refresh_token,
                            "error": ""
                        }
                    else:
                        err_desc = str(result.get('error_description', ''))
                        if 'AADSTS70011' in err_desc:
                            # Scope không đăng ký cho Client ID này → thử scope tiếp
                            continue
                        elif 'AADSTS70000' in err_desc:
                            # RT hết hạn/bị thu hồi → không cần thử scope khác
                            rt_expired = True
                            error_msg = _parse_ms_error(result)
                            last_error = f"RT hết hạn/bị thu hồi ({cid_label})"
                            _log(f"⚠️ RT hết hạn ({cid_label}): {error_msg}")
                            break
                        else:
                            error_msg = _parse_ms_error(result)
                            _log(f"⚠️ RT ({resource}): {error_msg}")
                            last_error = f"RT ({cid_label}): {error_msg}"
                except Exception as e:
                    err = str(e)[:120]
                    _log(f"⚠️ RT exception ({resource}): {err}")
                    last_error = f"RT exception: {err}"

        # ═══ Cách 2: ROPC Flow — thử từng scope ═══
        if password and password.strip():
            _log(f"🔑 [{cid_label}] ROPC (username + password)...")
            ropc_tried = False
            for scope in SCOPES_TO_TRY:
                scope_short = scope[0].split('/')[-1]
                resource = scope[0].split('/')[2]
                
                try:
                    result = app.acquire_token_by_username_password(
                        username=email.strip(),
                        password=password.strip(),
                        scopes=scope
                    )
                    ropc_tried = True
                    if "access_token" in result:
                        new_rt = result.get("refresh_token", "")
                        _log(f"✅ OAuth OK! (ROPC + {resource}/{scope_short})")
                        return {
                            "access_token": result["access_token"],
                            "refresh_token": new_rt,
                            "error": ""
                        }
                    else:
                        err_desc = str(result.get('error_description', ''))
                        if 'AADSTS70011' in err_desc:
                            continue  # Scope không đăng ký
                        else:
                            error_msg = _parse_ms_error(result)
                            _log(f"⚠️ ROPC ({resource}): {error_msg}")
                            last_error = f"ROPC ({cid_label}): {error_msg}"
                            break  # Lỗi xác thực → không cần thử scope khác
                except ValueError as e:
                    # "Unable to find wstrust endpoint" = MSA không hỗ trợ ROPC
                    if 'wstrust' in str(e).lower():
                        _log(f"⚠️ [{cid_label}] MSA account — ROPC không hỗ trợ")
                        last_error = "Tài khoản MSA (cá nhân) không hỗ trợ ROPC — cần Refresh Token mới"
                        break
                    raise
                except Exception as e:
                    err = str(e)[:120]
                    _log(f"⚠️ ROPC exception: {err}")
                    last_error = f"ROPC exception: {err}"
                    break

        # Log trước khi thử client_id tiếp
        if cid_label == "custom" and len(client_ids_to_try) > 1:
            _log("🔄 Thử Client ID mặc định...")

    # ═══ KẾT LUẬN ═══
    if not password and not refresh_token:
        return {
            "access_token": "", "refresh_token": "",
            "error": "Thiếu cả Refresh Token lẫn mật khẩu"
        }

    # Thông báo đặc biệt nếu RT hết hạn
    if rt_expired:
        last_error = (
            "Refresh Token đã hết hạn! "
            "Đang thử auto-renew..."
        )

    return {"access_token": "", "refresh_token": "", "error": last_error}


# ═══════════════════════════════════════════════════════════
# AUTO-RENEW REFRESH TOKEN (Playwright + Device Code Flow)
# ═══════════════════════════════════════════════════════════
def auto_renew_refresh_token(email: str, password: str,
                              client_id: str = "",
                              progress_callback=None) -> dict:
    """Tự động lấy Refresh Token mới bằng Playwright (ẩn danh) + Device Code Flow.

    Flow:
      1. MSAL tạo Device Code (mã + URL)
      2. Playwright mở trình duyệt ẩn danh
      3. Tự nhập mã, đăng nhập email/password
      4. MSAL polling tự nhận access_token + refresh_token mới

    Returns:
        {
            "access_token":  "..." hoặc "",
            "refresh_token": "..." hoặc "",
            "error":         "" hoặc "mô tả lỗi"
        }
    """
    def _log(msg):
        if progress_callback:
            progress_callback(msg)

    # ── Import ──
    try:
        import msal
    except ImportError:
        return {"access_token": "", "refresh_token": "",
                "error": "Thiếu msal — pip install msal"}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"access_token": "", "refresh_token": "",
                "error": "Thiếu playwright — pip install playwright && playwright install chromium"}

    import threading

    if not client_id or not client_id.strip():
        client_id = DEFAULT_CLIENT_ID

    _log("🔄 Auto-Renew: Đang tạo Device Code...")

    app = msal.PublicClientApplication(client_id.strip(), authority=AUTHORITY)

    # ── Tìm scope hoạt động ──
    flow = None
    working_scopes = None
    for scopes in SCOPES_TO_TRY:
        try:
            f = app.initiate_device_flow(scopes=scopes)
            if "user_code" in f:
                flow = f
                working_scopes = scopes
                break
        except Exception:
            continue

    if not flow or "user_code" not in flow:
        return {"access_token": "", "refresh_token": "",
                "error": "Không tạo được Device Code Flow — Client ID có thể chưa hỗ trợ"}

    user_code = flow["user_code"]
    verify_url = flow.get("verification_uri", "https://www.microsoft.com/link")
    _log(f"🔑 Device Code: {user_code} | URL: {verify_url}")

    # ── MSAL Polling chạy nền ──
    token_result = {"_pending": True}

    def _poll():
        try:
            r = app.acquire_token_by_device_flow(flow)
            token_result.update(r)
        except Exception as e:
            token_result["error"] = str(e)
        finally:
            token_result.pop("_pending", None)

    poll_thread = threading.Thread(target=_poll, daemon=True)
    poll_thread.start()

    # ── Playwright Automation (Incognito) ──
    _log("🌐 Mở trình duyệt ẩn danh...")
    browser_error = ""

    try:
        with sync_playwright() as p:
            # Launch Chromium với anti-detection flags
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-infobars",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-features=PasswordManagerOnboarding",
                    "--disable-save-password-bubble",
                    "--window-size=600,800",
                ]
            )

            # Context ẩn danh + user agent thật
            context = browser.new_context(
                viewport={"width": 600, "height": 800},
                locale="vi-VN",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                )
            )

            # Ẩn webdriver flag
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

            page = context.new_page()

            try:
                # ═══ BƯỚC 1: Mở trang nhập Device Code ═══
                _log(f"📱 Đang mở {verify_url}...")
                page.goto(verify_url, wait_until="load", timeout=30000)
                time.sleep(2)

                # ═══ BƯỚC 2: Nhập Device Code ═══
                _log(f"⌨️ Nhập mã: {user_code}")
                code_input = page.wait_for_selector('input#otc', timeout=15000)
                if not code_input:
                    code_input = page.wait_for_selector('input[name="otc"]', timeout=5000)
                code_input.fill(user_code)
                time.sleep(1)
                page.locator('#idSIButton9').click()
                time.sleep(4)

                # Dismiss Chrome Passkey/Autofill popup nếu có
                page.keyboard.press("Escape")
                time.sleep(1)

                # ═══ BƯỚC 3: Nhập Email ═══
                # Trang remoteconnect.srf dùng id="usernameEntry" (KHÔNG phải name="loginfmt")
                _log(f"📧 Nhập email: {email}")
                email_input = page.wait_for_selector(
                    'input#usernameEntry', timeout=15000
                )
                if not email_input:
                    # Fallback: thử selector chuẩn login.microsoftonline.com
                    email_input = page.wait_for_selector(
                        'input[name="loginfmt"]', timeout=5000
                    )
                email_input.fill(email)
                time.sleep(1)

                # Click Next — tìm nút submit trên trang remoteconnect
                next_btn = page.query_selector('#idSIButton9')
                if not next_btn:
                    next_btn = page.query_selector('input[type="submit"]')
                if not next_btn:
                    next_btn = page.query_selector('button[type="submit"]')
                if next_btn:
                    next_btn.click()
                else:
                    page.keyboard.press("Enter")
                time.sleep(4)

                # ═══ BƯỚC 4: Nhập Password ═══
                # Sau email, trang có thể chuyển sang login.live.com hoặc
                # login.microsoftonline.com với input[name="passwd"]
                _log("🔒 Chờ ô mật khẩu...")

                # Thử tìm password input trên main page VÀ trong iframes
                pw_input = None
                for attempt in range(15):  # Max 30s
                    # Tìm trên main page
                    pw_input = page.query_selector('input[name="passwd"]')
                    if not pw_input:
                        pw_input = page.query_selector('input#passwordEntry')
                    if not pw_input:
                        pw_input = page.query_selector('input[type="password"]')
                    if pw_input and pw_input.is_visible():
                        break
                    time.sleep(2)

                if not pw_input:
                    browser_error = "Không tìm thấy ô mật khẩu sau 30s"
                    _log(f"⚠️ {browser_error}")
                else:
                    _log("🔒 Nhập mật khẩu...")
                    pw_input.fill(password)
                    time.sleep(1)

                    # Click Sign in
                    sign_btn = page.query_selector('#idSIButton9')
                    if sign_btn:
                        sign_btn.click()
                    else:
                        page.keyboard.press("Enter")
                    time.sleep(4)

                    # ═══ BƯỚC 5: Kiểm tra lỗi đăng nhập ═══
                    try:
                        for err_sel in ['#usernameError', '#passwordError',
                                        '#idTd_Tile_ErrorMsg_Login', '#passwordError']:
                            error_el = page.query_selector(err_sel)
                            if error_el and error_el.is_visible():
                                err_text = error_el.inner_text().strip()
                                if err_text:
                                    browser_error = f"Microsoft: {err_text[:120]}"
                                    _log(f"❌ {browser_error}")
                                    break
                    except Exception:
                        pass

                # ═══ BƯỚC 5.5: "Bảo vệ tài khoản" → Skip tất cả ═══
                # Microsoft có thể hiện nhiều trang security liên tiếp
                # (thêm email thay thế, xác nhận skip, v.v.)
                if not browser_error:
                    for skip_round in range(3):  # Tối đa 3 trang skip
                        try:
                            time.sleep(2)
                            current_url = page.url.lower()

                            # Kiểm tra có phải trang security/proofs không
                            is_security_page = any(kw in current_url for kw in [
                                'proofs', 'protect', 'security', 'account.live.com'
                            ])

                            if not is_security_page:
                                break  # Không phải trang security → tiếp tục flow

                            _log(f"🛡️ Trang bảo vệ tài khoản (lần {skip_round+1}) — skip...")
                            skip_clicked = False

                            # Cách 1: Tìm link/nút "Bỏ qua" bằng text
                            for skip_text in [
                                "Bỏ qua bây giờ",
                                "Skip for now",
                                "Bỏ qua",
                                "Tôi không muốn",
                                "I don't want",
                                "Cancel",
                                "Hủy",
                                "skip",
                            ]:
                                try:
                                    el = page.get_by_text(skip_text, exact=False).first
                                    if el.is_visible():
                                        el.click()
                                        skip_clicked = True
                                        _log(f"⏭️ Đã skip: '{skip_text}'")
                                        time.sleep(3)
                                        break
                                except Exception:
                                    continue

                            # Cách 2: Tìm bằng selector phổ biến
                            if not skip_clicked:
                                for sel in ['a#iCancel', '#iShowSkip', 'a#skipLink',
                                            'a.skip-link', '#CancelLinkButton',
                                            'input[value="Cancel"]', '#idBtn_Back']:
                                    try:
                                        el = page.query_selector(sel)
                                        if el and el.is_visible():
                                            el.click()
                                            skip_clicked = True
                                            _log(f"⏭️ Đã skip (selector: {sel})")
                                            time.sleep(3)
                                            break
                                    except Exception:
                                        continue

                            if not skip_clicked:
                                _log("⚠️ Không tìm được nút skip — tiếp tục...")
                                break
                        except Exception:
                            break

                # ═══ BƯỚC 6: "Duy trì đăng nhập?" / "Stay signed in?" → Không ═══
                if not browser_error:
                    try:
                        time.sleep(2)
                        no_clicked = False

                        # Thử selector chuẩn
                        no_btn = page.query_selector('#idBtn_Back')
                        if no_btn and no_btn.is_visible():
                            no_btn.click()
                            no_clicked = True

                        # Thử text: "Không" (VN) hoặc "No" (EN)
                        if not no_clicked:
                            for btn_text in ["Không", "No"]:
                                try:
                                    page.get_by_role("button", name=btn_text).click(timeout=3000)
                                    no_clicked = True
                                    break
                                except Exception:
                                    continue

                        if no_clicked:
                            _log("🔘 'Duy trì đăng nhập?' → Không")
                            time.sleep(3)
                    except Exception:
                        pass

                # ═══ BƯỚC 7: Consent / "Chấp nhận" quyền truy cập ═══
                if not browser_error:
                    try:
                        time.sleep(2)
                        accept_clicked = False

                        # Kiểm tra URL consent
                        if 'consent' in page.url.lower():
                            _log("📋 Trang xác nhận quyền — đang chấp nhận...")

                        # Thử selector chuẩn
                        accept_btn = page.query_selector('#idBtn_Accept')
                        if accept_btn and accept_btn.is_visible():
                            accept_btn.click()
                            accept_clicked = True

                        # Thử text: "Chấp nhận" (VN), "Accept" (EN), "Yes", "Có"
                        if not accept_clicked:
                            for btn_text in ["Chấp nhận", "Accept", "Yes", "Có"]:
                                try:
                                    page.get_by_role("button", name=btn_text).click(timeout=3000)
                                    accept_clicked = True
                                    break
                                except Exception:
                                    continue

                        if accept_clicked:
                            _log("✅ Đã chấp nhận quyền truy cập")
                            time.sleep(3)
                    except Exception:
                        pass

                # ═══ BƯỚC 8: Chờ MSAL polling hoàn tất ═══
                if not browser_error:
                    _log("⏳ Chờ Microsoft xác nhận...")
                    for _ in range(25):
                        if "_pending" not in token_result:
                            break
                        time.sleep(1)

            except Exception as e:
                browser_error = f"Browser lỗi: {str(e)[:150]}"
                _log(f"⚠️ {browser_error}")

                # Nếu lỗi có thể do CAPTCHA → chờ user giải thủ công
                if "timeout" in str(e).lower():
                    _log("⏳ Có thể CAPTCHA — chờ bạn xử lý trên trình duyệt (60s)...")
                    for _ in range(60):
                        if "_pending" not in token_result:
                            browser_error = ""  # User đã giải → reset error
                            break
                        time.sleep(1)

            finally:
                # Đóng browser
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass

    except Exception as e:
        browser_error = f"Playwright lỗi: {str(e)[:150]}"
        _log(f"❌ {browser_error}")

    # ── Chờ MSAL polling hoàn tất ──
    poll_thread.join(timeout=15)

    if "access_token" in token_result:
        new_rt = token_result.get("refresh_token", "")
        _log("✅ Auto-Renew THÀNH CÔNG!")
        return {
            "access_token": token_result["access_token"],
            "refresh_token": new_rt,
            "error": ""
        }
    else:
        error = token_result.get("error_description",
                token_result.get("error", browser_error or "Không lấy được token"))
        return {
            "access_token": "", "refresh_token": "",
            "error": f"Auto-Renew thất bại: {str(error)[:200]}"
        }


# ═══════════════════════════════════════════════════════════
# IMAP — TÌM MAIL & TRÍCH MÃ OTP
# ═══════════════════════════════════════════════════════════
def _extract_otp_from_text(text: str) -> str:
    """Trích mã OTP 6 số từ nội dung mail.

    Ưu tiên:
      1. Mã ngay sau keyword (verification code, mã xác minh, ...)
      2. Mã 6 số đứng riêng (word boundary)
      3. Bỏ qua nếu là số quá dài (>6 số liền = không phải OTP)
    """
    if not text:
        return ""

    # 1. Tìm mã ngay sau keyword xác minh (chính xác nhất)
    keyword_patterns = [
        r'(?:verification\s*code|verify\s*code|mã\s*xác\s*(?:minh|nhận)|'
        r'security\s*code|your\s*code\s*is|code\s*is)[:\s]*(\d{6})\b',
    ]
    for pattern in keyword_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)

    # 2. Tìm mã 6 số đứng riêng (phải CHÍNH XÁC 6 số, không nằm trong số dài hơn)
    matches = re.findall(r'(?<!\d)(\d{6})(?!\d)', text)
    if matches:
        return matches[0]

    return ""


def fetch_otp_from_email(email: str, password: str = "",
                          refresh_token: str = "", client_id: str = "",
                          keyword: str = "tiktok",
                          max_retries: int = 3, wait_seconds: int = 5,
                          auto_renew: bool = True,
                          progress_callback=None) -> dict:
    """Hàm chính: Lấy mã OTP từ email.

    Xử lý đầy đủ tất cả trường hợp:
      - Validate đầu vào
      - Phân loại provider (Microsoft/Gmail/Khác)
      - Xác thực (OAuth2 / Basic Auth)
      - Kết nối IMAP + Tìm mail + Trích mã
      - Retry nếu mail chưa tới

    Args:
        email:            Địa chỉ email
        password:         Mật khẩu email (hoặc App Password cho Gmail)
        refresh_token:    OAuth2 refresh token (bắt buộc cho Hotmail nếu bật MFA)
        client_id:        Azure App Client ID (để trống = dùng default)
        keyword:          Từ khóa lọc mail (mặc định: "tiktok")
        max_retries:      Số lần retry nếu chưa thấy mail
        wait_seconds:     Giây chờ giữa mỗi lần retry
        progress_callback: Hàm callback(str) để cập nhật trạng thái UI

    Returns:
        dict: {"otp", "new_refresh_token", "status", "message"}
    """
    def _log(msg):
        if progress_callback:
            progress_callback(msg)

    # ═══════════════════════════════════════════
    # GIAI ĐOẠN 1: VALIDATE ĐẦU VÀO
    # ═══════════════════════════════════════════
    email = (email or "").strip()
    password = (password or "").strip()
    refresh_token = (refresh_token or "").strip()
    client_id = (client_id or "").strip()
    keyword = (keyword or "").strip()

    if not email:
        return _make_result(status="error", message="Email để trống")

    if not validate_email_format(email):
        return _make_result(status="error", message=f"Email không hợp lệ: {email}")

    if not password and not refresh_token:
        return _make_result(
            status="error",
            message="Cần ít nhất mật khẩu hoặc Refresh Token"
        )

    # ═══════════════════════════════════════════
    # GIAI ĐOẠN 2: PHÂN LOẠI & XÁC THỰC
    # ═══════════════════════════════════════════
    provider = detect_email_provider(email)
    imap_server = get_imap_server(email)
    access_token = None
    new_rt = ""

    _log(f"📧 Provider: {provider.upper()} | IMAP: {imap_server}")

    if provider == 'microsoft':
        # ── MICROSOFT: OAuth2 bắt buộc ──
        _log("🔐 Microsoft — Đang xác thực OAuth2...")
        oauth_result = get_microsoft_oauth_token(
            email=email,
            password=password,
            refresh_token=refresh_token,
            client_id=client_id,
            progress_callback=progress_callback
        )

        if not oauth_result.get("access_token"):
            # ── OAuth thất bại → Thử Auto-Renew nếu có password ──
            if auto_renew and password:
                _log("🔄 OAuth thất bại — thử Auto-Renew Refresh Token...")
                renew_result = auto_renew_refresh_token(
                    email=email,
                    password=password,
                    client_id=client_id,
                    progress_callback=progress_callback
                )
                if renew_result.get("access_token"):
                    oauth_result = renew_result
                    _log("✅ Auto-Renew thành công! Tiếp tục lấy OTP...")
                else:
                    renew_err = renew_result.get("error", "")
                    orig_err = oauth_result.get("error", "")
                    return _make_result(
                        status="error",
                        message=f"OAuth2 lỗi: {orig_err} | Auto-Renew: {renew_err}"
                    )
            else:
                error = oauth_result.get("error", "Không lấy được token")
                return _make_result(status="error", message=f"OAuth2 lỗi: {error}")

        access_token = oauth_result["access_token"]
        new_rt = oauth_result.get("refresh_token", "")
        _log("✅ Xác thực Microsoft thành công!")

    elif provider == 'gmail':
        _log("🔐 Gmail — Sẽ dùng Basic Auth (App Password)")
        if not password:
            return _make_result(
                status="error",
                message="Gmail cần App Password (16 ký tự) — không hỗ trợ mật khẩu thường"
            )
    else:
        _log(f"🔐 Provider khác ({email.split('@')[1]}) — Basic Auth")
        if not password:
            return _make_result(status="error", message="Cần mật khẩu để đăng nhập IMAP")

    # ═══════════════════════════════════════════
    # GIAI ĐOẠN 3: KẾT NỐI IMAP & TÌM MAIL
    # ═══════════════════════════════════════════
    try:
        from imap_tools import MailBox, AND
    except ImportError:
        return _make_result(
            status="error",
            message="Thiếu thư viện imap_tools — chạy: pip install imap-tools"
        )

    last_error = ""

    for attempt in range(max_retries):
        mailbox = None
        try:
            _log(f"📬 Kết nối IMAP (lần {attempt + 1}/{max_retries})...")

            # ── Kết nối & Đăng nhập ──
            mailbox = MailBox(imap_server)

            if provider == 'microsoft':
                # OAuth2 XOAUTH2
                mailbox.xoauth2(email, access_token)
            else:
                # Basic Auth
                mailbox.login(email, password)

            _log("✅ Đăng nhập IMAP thành công! Đang tìm mail...")

            # ── Tìm mail ──
            # Xây dựng filter: từ keyword + chưa đọc
            search_criteria = AND(seen=False)
            if keyword:
                search_criteria = AND(from_=keyword, seen=False)

            found_otp = ""
            mail_found = False
            mail_date = None

            for msg in mailbox.fetch(search_criteria, reverse=True, limit=5):
                mail_found = True
                body = (msg.text or '') + ' ' + (msg.html or '')
                subject = msg.subject or ''
                full_text = subject + ' ' + body
                mail_date = msg.date

                otp = _extract_otp_from_text(full_text)
                if otp:
                    # Đánh dấu đã đọc
                    try:
                        mailbox.flag(msg.uid, '\\Seen', True)
                    except Exception:
                        pass  # Không critical

                    found_otp = otp

                    # Kiểm tra mail có quá cũ không (>10 phút)
                    warn = ""
                    if mail_date:
                        try:
                            from datetime import datetime, timezone
                            now = datetime.now(timezone.utc)
                            mail_dt = mail_date if mail_date.tzinfo else mail_date.replace(tzinfo=timezone.utc)
                            age_seconds = (now - mail_dt).total_seconds()
                            if age_seconds > 600:  # > 10 phút
                                age_min = int(age_seconds / 60)
                                warn = f" (⚠️ mail {age_min} phút trước — mã có thể hết hạn)"
                        except Exception:
                            pass

                    try:
                        mailbox.logout()
                    except Exception:
                        pass

                    _log(f"✅ Tìm thấy mã: {found_otp}{warn}")
                    return _make_result(
                        otp=found_otp,
                        new_refresh_token=new_rt,
                        status="success",
                        message=f"Mã OTP: {found_otp}{warn}"
                    )

            # Có mail nhưng không chứa mã 6 số
            if mail_found and not found_otp:
                _log(f"⚠️ Có mail từ '{keyword}' nhưng không chứa mã 6 số")
                last_error = f"Có mail nhưng không chứa mã OTP 6 số"
            else:
                _log(f"📭 Chưa thấy mail từ '{keyword}'")
                last_error = f"Không tìm thấy mail từ '{keyword}'"

            try:
                mailbox.logout()
            except Exception:
                pass

        except Exception as e:
            err_str = str(e)
            _log(f"⚠️ IMAP lần {attempt + 1}: {err_str[:80]}")

            # Phân loại lỗi IMAP
            err_lower = err_str.lower()
            if 'authentication' in err_lower or 'login' in err_lower or 'credentials' in err_lower:
                # Lỗi xác thực → không cần retry
                try:
                    if mailbox: mailbox.logout()
                except Exception:
                    pass
                return _make_result(
                    status="error",
                    new_refresh_token=new_rt,
                    message=f"Lỗi đăng nhập IMAP: Sai mật khẩu hoặc tài khoản bị chặn"
                )
            elif 'ssl' in err_lower or 'tls' in err_lower or 'certificate' in err_lower:
                last_error = "Lỗi bảo mật SSL/TLS — kiểm tra antivirus/firewall"
            elif 'timeout' in err_lower or 'timed out' in err_lower:
                last_error = "Timeout kết nối IMAP — mạng chậm hoặc server quá tải"
            elif 'connection refused' in err_lower or 'errno 111' in err_lower:
                last_error = "Kết nối bị từ chối — port 993 có thể bị firewall chặn"
            elif 'name resolution' in err_lower or 'getaddrinfo' in err_lower:
                last_error = f"Không phân giải được server: {imap_server}"
            else:
                last_error = err_str[:100]

            if mailbox:
                try:
                    mailbox.logout()
                except Exception:
                    pass

        # Chờ trước khi retry (trừ lần cuối)
        if attempt < max_retries - 1:
            _log(f"⏳ Chờ {wait_seconds}s trước lần thử tiếp...")
            time.sleep(wait_seconds)

    # ═══════════════════════════════════════════
    # HẾT SỐ RETRY
    # ═══════════════════════════════════════════
    return _make_result(
        status="no_mail",
        new_refresh_token=new_rt,
        message=last_error or "Không tìm thấy mail OTP sau nhiều lần thử"
    )


# ═══════════════════════════════════════════════════════════
# TEST ĐỘC LẬP
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Ví dụ test nhanh
    result = fetch_otp_from_email(
        email="test@hotmail.com",
        password="mypassword",
        refresh_token="",
        keyword="tiktok",
        progress_callback=print
    )
    print(f"\nKết quả: {result}")
