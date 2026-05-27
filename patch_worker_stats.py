import re

filepath = 'd:/auto - backup/cdp_worker.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add feature trigger in _run_cdp_automation
target_feature = '''            if "Tương tác ở Feed" in self.selected_features:'''
replacement_feature = '''            if "Cập nhật thống kê" in self.selected_features:
                any_feature_ran = True
                self.status_update.emit("📊 Bắt đầu cập nhật thống kê tài khoản...", "blue")
                await self._update_tiktok_stats(cdp)

            if "Tương tác ở Feed" in self.selected_features:'''
content = content.replace(target_feature, replacement_feature)

# 2. Add _update_tiktok_stats method
method_code = '''
    async def _update_tiktok_stats(self, cdp):
        """Quét trang Profile và Studio để cập nhật Follow, Views, Video."""
        try:
            self.status_update.emit("🔄 Đang vào trang cá nhân...", "blue")
            await cdp.navigate("https://www.tiktok.com/profile")
            await asyncio.sleep(4)
            
            # Đợi load xong hoặc redirect xong
            await cdp.evaluate("""
                new Promise(resolve => {
                    if (document.readyState === 'complete') resolve();
                    else window.addEventListener('load', resolve);
                });
            """)
            await asyncio.sleep(2)

            stats = await cdp.evaluate("""
                (() => {
                    let followers = "0";
                    let likes = "0";
                    let following = "0";
                    let videos = "0";
                    
                    try {
                        const f_el = document.querySelector('[data-e2e="followers-stat"]');
                        if (f_el) followers = f_el.textContent.trim();
                        
                        const l_el = document.querySelector('[data-e2e="likes-stat"]');
                        if (l_el) likes = l_el.textContent.trim();
                        
                        const v_els = document.querySelectorAll('[data-e2e="user-post-item"]');
                        videos = v_els.length.toString();
                        if (videos === "0") {
                            // Cố đếm thẻ video nếu giao diện đổi
                            videos = document.querySelectorAll('div[class*="DivItemContainerForProfile"]').length.toString();
                        }
                    } catch(e) {}
                    
                    return {followers, likes, videos};
                })()
            """)
            
            if not stats:
                self.status_update.emit("⚠️ Không lấy được thông số từ trang cá nhân", "orange")
                return

            followers = stats.get('followers', '0')
            likes = stats.get('likes', '0')
            videos = stats.get('videos', '0')
            
            self.status_update.emit(f"✅ Follow: {followers} | Likes: {likes} | Videos: {videos}", "green")
            
            # Gửi signal về UI để update table
            # Ta dùng profile_update_signal hoặc tự update vào profile_data
            update_data = {
                "t_follows": followers,
                "t_views": likes, # Tạm dùng Likes cho cột T.Views vì profile chỉ hiện Likes
                "t_video": videos
            }
            self.profile_data.update(update_data)
            self.profile_update_signal.emit(self.profile_data)
            
        except Exception as e:
            self.status_update.emit(f"⚠️ Lỗi cập nhật thống kê: {str(e)[:50]}", "red")
'''

# Find a good place to insert the method (before `_skip_tiktok_popup` is fine)
target_insert = '''    async def _skip_tiktok_popup(self, cdp):'''
content = content.replace(target_insert, method_code + '\n' + target_insert)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
