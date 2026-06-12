import os
import cv2
import numpy as np
import random
import math
import time
import threading
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage
from PIL import Image, ImageDraw, ImageFont
from overlay_layout import build_logo_overlay_image, build_text_overlay_image, build_text2_overlay_image


def _fit_background_image(rgb_image, target_w, target_h, fit_mode="Cover"):
    if rgb_image is None or target_w <= 0 or target_h <= 0:
        return None

    src_h, src_w = rgb_image.shape[:2]
    if src_w <= 0 or src_h <= 0:
        return None

    contain = str(fit_mode).lower() == "contain"
    scale = min(target_w / src_w, target_h / src_h) if contain else max(target_w / src_w, target_h / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(rgb_image, (new_w, new_h), interpolation=interp)

    if contain:
        canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        x = max(0, (target_w - new_w) // 2)
        y = max(0, (target_h - new_h) // 2)
        paste_w = min(target_w, new_w)
        paste_h = min(target_h, new_h)
        canvas[y:y + paste_h, x:x + paste_w] = resized[:paste_h, :paste_w]
        return canvas

    x1 = max(0, (new_w - target_w) // 2)
    y1 = max(0, (new_h - target_h) // 2)
    cropped = resized[y1:y1 + target_h, x1:x1 + target_w]
    if cropped.shape[0] == target_h and cropped.shape[1] == target_w:
        return cropped.copy()

    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    paste_h, paste_w = cropped.shape[:2]
    canvas[:paste_h, :paste_w] = cropped
    return canvas


def _load_background_image(settings, target_w, target_h):
    if not settings.get("use_bg_image", False):
        return None

    path = str(settings.get("bg_image_path", "") or "").strip()
    if not path or not os.path.exists(path):
        return None

    try:
        with Image.open(path) as img:
            rgb = np.array(img.convert("RGB"))
        return _fit_background_image(rgb, target_w, target_h, settings.get("bg_image_fit", "Cover"))
    except Exception:
        return None


class EffectProcessor:
    def __init__(self, settings, orig_w, orig_h, is_preview=True):
        self.settings = settings
        self.orig_w = orig_w
        self.orig_h = orig_h
        self.is_preview = is_preview
        self._precompute()

    def _precompute(self):
        settings = self.settings
        self.target_ratio_setting = settings.get('target_ratio', 'Original')
        self.fit_mode = settings.get('fit_mode', 'Crop to Fill')
        self.blur_intensity = settings.get('blur_intensity', 31)
        if self.blur_intensity % 2 == 0: self.blur_intensity += 1

        w, h = self.orig_w, self.orig_h
        if self.target_ratio_setting != 'Original':
            if '9:16' in self.target_ratio_setting:
                target_aspect = 9.0 / 16.0
            else:
                target_aspect = 16.0 / 9.0
            
            if target_aspect < 1.0: # Vertical
                h = max(self.orig_h, self.orig_w)
                w = int(h * target_aspect)
            else: # Horizontal
                w = max(self.orig_h, self.orig_w)
                h = int(w / target_aspect)
            w = w + (w % 2)
            h = h + (h % 2)
        else:
            w = w + (w % 2)
            h = h + (h % 2)

        self.render_w, self.render_h = w, h
        
        if self.is_preview:
            # Cap working resolution for real-time preview performance
            max_h_pref = 640
            if h > max_h_pref:
                scale = max_h_pref / h
                h = max_h_pref
                w = int(w * scale)
                w = w + (w % 2)
                h = h + (h % 2)
        
        self.w, self.h = w, h
        self.preview_scale = (self.h / self.render_h) if self.render_h else 1.0
        target_aspect = w / h
        orig_aspect = self.orig_w / self.orig_h

        # Precompute Crop to Fill
        if self.fit_mode == 'Crop to Fill':
            if orig_aspect > target_aspect:
                self.crop_w = int(self.orig_h * target_aspect)
                self.crop_h = self.orig_h
            else:
                self.crop_w = self.orig_w
                self.crop_h = int(self.orig_w / target_aspect)
            self.crop_x1 = (self.orig_w - self.crop_w) // 2
            self.crop_y1 = (self.orig_h - self.crop_h) // 2
        else: # Fit modes
            if orig_aspect > target_aspect:
                self.fg_w = w
                self.fg_h = int(w / orig_aspect)
            else:
                self.fg_h = h
                self.fg_w = int(h * orig_aspect)
            self.fg_x = (w - self.fg_w) // 2
            self.fg_y = (h - self.fg_h) // 2

            if self.fit_mode == 'Fit with Blur':
                if orig_aspect > target_aspect:
                    self.bg_crop_w = int(self.orig_h * target_aspect)
                    self.bg_crop_h = self.orig_h
                else:
                    self.bg_crop_w = self.orig_w
                    self.bg_crop_h = int(self.orig_w / target_aspect)
                self.bg_x1 = (self.orig_w - self.bg_crop_w) // 2
                self.bg_y1 = (self.orig_h - self.bg_crop_h) // 2

            self.bg_image = _load_background_image(settings, w, h)

        # Scale settings
        use_ai_ratio = settings.get('use_ai_ratio', False)
        ai_ratio = settings.get('ai_ratio', 100)
        self.ai_scale_base = (ai_ratio / 100.0) if use_ai_ratio else 1.0
        use_scale_zoom_ratio = settings.get('use_scale_zoom_ratio', False)
        scale_zoom_ratio = settings.get('scale_zoom_ratio', 120) if use_scale_zoom_ratio else ai_ratio
        self.ai_scale_zoom = scale_zoom_ratio / 100.0
        self.scale_normal = settings.get('scale_normal', 0) if settings.get('use_scale_normal', False) else 0
        self.scale_zoom_hold = settings.get('scale_zoom_hold', 0) if settings.get('use_scale_zoom', False) else 0
        self.scale_easing = settings.get('scale_easing', 100) / 100.0
        self.scale_trans = self.scale_easing * 1.0
        self.scale_cycle = self.scale_zoom_hold + self.scale_trans + self.scale_normal + self.scale_trans
        self.static_scale_only = use_ai_ratio and self.ai_scale_base != 1.0 and (self.scale_normal <= 0 and self.scale_zoom_hold <= 0)
        self.animated_scale = use_ai_ratio and not self.static_scale_only and self.scale_cycle > 0
        
        self.pan_x = settings.get('pan_x', 0.0)
        self.pan_y = settings.get('pan_y', 0.0)

        self.mirror_video = settings.get('mirror_video', False)
        self.keep_hook = settings.get('keep_hook', True)
        self.hook_duration = settings.get('keep_hook_duration', 3.0)
        
        self.overlay_alpha = None
        self.overlay_rgb = None
        text_image, text_rect = build_text_overlay_image(settings, w, h)
        if text_image is not None and text_rect is not None:
            overlay_rgba = np.array(text_image)
            self.overlay_rgb = overlay_rgba[:, :, :3]
            self.overlay_alpha = (overlay_rgba[:, :, 3] / 255.0)[:, :, np.newaxis]
            self.overlay_x, self.overlay_y, self.box_w, self.box_h = text_rect

        self.overlay2_alpha = None
        self.overlay2_rgb = None
        text2_image, text2_rect = build_text2_overlay_image(settings, w, h)
        if text2_image is not None and text2_rect is not None:
            overlay2_rgba = np.array(text2_image)
            self.overlay2_rgb = overlay2_rgba[:, :, :3]
            self.overlay2_alpha = (overlay2_rgba[:, :, 3] / 255.0)[:, :, np.newaxis]
            self.overlay2_x, self.overlay2_y, self.box2_w, self.box2_h = text2_rect

        # Subtitle Proxy (for preview positioning)
        use_subtitles = settings.get('use_subtitles', False)
        self.sub_alpha = None
        self.sub_rgb = None
        self.cues = []
        self.last_cue_text = None
        
        if use_subtitles:
            self.sub_font_name = settings.get('subtitle_font', 'Arial')
            self.sub_color_hex = settings.get('subtitle_color', '#ffffff').lstrip('#')
            self.sub_bg_hex = settings.get('subtitle_bg_color', '#000000').lstrip('#')
            self.sub_bg_opacity = settings.get('subtitle_bg_opacity', 50) / 100.0
            self.sub_font_size = max(10, int(round(settings.get('subtitle_font_size', 48) * self.preview_scale)))
            self.sub_y_norm = settings.get('sub_y_norm', 0.9)
            self.sub_line_spacing = int(round(settings.get('subtitle_line_spacing', 0) * self.preview_scale))
            self.sub_padding = max(1, int(round(settings.get('subtitle_padding', 10) * self.preview_scale)))
            self.sub_min_margin = max(1, int(round(10 * self.preview_scale)))
            self.subtitle_path = settings.get('subtitle_path', '')
            self.subtitle_speed_mult = settings.get('subtitle_speed', 1.0)
            self.trim_start = settings.get('_preview_trim_start', 0.0)
            
            if self.subtitle_path and os.path.exists(self.subtitle_path):
                try:
                    from video_engine import parse_srt
                    self.cues = parse_srt(self.subtitle_path)
                except Exception:
                    self.cues = []
            
            # Initial render of a placeholder if no cues, or just wait for process_frame
            self._render_subtitle_bitmap("[Subtitle Preview]")

        self.logo_rgb = None
        self.logo_alpha = None
        logo_image, logo_rect = build_logo_overlay_image(settings, w, h)
        if logo_image is not None and logo_rect is not None:
            logo_rgba = np.array(logo_image)
            self.logo_rgb = logo_rgba[:, :, :3]
            self.logo_alpha = (logo_rgba[:, :, 3] / 255.0)[:, :, np.newaxis]
            self.logo_x, self.logo_y, self.logo_w, self.logo_h = logo_rect

        # Particles
        use_particles = settings.get('use_particles', False)
        self.part_speed = settings.get('part_speed', 5)
        self.part_particles = None
        if use_particles:
            part_opacity = settings.get('part_opacity', 0.5)
            top_half = np.zeros((h, w), dtype=np.uint8)
            for _ in range(150):
                px = random.randint(0, w - 1); py = random.randint(0, h - 1)
                radius = random.randint(max(2, int(w * 0.002)), max(5, int(w * 0.006)))
                alpha_val = random.randint(int(100 * part_opacity), int(255 * part_opacity))
                cv2.circle(top_half, (px, py), radius, alpha_val, -1, cv2.LINE_AA)
            top_half = cv2.GaussianBlur(top_half, (5, 5), 0)
            stacked = np.vstack((top_half, top_half))
            self.part_particles = np.stack((stacked, stacked, stacked), axis=-1)

        # Vignette
        use_vignette = settings.get('use_vignette', False)
        vignette_strength = settings.get('vignette_strength', 0.5)
        self.vignette_lut = None
        if use_vignette and vignette_strength > 0:
            cx, cy = w / 2.0, h / 2.0
            Y, X = np.ogrid[:h, :w]
            dist = np.sqrt(((X - cx) / cx) ** 2 + ((Y - cy) / cy) ** 2)
            self.vignette_lut = np.clip((1.0 - dist * vignette_strength) * 256, 0, 256).astype(np.uint16)[:, :, np.newaxis]

        # Film Grain
        use_film_grain = settings.get('use_film_grain', False)
        grain_strength = settings.get('grain_strength', 25)
        self.grain_pool = []
        if use_film_grain and grain_strength > 0:
            grain_rng = np.random.default_rng(seed=7)
            for _ in range(60):
                noise_raw = grain_rng.integers(-grain_strength, grain_strength + 1, (h, w, 3), dtype=np.int16)
                self.grain_pool.append((np.clip(noise_raw, 0, 255).astype(np.uint8), np.clip(-noise_raw, 0, 255).astype(np.uint8)))

        self.use_rgb_split = settings.get('use_rgb_split', False)
        self.rgb_split_amount = settings.get('rgb_split_amount', 5)

        use_screen_shake = settings.get('use_screen_shake', False)
        shake_strength = settings.get('shake_strength', 8)
        self.shake_offsets = []
        if use_screen_shake and shake_strength > 0:
            rng = random.Random(42)
            self.shake_offsets = [(rng.randint(-shake_strength, shake_strength), rng.randint(-shake_strength, shake_strength)) for _ in range(6000)]

        self.use_progress_bar = settings.get('use_progress_bar', False)
        pb_colors = {'red': (220, 60, 60), 'white': (255, 255, 255), 'yellow': (255, 220, 0), 'cyan': (0, 210, 210)}
        self.pb_color = pb_colors.get(settings.get('progress_bar_color', 'red'), (220, 60, 60))
        self.pb_bar_h = max(4, int(h * 0.008))
        self.pb_bar_y = h - self.pb_bar_h
        
        # Color & Mirror (GPU filters in output, done in python for preview!)
        self.use_brightness = settings.get('use_brightness', False)
        self.brightness = settings.get('brightness', 0)
        self.use_contrast = settings.get('use_contrast', False)
        self.contrast = settings.get('contrast', 1.0)
        self.use_saturation = settings.get('use_saturation', False)
        self.saturation = settings.get('saturation', 1.0)
        
        self._any_effect_active = any([
            self.static_scale_only or self.animated_scale,
            self.overlay_alpha is not None,
            self.overlay2_alpha is not None,
            self.logo_alpha is not None,
            self.sub_alpha is not None,
            self.part_particles is not None,
            self.vignette_lut is not None,
            bool(self.grain_pool),
            self.use_rgb_split and self.rgb_split_amount > 0,
            bool(self.shake_offsets),
            self.use_progress_bar,
            self.is_preview and self.mirror_video,
            self.is_preview and (self.use_brightness or self.use_contrast),
            self.is_preview and (self.use_saturation and self.saturation != 1.0)
        ])

    def _render_subtitle_bitmap(self, text):
        """Render subtitle text to a bitmap overlay. Called dynamically per-cue."""
        if not text:
            self.sub_alpha = None
            self.sub_rgb = None
            self.last_cue_text = None
            return

        try:
            sub_color_rgb = tuple(int(self.sub_color_hex[i:i+2], 16) for i in (0, 2, 4)) + (255,)
            sub_bg_rgb = tuple(int(self.sub_bg_hex[i:i+2], 16) for i in (0, 2, 4))
            sub_bg_rgba = sub_bg_rgb + (int(255 * self.sub_bg_opacity),)
        except Exception:
            sub_color_rgb = (255, 255, 255, 255)
            sub_bg_rgba = (0, 0, 0, 128)

        wrapped_text = text
        try:
            from video_engine import _wrap_subtitle_text
            sub_width = self.settings.get('subtitle_width', 0.90)
            usable_w = self.w * sub_width
            avg_char_px = self.sub_font_size * 0.42
            max_chars = max(10, int(usable_w / avg_char_px))
            wrapped_text = _wrap_subtitle_text(text, max_chars)
        except Exception:
            pass

        preview_font_size = max(10, self.sub_font_size)
        
        has_cjk = any('\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7af' or '\uff00' <= c <= '\uffef' for c in wrapped_text)
        # Always try the selected font first
        fonts_to_try = [f"{self.sub_font_name.lower()}.ttf"]
        if has_cjk:
            fonts_to_try.extend(["meiryo.ttc", "msgothic.ttc", "msyh.ttc", "malgun.ttf"])
        fonts_to_try.extend(["arialbd.ttf", "arial.ttf"])
        
        font = None
        for fn in fonts_to_try:
            try:
                font = ImageFont.truetype(fn, preview_font_size)
                break
            except IOError:
                continue
        if font is None:
            font = ImageFont.load_default()

        temp_img = Image.new('RGB', (1, 1))
        draw_temp = ImageDraw.Draw(temp_img)
        if hasattr(draw_temp, 'textbbox'):
            bbox = draw_temp.textbbox((0, 0), wrapped_text, font=font, spacing=self.sub_line_spacing)
            lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        else:
            lw, lh = draw_temp.textsize(wrapped_text, font=font, spacing=self.sub_line_spacing)

        pad_x = int(max(5, preview_font_size * 0.15)) + self.sub_padding
        pad_y = self.sub_padding
        box_w = lw + pad_x * 2
        box_h = lh + pad_y * 2

        img_pil = Image.new('RGBA', (box_w, box_h), sub_bg_rgba)
        draw = ImageDraw.Draw(img_pil)
        stroke_w = max(1, preview_font_size // 25)
        draw.text((pad_x, pad_y), wrapped_text, font=font, fill=sub_color_rgb,
                   stroke_width=stroke_w, stroke_fill=(0, 0, 0, 200),
                   spacing=self.sub_line_spacing)

        sub_rgba = np.array(img_pil)
        self.sub_rgb = sub_rgba[:, :, :3]
        self.sub_alpha = (sub_rgba[:, :, 3] / 255.0)[:, :, np.newaxis]

        margin_v = max(getattr(self, 'sub_min_margin', 10), int(self.h * (1.0 - self.sub_y_norm)))
        self.sub_x = max(0, (self.w - box_w) // 2)
        self.sub_y = max(0, self.h - box_h - margin_v)
        self.sub_w = box_w
        self.sub_h = box_h
        self.last_cue_text = text

    def process_frame(self, orig_frame, t, final_duration=1.0):
        if orig_frame is None: return None
        
        if getattr(self, 'is_preview', False) and getattr(self, 'mirror_video', False):
            if not getattr(self, 'keep_hook', True) or getattr(self, 'hook_duration', 3.0) <= 0 or (t >= getattr(self, 'hook_duration', 3.0)):
                orig_frame = cv2.flip(orig_frame, 1)

        # 1. Aspect Ratio Fit
        if self.target_ratio_setting != 'Original':
            if self.fit_mode == 'Crop to Fill':
                cropped = orig_frame[self.crop_y1:self.crop_y1+self.crop_h, self.crop_x1:self.crop_x1+self.crop_w]
                img = cv2.resize(cropped, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
            else:
                if getattr(self, 'bg_image', None) is not None:
                    img = self.bg_image.copy()
                elif self.fit_mode == 'Fit (Black Bars)':
                    img = np.zeros((self.h, self.w, 3), dtype=np.uint8)
                else: 
                    bg_frame = orig_frame[self.bg_y1:self.bg_y1+self.bg_crop_h, self.bg_x1:self.bg_x1+self.bg_crop_w]
                    small_w, small_h = max(2, self.w // 8), max(2, self.h // 8)
                    bg_small = cv2.resize(bg_frame, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
                    ksize = max(3, (self.blur_intensity // 8) | 1)
                    bg_blurred = cv2.GaussianBlur(bg_small, (ksize, ksize), 0)
                    bg_blurred = cv2.convertScaleAbs(bg_blurred, alpha=0.6, beta=0)
                    img = cv2.resize(bg_blurred, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
                
                fg_frame = cv2.resize(orig_frame, (self.fg_w, self.fg_h), interpolation=cv2.INTER_LINEAR)
                img[self.fg_y:self.fg_y+self.fg_h, self.fg_x:self.fg_x+self.fg_w] = fg_frame
        else:
            img = orig_frame.copy()

        if not self._any_effect_active:
            return img

        # 2. Effects
        
        # Scale
        scale_s = 1.0
        if self.static_scale_only: scale_s = self.ai_scale_base
        elif self.animated_scale:
            cycle_t = t % self.scale_cycle
            if self.scale_trans < 0.001: scale_s = self.ai_scale_zoom if cycle_t < self.scale_zoom_hold else self.ai_scale_base
            else:
                p1_end = self.scale_zoom_hold
                p2_end = p1_end + self.scale_trans
                p3_end = p2_end + self.scale_normal
                if cycle_t < p1_end: scale_s = self.ai_scale_zoom
                elif cycle_t < p2_end:
                    ramp = (cycle_t - p1_end) / self.scale_trans
                    eased = ramp * (1.0 - self.scale_easing) + math.sin(math.pi * ramp / 2) * self.scale_easing
                    scale_s = self.ai_scale_zoom + (self.ai_scale_base - self.ai_scale_zoom) * eased
                elif cycle_t < p3_end: scale_s = self.ai_scale_base
                else:
                    ramp = (cycle_t - p3_end) / self.scale_trans
                    eased = ramp * (1.0 - self.scale_easing) + math.sin(math.pi * ramp / 2) * self.scale_easing
                    scale_s = self.ai_scale_base + (self.ai_scale_zoom - self.ai_scale_base) * eased

        if abs(scale_s - 1.0) > 0.001:
            fh, fw = img.shape[:2]
            if scale_s > 1.0:
                new_w = max(1, min(int(fw / scale_s), fw)); new_h = max(1, min(int(fh / scale_s), fh))
                base_x1 = (fw - new_w) / 2.0
                base_y1 = (fh - new_h) / 2.0
                x1 = int(base_x1 + self.pan_x * base_x1)
                y1 = int(base_y1 + self.pan_y * base_y1)
                # Clamp coordinates to ensure we don't go out of bounds
                x1 = max(0, min(x1, fw - new_w))
                y1 = max(0, min(y1, fh - new_h))
                img = cv2.resize(img[y1:y1+new_h, x1:x1+new_w], (fw, fh), interpolation=cv2.INTER_LINEAR)
            else:
                new_w = max(1, int(fw * scale_s)); new_h = max(1, int(fh * scale_s))
                shrunk = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
                padded = np.zeros((fh, fw, 3), dtype=np.uint8)
                padded[(fh-new_h)//2:(fh-new_h)//2+new_h, (fw-new_w)//2:(fw-new_w)//2+new_w] = shrunk
                img = padded

        # Image Logo
        if self.logo_alpha is not None:
            y1, y2 = self.logo_y, self.logo_y + self.logo_h
            x1, x2 = self.logo_x, self.logo_x + self.logo_w
            y1 = max(0, y1); y2 = min(img.shape[0], y2)
            x1 = max(0, x1); x2 = min(img.shape[1], x2)
            bh = y2 - y1; bw = x2 - x1
            if bh > 0 and bw > 0:
                roi = img[y1:y2, x1:x2]
                alpha_slice = self.logo_alpha[0:bh, 0:bw]
                rgb_slice = self.logo_rgb[0:bh, 0:bw]
                img[y1:y2, x1:x2] = (roi * (1.0 - alpha_slice) + rgb_slice * alpha_slice).astype(np.uint8)

        # Watermark
        if self.overlay_alpha is not None:
            y1, y2 = self.overlay_y, self.overlay_y + self.box_h
            x1, x2 = self.overlay_x, self.overlay_x + self.box_w
            y1 = max(0, y1); y2 = min(img.shape[0], y2)
            x1 = max(0, x1); x2 = min(img.shape[1], x2)
            bh = y2 - y1; bw = x2 - x1
            if bh > 0 and bw > 0:
                roi = img[y1:y2, x1:x2]
                alpha_slice = self.overlay_alpha[0:bh, 0:bw]
                rgb_slice = self.overlay_rgb[0:bh, 0:bw]
                img[y1:y2, x1:x2] = (roi * (1.0 - alpha_slice) + rgb_slice * alpha_slice).astype(np.uint8)

        # Text Overlay 2
        if self.overlay2_alpha is not None:
            y1, y2 = self.overlay2_y, self.overlay2_y + self.box2_h
            x1, x2 = self.overlay2_x, self.overlay2_x + self.box2_w
            y1 = max(0, y1); y2 = min(img.shape[0], y2)
            x1 = max(0, x1); x2 = min(img.shape[1], x2)
            bh = y2 - y1; bw = x2 - x1
            if bh > 0 and bw > 0:
                roi = img[y1:y2, x1:x2]
                alpha_slice = self.overlay2_alpha[0:bh, 0:bw]
                rgb_slice = self.overlay2_rgb[0:bh, 0:bw]
                img[y1:y2, x1:x2] = (roi * (1.0 - alpha_slice) + rgb_slice * alpha_slice).astype(np.uint8)

        # Subtitle Proxy (Real-time)
        if self.settings.get('use_subtitles', False):
            # Find the cue for the current time t
            # t is the post-trim filter time used by FFmpeg before setpts.
            srt_t = self.trim_start + (t * self.subtitle_speed_mult)
            
            current_text = None
            for cue in self.cues:
                if cue["start"] <= srt_t <= cue["end"]:
                    current_text = cue["text"]
                    break
            
            if current_text != self.last_cue_text:
                self._render_subtitle_bitmap(current_text)

        if self.sub_alpha is not None:
            y1, y2 = self.sub_y, self.sub_y + self.sub_h
            x1, x2 = self.sub_x, self.sub_x + self.sub_w
            y1 = max(0, y1); y2 = min(img.shape[0], y2)
            x1 = max(0, x1); x2 = min(img.shape[1], x2)
            bh = y2 - y1; bw = x2 - x1
            if bh > 0 and bw > 0:
                roi = img[y1:y2, x1:x2]
                alpha_slice = self.sub_alpha[0:bh, 0:bw]
                rgb_slice = self.sub_rgb[0:bh, 0:bw]
                img[y1:y2, x1:x2] = (roi * (1.0 - alpha_slice) + rgb_slice * alpha_slice).astype(np.uint8)

        # Particles
        if self.part_particles is not None:
            pixels_per_sec = self.h * 0.1 * (self.part_speed / 5.0)
            offset_y = int(t * pixels_per_sec) % self.h
            current_layer = self.part_particles[offset_y:offset_y + self.h, :]
            img = cv2.add(img, current_layer)

        # Vignette
        if self.vignette_lut is not None:
            img = (img.astype(np.uint16) * self.vignette_lut >> 8).astype(np.uint8)

        # Film Grain
        if self.grain_pool:
            frame_idx = int(t * 30) % 60
            pos, neg = self.grain_pool[frame_idx]
            img = cv2.subtract(cv2.add(img, pos), neg)

        # RGB Split
        if self.use_rgb_split and self.rgb_split_amount > 0:
            s = self.rgb_split_amount
            img = np.stack([np.roll(img[:, :, 0], -s, axis=1), img[:, :, 1], np.roll(img[:, :, 2], s, axis=1)], axis=2)

        # Shake
        if self.shake_offsets:
            frame_idx = min(int(t * 30), len(self.shake_offsets) - 1)
            dx, dy = self.shake_offsets[frame_idx]
            fh, fw = img.shape[:2]
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            img = cv2.warpAffine(img, M, (fw, fh), borderMode=cv2.BORDER_REFLECT_101)

        # Progress
        if self.use_progress_bar and final_duration > 0:
            bar_w = int(self.w * min(t / final_duration, 1.0))
            img[self.pb_bar_y:self.pb_bar_y + self.pb_bar_h, :bar_w] = self.pb_color

        # 3. GPU Filter Offloads (Done via Python in preview to match FFmpeg post-processing)
        if self.is_preview and (self.use_brightness or self.use_contrast):
            alpha_val = self.contrast if self.use_contrast else 1.0
            beta_val = self.brightness if self.use_brightness else 0
            img = cv2.convertScaleAbs(img, alpha=alpha_val, beta=beta_val)
            
        if self.is_preview and self.use_saturation and self.saturation != 1.0:
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            gray3 = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
            img = cv2.addWeighted(gray3, 1.0 - self.saturation, img, self.saturation, 0)

        return img


class PreviewWorker(QThread):
    new_frame = pyqtSignal(QImage)
    position_changed = pyqtSignal(float)
    duration_changed = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.settings = {}
        self.is_playing = False
        self.is_running = True
        self.cap = None
        self.current_t = 0.0
        self.processor = None
        self.fps = 30.0
        self.total_frames = 0
        self.duration = 0.0
        self.source_duration = 0.0
        self.video_path = None
        self.trim_start = 0.0
        self.trim_end = 0.0
        self.final_speed = 1.0
        self.seek_requested = -1.0

        # Thread-safe flags: set from main thread, consumed inside run()
        self._state_lock = threading.Lock()
        self._pending_video_path = None
        self._pending_settings = None

    # ── Public API (called from main thread) ──────────────────────────────────

    def load_video(self, path):
        """Signal the run() loop to open this video file."""
        with self._state_lock:
            self.is_playing = False
            self._pending_video_path = path
            self._pending_settings = None
        if not self.isRunning():
            self.is_running = True
            self.start()

    def set_settings(self, settings):
        settings = dict(settings or {})
        with self._state_lock:
            self.settings = settings
            self._pending_settings = settings

    def toggle_playback(self):
        self.is_playing = not self.is_playing

    def seek(self, t):
        with self._state_lock:
            self.seek_requested = min(max(t, 0.0), self.duration)

    def stop(self):
        self.is_running = False
        self.quit()
        self.wait()

    # ── Private helpers (called from run() thread only) ───────────────────────

    def _take_pending_video(self):
        with self._state_lock:
            path = self._pending_video_path
            if path is None:
                return None, None
            settings = dict(self.settings)
            self._pending_video_path = None
            self._pending_settings = None
            return path, settings

    def _take_pending_settings(self):
        with self._state_lock:
            if self._pending_settings is None:
                return None
            settings = dict(self._pending_settings)
            self._pending_settings = None
            return settings

    def _take_seek_request(self):
        with self._state_lock:
            seek_t = self.seek_requested
            if seek_t >= 0:
                self.seek_requested = -1.0
            return seek_t

    def _apply_timing_plan(self):
        if not self.video_path:
            return
        try:
            from video_engine import get_timing_plan
            plan = get_timing_plan(self.video_path, self.settings, {'duration': self.source_duration})
        except Exception:
            plan = {
                'trim_start': 0.0,
                'trim_end': self.source_duration,
                'final_duration': self.source_duration,
                'final_speed': 1.0,
            }

        self.trim_start = float(plan.get('trim_start', 0.0) or 0.0)
        self.trim_end = float(plan.get('trim_end', self.source_duration) or self.source_duration)
        self.final_speed = float(plan.get('final_speed', 1.0) or 1.0)
        self.duration = max(0.0, float(plan.get('final_duration', 0.0) or 0.0))
        self.settings = dict(self.settings)
        self.settings['_preview_trim_start'] = self.trim_start

    def _read_frame_at_output_time(self, output_t):
        if not self.cap or not self.cap.isOpened():
            return None, 0.0

        if self.duration > 0:
            output_t = max(0.0, min(output_t, self.duration))
        source_t = self.trim_start + (output_t * self.final_speed)
        source_t = max(self.trim_start, min(source_t, self.trim_end))

        if self.total_frames > 0:
            target_frame = int(source_t * self.fps)
            target_frame = max(0, min(target_frame, self.total_frames - 1))
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        else:
            self.cap.set(cv2.CAP_PROP_POS_MSEC, source_t * 1000.0)

        ret, frame = self.cap.read()
        if not ret:
            return None, source_t - self.trim_start
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), source_t - self.trim_start

    def _open_video(self, path, settings=None):
        """Open a new VideoCapture and rebuild the processor. Must be called from run()."""
        if settings is not None:
            self.settings = dict(settings)
        if self.cap:
            self.cap.release()
            self.cap = None
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            print(f"[Preview] Cannot open: {path}")
            return
        self.cap = cap
        self.fps = cap.get(cv2.CAP_PROP_FPS)
        if self.fps <= 0:
            self.fps = 30.0
        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.source_duration = self.total_frames / self.fps if self.fps else 0.0
        self.video_path = path
        self._apply_timing_plan()
        self.current_t = 0.0
        self.is_playing = False
        self.seek_requested = 0.0  # will show the first frame immediately
        self.duration_changed.emit(self.duration)
        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.processor = EffectProcessor(self.settings, orig_w, orig_h, is_preview=True)

    def _rebuild_processor(self, settings):
        """Rebuild EffectProcessor with new settings. Must be called from run()."""
        if not self.cap or not self.cap.isOpened():
            return
        self.settings = settings
        self._apply_timing_plan()
        if self.current_t > self.duration:
            self.current_t = self.duration
        self.duration_changed.emit(self.duration)
        orig_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.processor = EffectProcessor(self.settings, orig_w, orig_h, is_preview=True)
        if not self.is_playing:
            self.seek_requested = self.current_t  # redraw current frame

    def _emit_frame(self, frame_rgb, effect_t):
        """Apply effects and emit the processed QImage."""
        if self.processor is None:
            return
        try:
            processed = self.processor.process_frame(frame_rgb, effect_t, self.duration)
            ph, pw = processed.shape[:2]
            max_h = 480
            if ph > max_h:
                scale = max_h / ph
                processed = cv2.resize(processed, (int(pw * scale), max_h), interpolation=cv2.INTER_AREA)
            h, w, ch = processed.shape
            # Make the array contiguous and copy bytes so Qt owns the data
            processed = np.ascontiguousarray(processed)
            qimg = QImage(processed.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
            self.new_frame.emit(qimg)
        except Exception as e:
            print(f"[Preview] Frame error: {e}")

    # ── Run loop ──────────────────────────────────────────────────────────────

    def run(self):
        while self.is_running:
            # 1. Handle pending video load (highest priority)
            pending_path, pending_video_settings = self._take_pending_video()
            if pending_path is not None:
                self._open_video(pending_path, pending_video_settings)
                continue  # loop immediately to process seek_requested = 0

            # 2. Handle pending settings change
            pending_settings = self._take_pending_settings()
            if pending_settings is not None:
                self._rebuild_processor(pending_settings)

            # 3. No video → idle
            if not self.cap or not self.cap.isOpened() or self.processor is None:
                time.sleep(0.05)
                continue

            frame_rgb = None
            effect_t = 0.0

            # 4. Seek / scrub
            seek_t = self._take_seek_request()
            if seek_t >= 0:
                frame_rgb, effect_t = self._read_frame_at_output_time(seek_t)
                self.current_t = seek_t
                self.position_changed.emit(self.current_t)

            # 5. Normal playback
            elif self.is_playing:
                if self.duration > 0 and self.current_t >= self.duration:
                    self.is_playing = False
                    self.current_t = self.duration
                    self.position_changed.emit(self.current_t)
                else:
                    frame_rgb, effect_t = self._read_frame_at_output_time(self.current_t)
                    if frame_rgb is not None:
                        self.current_t = min(self.duration, self.current_t + 1.0 / self.fps)
                        self.position_changed.emit(self.current_t)
                    else:
                        self.is_playing = False
                        self.current_t = self.duration
                        self.position_changed.emit(self.current_t)

            # 6. Emit
            if frame_rgb is not None:
                self._emit_frame(frame_rgb, effect_t)

            # 7. Sleep
            if self.is_playing:
                time.sleep(1.0 / self.fps * 0.85)
            else:
                time.sleep(0.03)

        if self.cap:
            self.cap.release()
