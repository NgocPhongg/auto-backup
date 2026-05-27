# Changelog: Dự án SSMATool - TikTok Automation

## Quy ước
- **Chỉ ghi những gì đã thực sự hoàn thành** – tổng hợp những công việc đã được xử lý trong thời gian qua.
- Mỗi entry phân chia rõ ràng theo Phase/mốc chức năng và kết quả cụ thể.

---

## 🚀 Các Phase & Tính năng đã hoàn thành

### ✅ Phase 1: Cấu trúc Dự án & Tối ưu UI Dashboard
- ✅ Xác minh và tổ chức lại cấu trúc thư mục, các dependencies quan trọng (ffmpeg, thư mục profile, v.v.).
- ✅ Tối ưu hóa UI Dashboard: Làm gọn giao diện, loại bỏ border thừa, tối đa hóa không gian hiển thị cho bảng theo dõi tài khoản (tracking table).
- ✅ Tích hợp trình duyệt vào Dashboard (Browser Embedding): Xử lý hiển thị giao diện giống native desktop, quản lý DPI, đồng bộ hóa kích thước khung hình tránh lỗi tràn viền.
- ✅ Quản lý đa luồng (Thread Management): Khắc phục các lỗi kẹt vòng lặp vô hạn trong worker thread, giúp ứng dụng chạy mượt mà và kết thúc tác vụ an toàn.
- ✅ Bổ sung menu "Đăng Ký" trên GUI chính để chuẩn bị cho các quy trình đăng ký tài khoản.

### ✅ Phase 2: Quản lý Trình duyệt & Proxy (AdsPower Integration)
- ✅ Quản lý vòng đời AdsPower: Hỗ trợ Tạo/Xóa/Mở profile hoàn toàn tự động từ giao diện SSMATool qua AdsPower Local API.
- ✅ Cô lập dữ liệu Profile: Đảm bảo mỗi trình duyệt hoạt động độc lập theo Browser ID, không bị xung đột cookie hay cấu hình.
- ✅ Xác thực và đồng bộ Proxy: Tích hợp check proxy live (qua api.ipify.org) khi tạo/cập nhật profile, đồng thời đồng bộ dữ liệu này với cấu hình AdsPower.
- ✅ Bổ sung tính năng "Mở trình duyệt" (Open Browser) thủ công: Cho phép người dùng kiểm tra tài khoản hoặc proxy độc lập mà không kích hoạt chuỗi tự động hóa.

### ✅ Phase 3: Tự động hóa Đăng nhập & Xác thực OTP (OAuth2 + IMAP)
- ✅ Xây dựng luồng Đăng nhập TikTok tự động "One-Click" mạnh mẽ sử dụng Playwright (thay thế hệ thống cũ).
- ✅ Phát hiện và xử lý Anti-bot: Nhận diện chính xác popup 2FA, thông báo lỗi chữ đỏ bằng cách kiểm tra các phần tử hiển thị trực quan (`is_visible`), giúp bot không bị mắc kẹt.
- ✅ Tích hợp IMAP OTP: Trích xuất mã xác nhận từ email (tách biệt mật khẩu TikTok và mật khẩu email) qua các từ khóa thông minh.
- ✅ Hệ thống OAuth2 Token Renewal cho Hotmail/Outlook: Tự động chạy Playwright headless để vượt qua các trang bảo mật của Microsoft ("Account Protection", "Stay signed in", "Consent").
- ✅ Tự động lưu và làm mới (renew) Refresh Token xuống database JSON cục bộ để đảm bảo đăng nhập lâu dài không bị đứt quãng.
- ✅ Tự động trích xuất Cookie, Username sau khi login thành công và cập nhật trạng thái "Logged" lên bảng điều khiển.

