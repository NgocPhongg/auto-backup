import os
import math
import random
import hashlib
import tempfile
import subprocess
import re
import time
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

def _get_ffmpeg_exe():
    """Get the ffmpeg binary path from imageio_ffmpeg."""
    from imageio_ffmpeg import get_ffmpeg_exe
    return get_ffmpeg_exe()

def _probe_video(input_path):
    """Probe video file for duration, resolution, fps, and codec using ffmpeg -i."""
    ffmpeg = _get_ffmpeg_exe()
    cmd = [
        ffmpeg, '-i', input_path,
        '-hide_banner'
    ]
    try:
        kwargs = {}
        if os.name == 'nt':
            kwargs['creationflags'] = 0x08000000
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace', **kwargs)
        stderr = result.stderr
        
        # Parse duration: "Duration: HH:MM:SS.ms"
        duration = 0.0
        dur_match = re.search(r'Duration:\s*(\d+):(\d+):(\d+)\.(\d+)', stderr)
        if dur_match:
            hh, mm, ss, ms = dur_match.groups()
            duration = int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 100.0
        
        # Parse video stream: "Stream #0:0... Video: h264 ..., 1920x1080 ..., 30 fps"
        width, height, fps, codec = 1920, 1080, 30.0, 'h264'
        has_audio = 'Audio:' in stderr
        
        video_match = re.search(r'Stream.*Video:\s*(\w+).*?,\s*(\d+)x(\d+)', stderr)
        if video_match:
            codec = video_match.group(1)
            width = int(video_match.group(2))
            height = int(video_match.group(3))
        
        fps_match = re.search(r'(\d+(?:\.\d+)?)\s*fps', stderr)
        if fps_match:
            fps = float(fps_match.group(1))
        
        return {
            'duration': duration,
            'width': width,
            'height': height,
            'fps': fps,
            'codec': codec,
            'has_audio': has_audio
        }
    except Exception as e:
        print(f"Probe failed: {e}")
        return {
            'duration': 0,
            'width': 1920,
            'height': 1080,
            'fps': 30.0,
            'codec': 'h264',
            'has_audio': True
        }
def get_timing_plan(input_path, settings, info=None):
    """Return the same trim/speed timing used by the FFmpeg render path."""
    if info is None:
        info = _probe_video(input_path)

    orig_duration = float(info.get('duration') or 0.0)
    max_source = settings.get('max_source', False)
    im_res = settings.get('im_res', False)
    keep_hook = settings.get('keep_hook', True)

    trim_start = 0.0
    trim_end = orig_duration
    if not max_source and im_res and not keep_hook and orig_duration > 10:
        trim_start = orig_duration * 0.25

    use_duration = settings.get('use_duration', False)
    duration_setting = float(settings.get('duration', 0) or 0)
    trimmed_duration = max(0.0, trim_end - trim_start)
    if not max_source and use_duration and duration_setting > 0 and trimmed_duration > duration_setting:
        trim_end = trim_start + duration_setting
        trimmed_duration = duration_setting

    final_speed = 1.0
    if settings.get('use_video_speed', False):
        final_speed *= settings.get('video_speed', 1.0)
    if settings.get('micro_speed', True):
        h = hashlib.md5(os.path.basename(input_path).encode()).hexdigest()
        local_seed = int(h, 16)
        local_rng = random.Random(local_seed)
        final_speed *= local_rng.uniform(1.03, 1.07)

    if final_speed <= 0:
        final_speed = 1.0

    return {
        'trim_start': trim_start,
        'trim_end': trim_end,
        'trimmed_duration': trimmed_duration,
        'final_duration': trimmed_duration / final_speed if final_speed else trimmed_duration,
        'final_speed': final_speed,
        'orig_duration': orig_duration,
    }


def get_render_plan(input_path, settings):
    """Estimate final duration and speed for queue planning (lightweight, no heavy processing)."""
    try:
        return get_timing_plan(input_path, settings)
    except Exception:
        return {'final_duration': 0, 'final_speed': 1.0}


