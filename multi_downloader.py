import os
import requests
import yt_dlp
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

class MultiDownloader:
    def __init__(self):
        # Header giả lập để tránh bị chặn
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def download_video(self, url, output_path):
        """
        Tải video từ YouTube, Facebook, Instagram... sử dụng yt-dlp.
        Định dạng tên: [Platform Name]_[Video ID].mp4
        """
        try:
            if not os.path.exists(output_path):
                os.makedirs(output_path)

            ydl_opts = {
                'format': 'bestvideo+bestaudio/best',
                'outtmpl': os.path.join(output_path, '%(extractor_key)s_%(id)s.%(ext)s'),
                'merge_output_format': 'mp4',
                'quiet': True,
                'no_warnings': True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                # Đảm bảo đuôi mở rộng là mp4 nếu merge thành công
                filename = filename.rsplit('.', 1)[0] + '.mp4'
                return filename
        except Exception as e:
            return f"Lỗi: {str(e)}"

    def download_tiktok_nowatermark(self, url, output_path):
        """
        Tải video TikTok/Douyin không dính logo qua API TikWM.
        """
        try:
            if not os.path.exists(output_path):
                os.makedirs(output_path)

            api_url = "https://www.tikwm.com/api/"
            params = {"url": url, "hd": 1}
            
            response = requests.get(api_url, params=params, headers=self.headers, timeout=15)
            data = response.json()

            if data.get("code") == 0:
                video_url = data["data"]["play"]
                video_id = data["data"]["id"]
                platform = "TikTok" if "tiktok.com" in url else "Douyin"
                
                file_name = f"{platform}_{video_id}.mp4"
                full_path = os.path.join(output_path, file_name)

                video_data = requests.get(video_url, stream=True, timeout=30)
                with open(full_path, "wb") as f:
                    for chunk in video_data.iter_content(chunk_size=1024*1024):
                        if chunk:
                            f.write(chunk)
                
                return full_path
            else:
                return False
        except Exception as e:
            print(f"Lỗi TikTok: {e}")
            return False

    def scan_local_videos(self, folder_path):
        """
        Quét thư mục và trả về danh sách đường dẫn tuyệt đối của các file video.
        """
        video_extensions = {'.mp4', '.mov', '.avi'}
        video_list = []
        
        try:
            path = Path(folder_path)
            if not path.exists():
                return []

            for file in path.iterdir():
                # Bỏ qua file ẩn (bắt đầu bằng dấu chấm) và kiểm tra định dạng
                if file.is_file() and not file.name.startswith('.') and file.suffix.lower() in video_extensions:
                    video_list.append(str(file.absolute()))
        except Exception as e:
            print(f"Lỗi khi quét thư mục: {e}")
            
        return video_list

    def process_download_list(self, link_list, output_folder):
        """
        Xử lý tải hàng loạt sử dụng ThreadPoolExecutor.
        """
        total = len(link_list)
        print(f"Bắt đầu xử lý {total} link...")

        results = []
        # Tải đồng thời 3-5 video (mặc định 5)
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_url = {}
            
            for index, url in enumerate(link_list, 1):
                # Quyết định dùng hàm nào dựa trên domain
                if "tiktok.com" in url or "douyin.com" in url:
                    future = executor.submit(self.download_tiktok_nowatermark, url, output_folder)
                else:
                    future = executor.submit(self.download_video, url, output_folder)
                
                future_to_url[future] = (index, url)

            for future in as_completed(future_to_url):
                index, url = future_to_url[future]
                try:
                    result = future.result()
                    if result and not str(result).startswith("Lỗi"):
                        print(f"[{index}/{total}] Thành công: {url}")
                        results.append(result)
                    else:
                        print(f"[{index}/{total}] Thất bại: {url} - {result}")
                except Exception as e:
                    print(f"[{index}/{total}] Lỗi hệ thống: {url} - {e}")

        return results

if __name__ == "__main__":
    # Ví dụ sử dụng
    downloader = MultiDownloader()
    
    # link_list = [
    #     "https://www.youtube.com/watch?v=example",
    #     "https://www.tiktok.com/@user/video/123456789"
    # ]
    # downloader.process_download_list(link_list, "./downloads")
    
    print("MultiDownloader đã sẵn sàng.")
