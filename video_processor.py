import subprocess
import os

def bypass_audio_copyright(input_video, output_video):
    """
    Lách bản quyền âm thanh bằng cách thay đổi tốc độ, pitch và EQ.
    Sử dụng FFmpeg qua subprocess.
    """
    if not os.path.exists(input_video):
        print(f"Lỗi: Không tìm thấy file đầu vào {input_video}")
        return False

    # Câu lệnh FFmpeg:
    # 1. atempo=1.05: Tăng tốc độ 1.05 lần
    # 2. asetrate=44100*1.03,aresample=44100: Đẩy cao độ (pitch) lên 3%
    # 3. firequalizer: Tăng nhẹ tần số thấp (Bass)
    # 4. volume=0.98: Giảm âm lượng cực nhỏ để tránh peak
    
    audio_filter = (
        "atempo=1.05,"
        "asetrate=44100*1.03,aresample=44100,"
        "firequalizer=gain='if(lt(f,200),2,0)',"
        "volume=0.98"
    )

    command = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-i', input_video,
        '-c:v', 'copy', # Giữ nguyên chất lượng hình ảnh
        '-af', audio_filter,
        '-c:a', 'aac', '-b:a', '192k', # Encode lại âm thanh
        output_video
    ]

    try:
        print(f"Đang xử lý lách bản quyền: {input_video}...")
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        print(f"Thành công! Video đã lưu tại: {output_video}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Lỗi FFmpeg: {e.stderr}")
        return False
    except Exception as e:
        print(f"Lỗi hệ thống: {e}")
        return False

if __name__ == "__main__":
    # bypass_audio_copyright("original.mp4", "bypassed.mp4")
    print("Hàm bypass_audio_copyright đã sẵn sàng.")