def _render_text_overlay(settings, w, h, mirror_video):
    """Pre-render the text watermark to a temporary RGBA PNG file.
    Returns (temp_png_path, x_pos, y_pos) or (None, 0, 0) if no watermark."""
    
    use_watermark = settings.get('use_watermark', False)
    watermark_text = settings.get('watermark_text', '').replace('\\n', '\n')
    watermark_size = settings.get('watermark_size', 0.08)
    wm_color_hex = settings.get('watermark_color', '#ffff00').lstrip('#')
    try:
        wm_color_rgb = tuple(int(wm_color_hex[i:i+2], 16) for i in (0, 2, 4)) + (255,)
    except Exception:
        wm_color_rgb = (255, 255, 0, 255)
    
    if not use_watermark or not watermark_text.strip():
        return None, 0, 0

    # Measure and render text
    temp_img = Image.new('RGB', (1, 1))
    draw = ImageDraw.Draw(temp_img)

    try:
        font_size = int(h * watermark_size)
        has_cjk = any('\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7af' or '\uff00' <= c <= '\uffef' for c in watermark_text)
        fonts_to_try = ["meiryo.ttc", "msgothic.ttc", "msyh.ttc", "malgun.ttf", "arialbd.ttf", "arial.ttf"] if has_cjk else ["arialbd.ttf", "arial.ttf"]
        
        font = None
        for fn in fonts_to_try:
            try:
                font = ImageFont.truetype(fn, font_size)
                break
            except IOError:
                continue
        if font is None:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    stroke_width = max(1, int(h * watermark_size * 0.05))

    try:
        max_chars = max(10, int(w / (h * watermark_size * 0.55)))
        wrapped_lines = []
        for line in watermark_text.split('\n'):
            if not line.strip():
                wrapped_lines.append("")
                continue
            wrapped = _wrap_subtitle_text(line, max_chars)
            wrapped_lines.extend(wrapped.split('\n'))
    except Exception:
        import textwrap
        max_chars = max(10, int(w / (h * watermark_size * 0.55)))
        wrapped_lines = []
        for line in watermark_text.split('\n'):
            wrapped = textwrap.wrap(line, width=max_chars)
            if not wrapped:
                wrapped_lines.append("")
            else:
                wrapped_lines.extend(wrapped)

    text_w = 0
    text_h = 0
    line_heights = []
    line_widths = []

    for line in wrapped_lines:
        if not line:
            line_widths.append(0)
            line_heights.append(int(h * watermark_size))
            text_h += int(h * watermark_size)
            continue
        if hasattr(draw, 'textbbox'):
            bbox = draw.textbbox((0, 0), line, font=font)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1]
        else:
            lw, lh = draw.textsize(line, font=font)
        line_widths.append(lw)
        line_heights.append(lh)
        text_w = max(text_w, lw)
        text_h += lh

    line_spacing = int(h * watermark_size * 0.2)
    text_h += max(0, len(wrapped_lines) - 1) * line_spacing
    text_h += stroke_width * 2

    box_w = int(text_w + stroke_width * 4)
    box_h = int(text_h + stroke_width * 4)
    
    if box_w <= 0 or box_h <= 0:
        return None, 0, 0

    img_pil = Image.new('RGBA', (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img_pil)

    current_y = stroke_width * 2
    for i, line in enumerate(wrapped_lines):
        if not line:
            current_y += line_heights[i] + line_spacing
            continue
        x_pos = (box_w - line_widths[i]) // 2
        draw.text((x_pos, current_y), line, font=font, fill=wm_color_rgb,
                  stroke_width=stroke_width, stroke_fill=(0, 0, 0, 255))
        current_y += line_heights[i] + line_spacing

    # Calculate position — no need for mirror compensation here since
    # FFmpeg overlay is applied AFTER hflip, so coordinates are in output space
    text_x_norm = settings.get('text_x_norm', 0.5)
    text_y_norm = settings.get('text_y_norm', 0.9)
    overlay_x = int(w * text_x_norm) - box_w // 2
    overlay_y = int(h * text_y_norm) - box_h // 2
    overlay_x = max(0, min(overlay_x, w - box_w))
    overlay_y = max(0, min(overlay_y, h - box_h))

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False, prefix='wm_')
    img_pil.save(tmp.name)
    tmp.close()
    
    return tmp.name, overlay_x, overlay_y


def _render_logo_overlay(settings, w, h, mirror_video):
    """Pre-render the logo to a temporary RGBA PNG at target scale/opacity.
    Returns (temp_png_path, x_pos, y_pos) or (None, 0, 0) if no logo."""
    
    use_logo = settings.get('use_logo', False)
    logo_path = settings.get('logo_path', "")
    
    if not use_logo or not logo_path or not os.path.exists(logo_path):
        return None, 0, 0

    try:
        logo_img = Image.open(logo_path).convert("RGBA")
        logo_scale = settings.get('logo_scale', 0.2)

        target_w = max(10, int(w * logo_scale))
        aspect_ratio = logo_img.height / logo_img.width
        target_h = int(target_w * aspect_ratio)
        logo_img = logo_img.resize((target_w, target_h), Image.Resampling.LANCZOS)

        # Apply opacity to alpha channel
        logo_opacity = settings.get('logo_opacity', 0.8)
        logo_rgba = np.array(logo_img)
        logo_rgba[:, :, 3] = (logo_rgba[:, :, 3] * logo_opacity).astype(np.uint8)
        logo_img = Image.fromarray(logo_rgba)

        # Calculate position (in output coordinate space, AFTER mirror)
        logo_pos = settings.get('logo_pos', 'Bottom-Right')
        pad_x = int(w * 0.05)
        pad_y = int(h * 0.05)

        if logo_pos == 'Manual (Drag)':
            lx_n = settings.get('logo_x_norm', 0.85)
            ly_n = settings.get('logo_y_norm', 0.85)
            logo_x = int(w * lx_n) - target_w // 2
            logo_y = int(h * ly_n) - target_h // 2
        elif logo_pos == 'Top-Left':
            logo_x, logo_y = pad_x, pad_y
        elif logo_pos == 'Top-Right':
            logo_x, logo_y = w - target_w - pad_x, pad_y
        elif logo_pos == 'Bottom-Left':
            logo_x, logo_y = pad_x, h - target_h - pad_y
        elif logo_pos == 'Bottom-Right':
            logo_x, logo_y = w - target_w - pad_x, h - target_h - pad_y
        else:  # Center
            logo_x, logo_y = (w - target_w) // 2, (h - target_h) // 2

        logo_x = max(0, min(logo_x, w - target_w))
        logo_y = max(0, min(logo_y, h - target_h))

        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False, prefix='logo_')
        logo_img.save(tmp.name)
        tmp.close()

        return tmp.name, logo_x, logo_y
    except Exception as e:
        print(f"Error pre-rendering logo: {e}")
        return None, 0, 0


