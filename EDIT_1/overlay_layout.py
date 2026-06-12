import os
import unicodedata
from PIL import Image, ImageDraw, ImageFont


def wrap_multilingual_text(text, max_chars_per_line):
    def char_width(ch):
        cat = unicodedata.east_asian_width(ch)
        return 1.7 if cat in ("W", "F") else 1.0

    def line_width(value):
        return sum(char_width(ch) for ch in value)

    if line_width(text) <= max_chars_per_line:
        return text

    words = text.split(" ")
    lines = []
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip() if current_line else word
        if line_width(test_line) <= max_chars_per_line:
            current_line = test_line
            continue
        if current_line:
            lines.append(current_line)
        if line_width(word) > max_chars_per_line:
            current_line = ""
            for ch in word:
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
    return "\n".join(lines)


def parse_hex_rgba(value, fallback):
    try:
        rgb = tuple(int(value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        return rgb + (255,)
    except Exception:
        return fallback


def clamp_rect_to_frame(x, y, box_w, box_h, frame_w, frame_h):
    x = max(0, min(int(x), max(0, frame_w - box_w)))
    y = max(0, min(int(y), max(0, frame_h - box_h)))
    return x, y, int(box_w), int(box_h)


def centered_rect_from_norm(norm_x, norm_y, box_w, box_h, frame_w, frame_h):
    x = int(frame_w * float(norm_x)) - int(box_w) // 2
    y = int(frame_h * float(norm_y)) - int(box_h) // 2
    return clamp_rect_to_frame(x, y, box_w, box_h, frame_w, frame_h)


def anchored_rect(position, box_w, box_h, frame_w, frame_h, pad_ratio=0.05):
    pad_x = int(frame_w * pad_ratio)
    pad_y = int(frame_h * pad_ratio)
    if position == "Top-Left":
        x, y = pad_x, pad_y
    elif position == "Top-Right":
        x, y = frame_w - box_w - pad_x, pad_y
    elif position == "Bottom-Left":
        x, y = pad_x, frame_h - box_h - pad_y
    elif position == "Bottom-Right":
        x, y = frame_w - box_w - pad_x, frame_h - box_h - pad_y
    else:
        x, y = (frame_w - box_w) // 2, (frame_h - box_h) // 2
    return clamp_rect_to_frame(x, y, box_w, box_h, frame_w, frame_h)


def _load_font_for_text(text, font_size, preferred_fonts=None):
    preferred_fonts = list(preferred_fonts or [])
    has_cjk = any(
        "\u4e00" <= ch <= "\u9fff"
        or "\u3040" <= ch <= "\u30ff"
        or "\uac00" <= ch <= "\ud7af"
        or "\uff00" <= ch <= "\uffef"
        for ch in text
    )
    fonts_to_try = preferred_fonts
    if has_cjk:
        fonts_to_try.extend(["meiryo.ttc", "msgothic.ttc", "msyh.ttc", "malgun.ttf"])
    fonts_to_try.extend(["arialbd.ttf", "arial.ttf"])
    tried = set()
    for font_name in fonts_to_try:
        if not font_name or font_name in tried:
            continue
        tried.add(font_name)
        try:
            return ImageFont.truetype(font_name, font_size)
        except IOError:
            continue
    return ImageFont.load_default()


def _build_text_overlay_image(text, size, color, norm_x, norm_y, frame_w, frame_h):
    text = str(text or "").replace("\\n", "\n")
    if not text.strip():
        return None, None

    size = float(size or 0.08)
    font_size = max(1, int(frame_h * size))
    font = _load_font_for_text(text, font_size, preferred_fonts=["arialbd.ttf", "arial.ttf"])
    stroke_width = max(1, int(frame_h * size * 0.05))
    text_color = parse_hex_rgba(color, (255, 255, 0, 255))

    max_chars = max(10, int(frame_w / max(1.0, frame_h * size * 0.55)))
    wrapped_lines = []
    for line in text.split("\n"):
        if not line.strip():
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(wrap_multilingual_text(line, max_chars).split("\n"))

    temp_img = Image.new("RGB", (1, 1))
    measure_draw = ImageDraw.Draw(temp_img)
    text_w = 0
    text_h = 0
    line_widths = []
    line_heights = []
    for line in wrapped_lines:
        if not line:
            line_widths.append(0)
            line_heights.append(int(frame_h * size))
            text_h += int(frame_h * size)
            continue
        if hasattr(measure_draw, "textbbox"):
            bbox = measure_draw.textbbox((0, 0), line, font=font)
            line_w = bbox[2] - bbox[0]
            line_h = bbox[3] - bbox[1]
        else:
            line_w, line_h = measure_draw.textsize(line, font=font)
        line_widths.append(line_w)
        line_heights.append(line_h)
        text_w = max(text_w, line_w)
        text_h += line_h

    line_spacing = int(frame_h * size * 0.2)
    text_h += max(0, len(wrapped_lines) - 1) * line_spacing
    text_h += stroke_width * 2
    box_w = int(text_w + stroke_width * 4)
    box_h = int(text_h + stroke_width * 4)
    if box_w <= 0 or box_h <= 0:
        return None, None

    image = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    current_y = stroke_width * 2
    for idx, line in enumerate(wrapped_lines):
        if not line:
            current_y += line_heights[idx] + line_spacing
            continue
        x_pos = (box_w - line_widths[idx]) // 2
        draw.text(
            (x_pos, current_y),
            line,
            font=font,
            fill=text_color,
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0, 255),
        )
        current_y += line_heights[idx] + line_spacing

    rect = centered_rect_from_norm(
        norm_x,
        norm_y,
        box_w,
        box_h,
        frame_w,
        frame_h,
    )
    return image, rect


def build_text_overlay_image(settings, frame_w, frame_h):
    if not settings.get("use_watermark", False):
        return None, None
    return _build_text_overlay_image(
        settings.get("watermark_text", ""),
        settings.get("watermark_size", 0.08),
        settings.get("watermark_color", "#ffff00"),
        settings.get("text_x_norm", 0.5),
        settings.get("text_y_norm", 0.9),
        frame_w,
        frame_h,
    )


def build_text2_overlay_image(settings, frame_w, frame_h):
    if not settings.get("use_text2", False):
        return None, None
    return _build_text_overlay_image(
        settings.get("text2_text", ""),
        settings.get("text2_size", 0.08),
        settings.get("text2_color", "#ffffff"),
        settings.get("text2_x_norm", 0.5),
        settings.get("text2_y_norm", 0.2),
        frame_w,
        frame_h,
    )


def build_logo_overlay_image(settings, frame_w, frame_h):
    use_logo = settings.get("use_logo", False)
    logo_path = settings.get("logo_path", "")
    if not use_logo or not logo_path or not os.path.exists(logo_path):
        return None, None

    try:
        image = Image.open(logo_path).convert("RGBA")
    except Exception:
        return None, None

    logo_scale = float(settings.get("logo_scale", 0.2) or 0.2)
    target_w = max(10, int(frame_w * logo_scale))
    aspect_ratio = image.height / max(1, image.width)
    target_h = max(1, int(target_w * aspect_ratio))
    image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)

    logo_opacity = float(settings.get("logo_opacity", 0.8) or 0.8)
    if logo_opacity < 1.0:
        alpha = image.getchannel("A").point(lambda a: int(a * logo_opacity))
        image.putalpha(alpha)

    logo_pos = settings.get("logo_pos", "Bottom-Right")
    if logo_pos == "Manual (Drag)":
        rect = centered_rect_from_norm(
            settings.get("logo_x_norm", 0.85),
            settings.get("logo_y_norm", 0.85),
            target_w,
            target_h,
            frame_w,
            frame_h,
        )
    else:
        rect = anchored_rect(logo_pos, target_w, target_h, frame_w, frame_h)
    return image, rect