### ✅ Phase 4: Tự động hóa Tương tác tự nhiên (Human-like Automation)
- ✅ Mô phỏng con trỏ chuột (Cursor Interaction): Xây dựng hệ thống render con trỏ mượt mà thông qua cơ chế tuần tự, giải quyết lỗi bất đồng bộ JS-to-Python.
- ✅ Tương tác xem Feed: Thực hiện các hành vi cuộn (scroll) ngẫu nhiên, hover, và mở bảng bình luận với logic giống thói quen người dùng thực.
- ✅ Tối ưu thời gian và định dạng video: Viết logic xem video thời lượng dài (20-40 giây) và khắc phục thành công lỗi kẹt (infinite watch bug) khi gặp các bài đăng dạng slideshow/hình ảnh.
- ✅ Hoàn thiện kịch bản tương tác: Loại bỏ các thao tác cuộn thừa và xác minh kỹ trạng thái trước khi mở bình luận, tăng độ mượt mà và ổn định cho luồng chạy.

### ✅ Phase 5: Tương tác theo Từ khóa (Keyword Search & Interact)
- ✅ Viết lại hoàn toàn `_do_keyword_interaction()` với luồng 4 giai đoạn human-like.
- ✅ **GĐ1**: Navigate TikTok → Tìm ô Search Box (multi-selector fallback) → Gõ từng ký tự keyword (delay 100-300ms) → Bấm Enter.
- ✅ **GĐ2**: Click video đầu tiên trong kết quả → Chờ Theater Mode (nền đen) mở ổn định.
- ✅ **GĐ3**: Vòng lặp nuôi nick — Xem video (detect duration + mouse drift) → Like/Fav/Comment theo tỉ lệ % → ArrowDown chuyển video (không dùng mouse wheel).
- ✅ **GĐ4**: Escape thoát Theater Mode → Nghỉ giữa từ khóa → Tiếp tục từ khóa kế.
- ✅ Tạo `KeywordSettingsDialog` — Giao diện cài đặt từ khóa (nhập multi-line, số video min/max).
- ✅ Kết nối nút "Cài đặt" bên cạnh checkbox "Tương tác theo từ khóa(key)" với dialog mới.
- ✅ Cập nhật `feed_settings.json` — thêm fields `keywords`, `keyword_min_videos`, `keyword_max_videos`.
- ✅ Tái sử dụng 100% các helper có sẵn: `_watch_current_video()`, `_interact_current_video()`, `_human_move_and_click()`.

### ✅ Phase 6: Code Audit & Sửa lỗi Proxy Sync (12/05/2026)
- ✅ **Kiểm tra toàn bộ codebase** — Audit 6 file chính, phát hiện 10 vấn đề.
- ✅ **FIX: Proxy "dính" dù đã xóa trên bảng** — 3 nguyên nhân gốc:
  - `save_accounts_to_db()` — cell proxy rỗng không sync → JSON giữ proxy cũ.
  - `open_dashboard_selected()` — thiếu `table_to_profile` sync → UserRole giữ proxy cũ.
  - GoLogin API — proxy trống không gọi `changeProfileProxy` → server giữ proxy cũ.
- ✅ **FIX: Explore feed timer** — Đổi từ accumulated video time → `time.time()` real wall-clock. Session explore giờ kết thúc đúng thời gian target.
- ✅ **FIX: Khôi phục `_clone_comment`** — Rate limit handling + verify block bị mất do lỗi edit, đã phục hồi hoàn toàn.
- ✅ **FIX: Khôi phục `_get_comment_input_position`** — Cách 2 (tìm ô comment bằng placeholder) bị xóa, đã phục hồi.
- ✅ **FIX: GoLogin proxy clear** — Thêm `changeProfileProxy(mode="none")` khi user xóa proxy, đảm bảo server GoLogin cũng được cập nhật.
- ✅ Tất cả file pass `py_compile` syntax check.

#### Backlog còn lại (ưu tiên thấp)
- 🟡 GoLogin worker (`gologin_worker.py`) — Legacy, thiếu cookie persistence, anti-detection, human-like navigation. Khuyến nghị deprecate.
- 🟡 CDPClient `get_event_loop()` → `get_running_loop()` (Python 3.10+ deprecation warning).
- 🟡 Encrypt sensitive data trong `accounts_data.json` (password, refresh_token plaintext).

### 📝 Ghi chú
- Các module cốt lõi bao gồm: **Bypass bảo mật, tích hợp OAuth2 email, điều khiển trình duyệt AdsPower và các kịch bản tương tác giống người thật** hiện đã hoạt động rất ổn định.
- Hệ thống đã sẵn sàng để vận hành với số lượng lớn profile.