def _build_scale_crop_expr(settings, w, h):
    """Build FFmpeg crop expression for animated scale.
    Returns a crop filter string or None if no scale effect is active."""
    
    use_ai_ratio = settings.get('use_ai_ratio', False)
    ai_ratio = settings.get('ai_ratio', 100)
    ai_scale_base = (ai_ratio / 100.0) if use_ai_ratio else 1.0
    
    use_scale_zoom_ratio = settings.get('use_scale_zoom_ratio', False)
    scale_zoom_ratio = settings.get('scale_zoom_ratio', 120) if use_scale_zoom_ratio else ai_ratio
    ai_scale_zoom = scale_zoom_ratio / 100.0
    
    scale_normal = settings.get('scale_normal', 0) if settings.get('use_scale_normal', False) else 0
    scale_zoom_hold = settings.get('scale_zoom_hold', 0) if settings.get('use_scale_zoom', False) else 0
    scale_easing = settings.get('scale_easing', 100) / 100.0
    scale_trans = scale_easing * 1.0
    scale_cycle = scale_zoom_hold + scale_trans + scale_normal + scale_trans

    static_scale_only = use_ai_ratio and ai_scale_base != 1.0 and (scale_normal <= 0 and scale_zoom_hold <= 0)
    animated_scale = use_ai_ratio and not static_scale_only and scale_cycle > 0

    if not use_ai_ratio or ai_scale_base == 1.0:
        if not animated_scale:
            return None

    if static_scale_only:
        # Static crop: constant zoom
        s = ai_scale_base
        if s > 1.0:
            pan_x = settings.get('pan_x', 0.0)
            pan_y = settings.get('pan_y', 0.0)
            mirror_video = settings.get('mirror_video', False)
            keep_hook = settings.get('keep_hook', True)
            hook_duration = settings.get('keep_hook_duration', 3.0)

            if mirror_video:
                if keep_hook and hook_duration > 0:
                    pan_x_expr = f"(if(gt(t\\,{hook_duration:.2f})\\,{-pan_x:.4f}\\,{pan_x:.4f}))"
                else:
                    pan_x_expr = f"{-pan_x:.4f}"
            else:
                pan_x_expr = f"{pan_x:.4f}"

            crop_w = f"iw/{s:.6f}"
            crop_h = f"ih/{s:.6f}"
            crop_x = f"(iw-{crop_w})/2 + ({pan_x_expr})*(iw-{crop_w})/2"
            crop_y = f"(ih-{crop_h})/2 + ({pan_y:.4f})*(ih-{crop_h})/2"
            return f"crop=w={crop_w}:h={crop_h}:x={crop_x}:y={crop_y},scale={w}:{h}"
        else:
            scale_w = f"trunc(iw*{s:.6f}/2)*2"
            scale_h = f"trunc(ih*{s:.6f}/2)*2"
            return f"scale={scale_w}:{scale_h},pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
    
    if animated_scale and scale_cycle > 0:
        # Build time-varying crop expression
        # Variables in FFmpeg expressions: t = time, iw = input width, ih = input height
        P1 = scale_zoom_hold
        P2 = P1 + scale_trans
        P3 = P2 + scale_normal
        CYCLE = scale_cycle
        ZOOM = ai_scale_zoom
        BASE = ai_scale_base
        EASE = scale_easing
        TRANS = scale_trans

        if TRANS < 0.001:
            # No easing: instant switch
            scale_expr = f"if(lt(mod(t\\,{CYCLE:.4f})\\,{P1:.4f})\\,{ZOOM:.6f}\\,{BASE:.6f})"
        else:
            # Full easing with sine interpolation
            # ct = mod(t, CYCLE)
            ct = f"mod(t\\,{CYCLE:.6f})"
            
            # ramp for phase 2 (zoom→base): (ct - P1) / TRANS
            ramp2 = f"((({ct})-{P1:.6f})/{TRANS:.6f})"
            # eased2 = ramp*(1-EASE) + sin(PI*ramp/2)*EASE
            eased2 = f"(({ramp2})*(1-{EASE:.6f})+sin(PI*({ramp2})/2)*{EASE:.6f})"
            # scale2 = ZOOM + (BASE-ZOOM) * eased2
            scale2 = f"({ZOOM:.6f}+({BASE:.6f}-{ZOOM:.6f})*{eased2})"

            # ramp for phase 4 (base→zoom): (ct - P3) / TRANS
            ramp4 = f"((({ct})-{P3:.6f})/{TRANS:.6f})"
            eased4 = f"(({ramp4})*(1-{EASE:.6f})+sin(PI*({ramp4})/2)*{EASE:.6f})"
            scale4 = f"({BASE:.6f}+({ZOOM:.6f}-{BASE:.6f})*{eased4})"

            # Full piecewise expression
            scale_expr = (
                f"if(lt({ct}\\,{P1:.6f})\\,{ZOOM:.6f}\\,"
                f"if(lt({ct}\\,{P2:.6f})\\,{scale2}\\,"
                f"if(lt({ct}\\,{P3:.6f})\\,{BASE:.6f}\\,"
                f"{scale4})))"
            )

        # crop filter: w=iw/scale, h=ih/scale, centered with pan
        pan_x = settings.get('pan_x', 0.0)
        pan_y = settings.get('pan_y', 0.0)
        
        # FFmpeg applies crop BEFORE hflip, but preview applies flip BEFORE crop.
        # To match the UI preview, we must invert pan_x during the mirrored portion.
        mirror_video = settings.get('mirror_video', False)
        keep_hook = settings.get('keep_hook', True)
        hook_duration = settings.get('keep_hook_duration', 3.0)
        
        if mirror_video:
            if keep_hook and hook_duration > 0:
                pan_x_expr = f"(if(gt(t\\,{hook_duration:.2f})\\,{-pan_x:.4f}\\,{pan_x:.4f}))"
            else:
                pan_x_expr = f"{-pan_x:.4f}"
        else:
            pan_x_expr = f"{pan_x:.4f}"
        
        crop_w = f"iw/({scale_expr})"
        crop_h = f"ih/({scale_expr})"
        crop_x = f"(iw-{crop_w})/2 + ({pan_x_expr})*(iw-{crop_w})/2"
        crop_y = f"(ih-{crop_h})/2 + ({pan_y:.4f})*(ih-{crop_h})/2"
        
        return f"crop=w={crop_w}:h={crop_h}:x={crop_x}:y={crop_y},scale={w}:{h}"
    
    return None


# ─── FONT FILE MAPPING (Windows) ─────────────────────────────────────────────
FONT_FILE_MAP = {
    "Arial": "arial.ttf",
    "Times New Roman": "times.ttf",
    "Courier New": "cour.ttf",
    "Verdana": "verdana.ttf",
    "Tahoma": "tahoma.ttf",
    "Trebuchet MS": "trebuc.ttf",
    "Impact": "impact.ttf",
}

def _get_font_path(font_name: str, cues=None) -> str:
    """Resolve font name to full Windows font path."""
    filename = FONT_FILE_MAP.get(font_name, "arial.ttf")
    
    # Try to find a CJK fallback if cues contain CJK text and Arial is selected
    if cues and filename == "arial.ttf":
        has_cjk = False
        for cue in cues:
            if any('\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7af' or '\uff00' <= c <= '\uffef' for c in cue["text"]):
                has_cjk = True
                break
        if has_cjk:
            for cjk_font in ["meiryo.ttc", "msgothic.ttc", "msyh.ttc", "malgun.ttf"]:
                cjk_path = os.path.join("C:/Windows/Fonts", cjk_font)
                if os.path.exists(cjk_path):
                    return cjk_path.replace("\\", "/")

    full_path = os.path.join("C:/Windows/Fonts", filename)
    if os.path.exists(full_path):
        return full_path.replace("\\", "/")
    # Fallback
    return "C:/Windows/Fonts/arial.ttf"


def _srt_time_to_seconds(ts: str) -> float:
    """Convert SRT timestamp '00:01:23,456' to seconds."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def parse_srt(srt_path: str) -> list:
    """Parse SRT file → list of dicts: {start, end, text}."""
    cues = []
    with open(srt_path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    blocks = content.strip().split("\n\n")
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        ts_line = lines[1]
        if "-->" not in ts_line:
            continue
        parts = ts_line.split("-->")
        start = _srt_time_to_seconds(parts[0])
        end = _srt_time_to_seconds(parts[1])
        text = " ".join(line.strip() for line in lines[2:] if line.strip())
        if text:
            cues.append({"start": start, "end": end, "text": text})
    return cues


def calculate_ffmpeg_params(font_size, sub_y_norm, vid_w, vid_h):
    """Calculate FFmpeg drawtext parameters in absolute video pixels.
    
    Args:
        font_size:  Font size in pixels (from UI slider)
        sub_y_norm: Vertical position 0.0=top, 1.0=bottom (from UI)
        vid_w, vid_h: Output video dimensions
    
    Returns:
        (x_expr, y_expr, ffmpeg_fontsize)
        x_expr/y_expr are FFmpeg expressions for drawtext.
    """
    ffmpeg_fontsize = max(10, font_size)
    margin_v = max(10, int(vid_h * (1.0 - sub_y_norm)))
    x_expr = "(w-tw)/2"                   # centered horizontally
    y_expr = f"h-th-{margin_v}"            # from bottom edge
    return x_expr, y_expr, ffmpeg_fontsize


def _wrap_subtitle_text(text, max_chars_per_line):
    """Wrap subtitle text to fit within max_chars_per_line.
    
    Handles both CJK (Chinese/Japanese/Korean) and Latin text.
    CJK characters are ~1.7x wider than Latin, so they count more.
    """
    import unicodedata
    
    def char_width(ch):
        """Return effective width: CJK=1.7, Latin=1.0"""
        cat = unicodedata.east_asian_width(ch)
        return 1.7 if cat in ('W', 'F') else 1.0
    
    def line_width(s):
        return sum(char_width(c) for c in s)
    
    if line_width(text) <= max_chars_per_line:
        return text
    
    # Try to split at spaces first (for Latin/Vietnamese text)
    words = text.split(' ')
    lines = []
    current_line = ""
    
    for word in words:
        test = f"{current_line} {word}".strip() if current_line else word
        if line_width(test) <= max_chars_per_line:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            # If a single word is too long (e.g. CJK without spaces), break it
            if line_width(word) > max_chars_per_line:
                chars = list(word)
                current_line = ""
                for ch in chars:
                    if line_width(current_line + ch) > max_chars_per_line:
                        if current_line:
                            lines.append(current_line)
                        current_line = ch
                    else:
                        current_line += ch
            else:
                current_line = word
    
    if current_line:
        lines.append(current_line)
    
    return '\n'.join(lines)


def build_drawtext_filters(cues, font_path, font_size, font_color_hex,
                           bg_color_hex, bg_opacity, x_expr, y_expr,
                           script_dir, speed_factor=1.0, start_offset=0.0, max_duration=None, 
                           vid_w=1080, sub_width=0.90, line_spacing=0, padding=10, sub_speed_mult=1.0):
    """Build drawtext vf filter strings for each SRT cue.
    
    Uses textfile= per cue for robust Unicode handling.
    Returns (list_of_filter_strings, list_of_temp_files).
    """
    filters = []
    temp_files = []
    
    # FFmpeg color format: 0xRRGGBB  /  boxcolor with alpha: 0xRRGGBB@0.5
    fc = f"0x{font_color_hex}"
    bg_alpha = max(0.0, min(1.0, bg_opacity / 100.0))
    box_color = f"0x{bg_color_hex}@{bg_alpha:.2f}"
    border_w = max(1, font_size // 25)
    box_pad = max(2, font_size // 10)
    
    # Calculate max chars per line: usable width / avg char width
    usable_w = vid_w * sub_width
    avg_char_px = font_size * 0.42  # allowed more chars per line
    max_chars = max(10, int(usable_w / avg_char_px))
    
    # Copy font to script_dir to avoid Windows path escaping (C:\, colons, etc.)
    import shutil
    font_local_name = "_subfont.ttf"
    font_local_path = os.path.join(script_dir, font_local_name)
    try:
        shutil.copy2(font_path, font_local_path)
        temp_files.append(font_local_path)
    except Exception:
        font_local_name = "_subfont.ttf"  # fallback, drawtext will use default
    
    for i, cue in enumerate(cues):
        # Wrap text to fit video width
        wrapped_text = _wrap_subtitle_text(cue["text"], max_chars)
        
        # Write cue text to a temp file (avoids all escaping issues)
        txt_name = f"_sub_cue_{i}.txt"
        txt_path = os.path.join(script_dir, txt_name)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(wrapped_text)
        temp_files.append(txt_path)
        
        # Adjust timing for trim start and speed factor (with manual multiplier)
        total_speed = speed_factor * sub_speed_mult
        t_start = (cue["start"] - start_offset) / total_speed
        t_end = (cue["end"] - start_offset) / total_speed
        
        # Skip subtitles that are before the trimmed start or after the trimmed end
        if t_end <= 0:
            continue
        if max_duration is not None and t_start >= max_duration:
            continue
            
        # Ensure t_start is at least 0
        t_start = max(0.0, t_start)
        # Cap t_end at max_duration if provided
        if max_duration is not None:
            t_end = min(t_end, max_duration)
        
        # If after clipping t_end is still <= t_start, skip
        if t_end <= t_start:
            continue
        
        flt = (
            f"drawtext=textfile={txt_name}"
            f":fontfile={font_local_name}"
            f":fontsize={font_size}"
            f":fontcolor={fc}"
            f":x={x_expr}"
            f":y={y_expr}"
            f":borderw={border_w}"
            f":bordercolor=black"
            f":line_spacing={line_spacing}"
            f":box=1"
            f":boxcolor={box_color}"
            f":boxborderw={padding}"
            f":enable='between(t\\,{t_start:.3f}\\,{t_end:.3f})'"
        )
        filters.append(flt)
    
    return filters, temp_files

def process_video(input_path, output_path, settings, progress_cb=None, log_cb=None, start_t=None, end_t=None):
    """Process a video using Pure FFmpeg pipeline (NVDEC decode → filters → NVENC encode).
    
    This is the Turbo GPU pipeline: no Python frame processing, everything runs
    inside FFmpeg as a single subprocess with hardware acceleration.
    """
    
    ffmpeg = _get_ffmpeg_exe()
    info = _probe_video(input_path)
    
    orig_duration = info['duration']
    orig_w = info['width']
    orig_h = info['height']
    orig_fps = info['fps']
    codec = info['codec']
    has_audio = info['has_audio']
    
    # --- SETTINGS ---
    duration_setting = settings.get('duration', 0)
    use_duration = settings.get('use_duration', False)
    max_source = settings.get('max_source', False)
    mirror_video = settings.get('mirror_video', False)
    micro_speed = settings.get('micro_speed', True)
    keep_hook = settings.get('keep_hook', True)
    im_res = settings.get('im_res', False)
    use_video_speed = settings.get('use_video_speed', False)
    video_speed = settings.get('video_speed', 1.0)
    use_pitch = settings.get('use_pitch', False)
    pitch_ratio = settings.get('pitch_ratio', 1.0)
    use_volume = settings.get('use_volume', False)
    volume = settings.get('volume', 1.0)
    target_ratio_setting = settings.get('target_ratio', 'Original')
    fit_mode = settings.get('fit_mode', 'Crop to Fill')
    blur_intensity = settings.get('blur_intensity', 31)
    if blur_intensity % 2 == 0:
        blur_intensity += 1
        
    use_bg_music = settings.get('use_bg_music', False)
    bg_music_path = settings.get('bg_music_path', "")
    bgm_volume = settings.get('bgm_volume', 1.0)
    bgm_speed = settings.get('bgm_speed', 1.0)
    has_bg_music = use_bg_music and bg_music_path and os.path.exists(bg_music_path)
    
    use_subtitles = settings.get('use_subtitles', False)
    subtitle_path = settings.get('subtitle_path', "")
    sub_font = settings.get('subtitle_font', 'Arial')
    sub_color = settings.get('subtitle_color', '#ffffff').lstrip('#')
    sub_bg_color = settings.get('subtitle_bg_color', '#000000').lstrip('#')
    sub_bg_opacity = settings.get('subtitle_bg_opacity', 50)
    sub_x_norm = settings.get('sub_x_norm', 0.5)
    sub_y_norm = settings.get('sub_y_norm', 0.9)
    sub_font_size = settings.get('subtitle_font_size', 48)
    subtitle_speed_mult = settings.get('subtitle_speed', 1.0)


    
    # Hook duration (first N seconds with no scale effects or mirror)
    hook_duration = settings.get('keep_hook_duration', 3.0) if keep_hook else 0.0
    
    timing = get_timing_plan(input_path, settings, info)
    trim_start = timing['trim_start']
    trim_end = timing['trim_end']
    trimmed_duration = timing['trimmed_duration']
    final_speed = timing['final_speed']
    final_duration = timing['final_duration']
    
    # --- DETERMINE OUTPUT DIMENSIONS ---
    w, h = orig_w, orig_h
    if target_ratio_setting != 'Original':
        if '9:16' in target_ratio_setting:
            target_aspect = 9.0 / 16.0
        else:
            target_aspect = 16.0 / 9.0
        
        # Set output dimensions based on original resolution
        if target_aspect < 1.0:  # Vertical
            h = max(orig_h, orig_w)
            w = int(h * target_aspect)
        else:  # Horizontal
            w = max(orig_w, orig_h)
            h = int(w / target_aspect)
        # Ensure even dimensions
        w = w + (w % 2)
        h = h + (h % 2)
    else:
        w = orig_w + (orig_w % 2)
        h = orig_h + (orig_h % 2)

    # --- PRE-RENDER OVERLAYS ---
    logo_png, logo_x, logo_y = _render_logo_overlay(settings, w, h, mirror_video)
    text_png, text_x, text_y = _render_text_overlay(settings, w, h, mirror_video)

    if log_cb:
        if logo_png:
            try:
                with Image.open(logo_png) as logo_img:
                    log_cb(
                        "Logo overlay: "
                        f"mode={settings.get('logo_pos', 'Bottom-Right')}, "
                        f"norm=({settings.get('logo_x_norm', 0.85):.3f},{settings.get('logo_y_norm', 0.85):.3f}), "
                        f"pixel=({logo_x},{logo_y}), size=({logo_img.width},{logo_img.height}), output=({w},{h})"
                    )
            except Exception as exc:
                log_cb(f"Logo overlay debug failed: {exc}")
        if text_png:
            try:
                with Image.open(text_png) as text_img:
                    log_cb(
                        "Text overlay: "
                        f"norm=({settings.get('text_x_norm', 0.5):.3f},{settings.get('text_y_norm', 0.9):.3f}), "
                        f"pixel=({text_x},{text_y}), box=({text_img.width},{text_img.height}), output=({w},{h})"
                    )
            except Exception as exc:
                log_cb(f"Text overlay debug failed: {exc}")
    
    temp_files = []
    tts_data = []
    if logo_png:
        temp_files.append(logo_png)
    if text_png:
        temp_files.append(text_png)
    
    try:
        # --- BUILD FFMPEG COMMAND ---
        cmd = [ffmpeg, '-y']  # Overwrite output
        
        # Hardware-accelerated decoding (try CUVID first)
        # NOTE: subtitles filter (libass) is CPU-only and CANNOT process
        # GPU-memory frames from h264_cuvid. When subtitles are enabled,
        # we MUST use CPU decoding.
        use_hw_decode = not (use_subtitles and subtitle_path and os.path.exists(subtitle_path))
        if use_hw_decode:
            cuvid_codecs = {'h264': 'h264_cuvid', 'hevc': 'hevc_cuvid', 'vp9': 'vp9_cuvid',
                            'av1': 'av1_cuvid', 'mpeg4': 'mpeg4_cuvid', 'mpeg2video': 'mpeg2_cuvid'}
            hw_decoder = cuvid_codecs.get(codec)
            if hw_decoder:
                cmd += ['-c:v', hw_decoder]
        else:
            if log_cb:
                log_cb("Using CPU decoding (required for subtitles filter)")
        
        # Input file with trim
        if trim_start > 0:
            cmd += ['-ss', f'{trim_start:.3f}']
        cmd += ['-i', input_path]
        if trim_end < orig_duration:
            cmd += ['-t', f'{(trim_end - trim_start):.3f}']
        
        # Input overlay files
        input_idx = 1
        logo_input_idx = None
        text_input_idx = None
        bgm_input_idx = None
        if logo_png:
            cmd += ['-i', logo_png]
            logo_input_idx = input_idx
            input_idx += 1
        if text_png:
            cmd += ['-i', text_png]
            text_input_idx = input_idx
            input_idx += 1
        
        if has_bg_music:
            cmd += ['-stream_loop', '-1', '-i', bg_music_path]
            bgm_input_idx = input_idx
            input_idx += 1

        
        # --- BUILD VIDEO FILTER CHAIN ---
        vf_parts = []
        
        # Aspect ratio transformation
        orig_aspect = orig_w / orig_h
        if target_ratio_setting != 'Original':
            if '9:16' in target_ratio_setting:
                target_aspect = 9.0 / 16.0
            else:
                target_aspect = 16.0 / 9.0
            
            if fit_mode == 'Crop to Fill':
                # Center-crop to target aspect, then scale
                if orig_aspect > target_aspect:
                    crop_w = f"ih*{target_aspect:.6f}"
                    crop_h = "ih"
                else:
                    crop_w = "iw"
                    crop_h = f"iw/{target_aspect:.6f}"
                vf_parts.append(f"crop={crop_w}:{crop_h}")
                vf_parts.append(f"scale={w}:{h}")
            
            elif fit_mode == 'Fit (Black Bars)':
                # Scale to fit, then pad with black bars
                vf_parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease")
                vf_parts.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
            
            elif fit_mode == 'Fit with Blur':
                # This requires filter_complex for blurred background
                # We'll handle this specially below
                pass
        else:
            # Original ratio — just ensure even dimensions
            if orig_w != w or orig_h != h:
                vf_parts.append(f"scale={w}:{h}")
        
        # Scale animation (crop-zoom)
        scale_filter = _build_scale_crop_expr(settings, w, h)
        if scale_filter:
            vf_parts.append(scale_filter)
        
        # Mirror
        if mirror_video:
            if keep_hook and hook_duration > 0:
                vf_parts.append(f"hflip=enable='gt(t,{hook_duration:.2f})'")
            else:
                vf_parts.append("hflip")
        
        # Use filter_complex when we have overlays, blur-fit mode, or bg music
        use_filter_complex = (logo_png is not None or text_png is not None or
                              (target_ratio_setting != 'Original' and fit_mode == 'Fit with Blur') or
                              has_bg_music)
        if log_cb:
            log_cb(f"use_filter_complex={use_filter_complex} (logo={logo_png is not None}, text={text_png is not None}, blur_fit={target_ratio_setting != 'Original' and fit_mode == 'Fit with Blur'}, bgm={has_bg_music})")
        
        # --- BUILD AUDIO FILTER CHAIN ---
        af_parts = []
        
        # Subtitles via drawtext (absolute pixel coordinates, matches preview exactly)
        sub_drawtext_filters = []
        if use_subtitles and subtitle_path and os.path.exists(subtitle_path):
            try:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                # Parse SRT cues
                cues = parse_srt(subtitle_path)
                
                font_path = _get_font_path(sub_font, cues)
                
                # Calculate position & size in video pixels
                x_expr, y_expr, ffmpeg_fs = calculate_ffmpeg_params(
                    sub_font_size, sub_y_norm, w, h
                )
                
                if cues:
                    # Build drawtext filter for each cue
                    sub_width = settings.get('subtitle_width', 0.90)
                    line_spacing = settings.get('subtitle_line_spacing', 0)
                    padding = settings.get('subtitle_padding', 10)
                    
                    dt_filters, dt_temp_files = build_drawtext_filters(
                        cues=cues,
                        font_path=font_path,
                        font_size=ffmpeg_fs,
                        font_color_hex=sub_color,
                        bg_color_hex=sub_bg_color,
                        bg_opacity=sub_bg_opacity,
                        x_expr=x_expr,
                        y_expr=y_expr,
                        script_dir=script_dir,
                        speed_factor=final_speed,
                        start_offset=trim_start,
                        max_duration=final_duration,
                        vid_w=w,
                        sub_width=sub_width,
                        line_spacing=line_spacing,
                        padding=padding,
                        sub_speed_mult=subtitle_speed_mult
                    )
                    sub_drawtext_filters = dt_filters
                    temp_files.extend(dt_temp_files)
                    
                    if log_cb:
                        log_cb(f"Drawtext subtitles: {len(cues)} cues, fontsize={ffmpeg_fs}px")
                        log_cb(f"Position: x={x_expr}, y={y_expr}")
                else:
                    if log_cb:
                        log_cb(f"No subtitle cues found in: {subtitle_path}")
            except Exception as e:
                if log_cb:
                    log_cb(f"Subtitle error: {e}")
            
        # Speed change (video: setpts, audio: atempo)
        if abs(final_speed - 1.0) > 0.001:
            vf_parts.append(f"setpts=PTS/{final_speed:.6f}")
            # atempo only supports 0.5-100.0, chain multiple for extreme values
            remaining_speed = final_speed
            while remaining_speed > 2.0:
                af_parts.append("atempo=2.0")
                remaining_speed /= 2.0
            while remaining_speed < 0.5:
                af_parts.append("atempo=0.5")
                remaining_speed *= 2.0
            if abs(remaining_speed - 1.0) > 0.001:
                af_parts.append(f"atempo={remaining_speed:.6f}")
        
        # Volume
        if use_volume and abs(volume - 1.0) > 0.01:
            af_parts.append(f"volume={volume:.3f}")
        
        # Pitch shift
        if use_pitch and abs(pitch_ratio - 1.0) > 0.01:
            new_rate = int(44100 * pitch_ratio)
            inverse_tempo = 1.0 / pitch_ratio
            af_parts.append(f"asetrate={new_rate}")
            af_parts.append(f"atempo={inverse_tempo:.6f}")
            af_parts.append(f"aresample=44100")
        
        # --- ASSEMBLE FINAL COMMAND ---
        if use_filter_complex:
            # Build filter_complex graph
            fc_parts = []
            current_vid = "[0:v]"
            
            # Blur-fit mode needs special handling
            if target_ratio_setting != 'Original' and fit_mode == 'Fit with Blur':
                if '9:16' in target_ratio_setting:
                    target_aspect = 9.0 / 16.0
                else:
                    target_aspect = 16.0 / 9.0
                
                # Background: crop to target aspect, blur heavily, darken
                if orig_aspect > target_aspect:
                    bg_crop = f"crop=ih*{target_aspect:.6f}:ih"
                else:
                    bg_crop = f"crop=iw:iw/{target_aspect:.6f}"
                
                ksize = max(3, (blur_intensity // 8) | 1)
                # Use boxblur for speed; approximate the Gaussian
                blur_radius = max(1, blur_intensity // 4)
                
                # Optimize by scaling down before blurring (matches preview_engine.py logic)
                small_w = max(2, w // 8)
                small_h = max(2, h // 8)
                
                fc_parts.append(
                    f"[0:v]{bg_crop},scale={small_w}:{small_h},"
                    f"boxblur={blur_radius}:{blur_radius},"
                    f"scale={w}:{h},"
                    f"colorlevels=rimax=0.6:gimax=0.6:bimax=0.6[bg]"
                )
                # Foreground: scale to fit
                fc_parts.append(
                    f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease[fg]"
                )
                # Overlay foreground on blurred background
                fc_parts.append(
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2[composed]"
                )
                current_vid = "[composed]"
            else:
                # Apply basic video filters first
                if vf_parts:
                    basic_vf = ",".join(vf_parts)
                    fc_parts.append(f"[0:v]{basic_vf}[base]")
                    current_vid = "[base]"
                    vf_parts = []  # consumed
            
            # If we still have vf_parts (for blur-fit mode + scale/mirror)
            remaining_vf = []
            if scale_filter:
                remaining_vf.append(scale_filter)
            if mirror_video:
                if keep_hook and hook_duration > 0:
                    remaining_vf.append(f"hflip=enable='gt(t,{hook_duration:.2f})'")
                else:
                    remaining_vf.append("hflip")
            if abs(final_speed - 1.0) > 0.001:
                remaining_vf.append(f"setpts=PTS/{final_speed:.6f}")
            
            if target_ratio_setting != 'Original' and fit_mode == 'Fit with Blur':
                # Apply remaining video filters to composed stream
                if remaining_vf:
                    rv_str = ",".join(remaining_vf)
                    fc_parts.append(f"{current_vid}{rv_str}[processed]")
                    current_vid = "[processed]"
            
            # Logo overlay (rendered BEFORE text so text is on top)
            if logo_input_idx is not None:
                next_label = "[with_logo]"
                fc_parts.append(
                    f"{current_vid}[{logo_input_idx}:v]overlay={logo_x}:{logo_y}{next_label}"
                )
                current_vid = next_label
            
            # Text overlay (rendered AFTER logo so text is on top)
            if text_input_idx is not None:
                next_label = "[with_text]"
                fc_parts.append(
                    f"{current_vid}[{text_input_idx}:v]overlay={text_x}:{text_y}{next_label}"
                )
                current_vid = next_label
            
            # Apply subtitle drawtext filters at the very end of video chain
            if sub_drawtext_filters:
                dt_chain = ",".join(sub_drawtext_filters)
                next_label = "[with_subs]"
                fc_parts.append(f"{current_vid}{dt_chain}{next_label}")
                current_vid = next_label

            # Final output label for video
            if current_vid.startswith('[') and current_vid.endswith(']'):
                next_label = "[vout]"
                fc_parts.append(f"{current_vid}setsar=1{next_label}")
                current_vid = next_label
                
            # Audio complex filter for background music mixing
            current_a = None
            if has_audio or has_bg_music:
                if has_audio:
                    # Original audio volume
                    orig_af = list(af_parts)

                    if orig_af:
                        fc_parts.append(f"[0:a]{','.join(orig_af)}[main_a]")
                        current_a = "[main_a]"
                    else:
                        current_a = "[0:a]"
                
                if has_bg_music:
                    bgm_af = []
                    if abs(bgm_speed - 1.0) > 0.001:
                        remaining_speed = bgm_speed
                        while remaining_speed > 2.0:
                            bgm_af.append("atempo=2.0")
                            remaining_speed /= 2.0
                        while remaining_speed < 0.5:
                            bgm_af.append("atempo=0.5")
                            remaining_speed *= 2.0
                        if abs(remaining_speed - 1.0) > 0.001:
                            bgm_af.append(f"atempo={remaining_speed:.6f}")
                    if abs(bgm_volume - 1.0) > 0.001:
                        bgm_af.append(f"volume={bgm_volume:.3f}")
                    
                    bgm_filter_str = ",".join(bgm_af) if bgm_af else "anull"
                    fc_parts.append(f"[{bgm_input_idx}:a]{bgm_filter_str}[bgm_a]")
                    
                    if current_a:
                        fc_parts.append(f"{current_a}[bgm_a]amix=inputs=2:duration=first:dropout_transition=2[after_bgm]")
                        current_a = "[after_bgm]"
                    else:
                        fc_parts.append(f"[bgm_a]anull[after_bgm]")
                        current_a = "[after_bgm]"

                if current_a and current_a not in ("[aout]", "[after_bgm]"):
                    # Rename last audio label to [aout] for clarity
                    last = fc_parts[-1]
                    if current_a in last:
                        fc_parts[-1] = last.replace(current_a, "[aout]")
                        current_a = "[aout]"
            fc_string = ";".join(fc_parts)
            cmd += ['-filter_complex', fc_string]
            cmd += ['-map', current_vid]
            
            if log_cb:
                log_cb(f"filter_complex ({len(fc_parts)} parts): {fc_string[:500]}{'...' if len(fc_string) > 500 else ''}")
            
            if current_a:
                cmd += ['-map', current_a]
            elif has_audio:
                if af_parts:
                    af_string = ",".join(af_parts)
                    cmd += ['-af', af_string]
                cmd += ['-map', '0:a?']
                
            # Use -shortest if we mapped just bgm_a to cut it at video length
            if has_bg_music and not has_audio:
                cmd += ['-shortest']
                
        else:
            # Simple mode: no overlays, just -vf and -af
            # Append drawtext subtitle filters to the video filter chain
            all_vf = list(vf_parts)
            if sub_drawtext_filters:
                all_vf.extend(sub_drawtext_filters)
            all_vf.append("setsar=1")
            
            if log_cb:
                log_cb(f"Simple mode: {len(all_vf)} video filters, {len(sub_drawtext_filters)} subtitle cues")
            if all_vf:
                cmd += ['-vf', ','.join(all_vf)]
            if af_parts and has_audio:
                cmd += ['-af', ','.join(af_parts)]
        
        # --- OUTPUT ENCODING ---
        cmd += [
            '-c:v', 'h264_nvenc',
            '-preset', 'p1',
            '-rc', 'vbr',
            '-cq', '28',
            '-b:v', '0',
            '-profile:v', 'main',
            '-bf', '0',
            '-g', '60',
            '-gpu', '0',
            '-c:a', 'aac',
            '-b:a', '192k',
        ]
        
        # No audio if source has none
        if not has_audio:
            cmd += ['-an']
        
        # Progress reporting via FFmpeg stderr  
        cmd += ['-progress', 'pipe:1']
        cmd += [output_path]
        
        # Ensure output directory exists
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        
        if log_cb:
            log_cb(f"Turbo GPU Render: {os.path.basename(input_path)}")
            # Log the FULL FFmpeg command for debugging
            cmd_full = ' '.join(cmd)
            log_cb(f"FFmpeg FULL cmd: {cmd_full}")
        
        # --- RUN FFMPEG ---
        kwargs = {}
        if os.name == 'nt':
            kwargs['creationflags'] = 0x08000000
            
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout to prevent deadlock
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            cwd=os.path.dirname(os.path.abspath(__file__)),
            **kwargs
        )
        
        # Parse progress from stdout (pipe:1 format)
        error_log = []
        while True:
            line = process.stdout.readline()
            if not line:
                break
            
            # Keep rolling log of last 50 lines for error reporting
            if len(error_log) > 50:
                error_log.pop(0)
            error_log.append(line.strip())
            
            line = line.strip()
            if line.startswith('out_time_us='):
                try:
                    time_us = int(line.split('=')[1])
                    current_time = time_us / 1_000_000.0
                    if final_duration > 0 and progress_cb:
                        pct = min(1.0, current_time / final_duration)
                        progress_cb(pct)
                except (ValueError, IndexError):
                    pass
            elif line == 'progress=end':
                break
        
        process.wait()
        
        if process.returncode != 0:
            error_msg = "\n".join(error_log[-20:])
            if log_cb:
                log_cb(f"FFmpeg error: {error_msg}")
            raise Exception(f"FFmpeg failed (exit code {process.returncode}): {error_msg}")
        
        if progress_cb:
            progress_cb(1.0)
    
    finally:
        # Clean up temp overlay files
        for f in temp_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
