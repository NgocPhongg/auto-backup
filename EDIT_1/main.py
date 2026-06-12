import importlib.util
import os
import sys
from PyQt6.QtWidgets import QApplication
from ui_main import MainWindow
from queue_manager import QueueManager
from render_engine import RenderWorker
from preview_engine import PreviewWorker
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt


class AppController:
    def __init__(self, window):
        self.window = window
        self.window.shutdown_callback = self.shutdown
        self.queue_manager = QueueManager(window)
        self.queue_manager.log_signal.connect(self.window.log_msg)
        
        self.window.btn_browse_out.clicked.connect(self.browse_output)
        self.window.btn_open_out.clicked.connect(self.open_output_folder)
        self.window.btn_browse_logo.clicked.connect(self.browse_logo)
        self.window.btn_browse_subtitle.clicked.connect(self.browse_subtitle)
        self.window.btn_browse_bg_music.clicked.connect(self.browse_bg_music)
        self.window.btn_browse_bg_image.clicked.connect(self.browse_bg_image)
        self.window.btn_start_render.clicked.connect(self.start_render)
        self.window.btn_save_preset.clicked.connect(self.save_preset)
        self.window.btn_load_preset.clicked.connect(self.load_preset)
        self.window.btn_watermark_color.clicked.connect(self.choose_watermark_color)
        self.window.btn_text2_color.clicked.connect(self.choose_text2_color)
        self.window.btn_sub_color.clicked.connect(self.choose_sub_color)
        self.window.btn_sub_bg_color.clicked.connect(self.choose_sub_bg_color)
        
        self.render_worker = None
        self.preview_worker = PreviewWorker()
        self._last_preview_qimg = None
        
        # Connect Preview Signals
        self.preview_worker.new_frame.connect(self.update_preview_frame)
        self.preview_worker.duration_changed.connect(self.update_preview_duration)
        self.preview_worker.position_changed.connect(self.update_preview_position)
        self.window.lbl_preview.resized.connect(self._rescale_cached_preview_frame)
        
        self.window.btn_play_pause.clicked.connect(self.toggle_preview_playback)
        self.window.slider_seek.sliderReleased.connect(self.seek_preview)
        
        # Connect Queue selection to preview
        self.window.queue_list.itemSelectionChanged.connect(self.on_queue_selection)
        
        # Connect all UI changes to update preview settings
        self.hook_ui_for_preview()
        
        # Drag position state (normalized 0.0-1.0)
        self._logo_x_norm = 0.85
        self._logo_y_norm = 0.85
        self._text_x_norm = 0.5
        self._text_y_norm = 0.9
        self._text2_x_norm = 0.5
        self._text2_y_norm = 0.2
        self._sub_x_norm = 0.5
        self._sub_y_norm = 0.9
        self._active_preview_drag_target = None
        
        # Wire drag signals
        self.window.lbl_preview.logo_dragged.connect(self._on_logo_dragged)
        self.window.lbl_preview.text_dragged.connect(self._on_text_dragged)
        self.window.lbl_preview.text2_dragged.connect(self._on_text2_dragged)
        self.window.lbl_preview.subtitle_dragged.connect(self._on_subtitle_dragged)
        
        # Toggle drag hints  
        self.window.chk_use_logo.stateChanged.connect(self._update_drag_ui)
        self.window.chk_use_watermark.stateChanged.connect(self._update_drag_ui)
        self.window.chk_use_text2.stateChanged.connect(self._update_drag_ui)
        self.window.chk_use_subtitles.stateChanged.connect(self._update_drag_ui)
        self.window.txt_watermark.textChanged.connect(self._on_watermark_text_changed)
        self.window.txt_text2.textChanged.connect(self._on_text2_text_changed)
        self.window.slider_watermark_size.valueChanged.connect(lambda _value: self._set_active_preview_drag_target('text'))
        self.window.slider_text2_size.valueChanged.connect(lambda _value: self._set_active_preview_drag_target('text2'))
        self.window.txt_logo_path.textChanged.connect(lambda _text: self._set_active_preview_drag_target('logo'))
        self.window.cmb_logo_pos.currentTextChanged.connect(self._on_logo_pos_changed)
        self.window.slider_logo_scale.valueChanged.connect(lambda _value: self._set_active_preview_drag_target('logo'))
        self.window.slider_watermark_pos_y.valueChanged.connect(self._on_text_y_slider_changed)
        self.window.slider_text2_pos_y.valueChanged.connect(self._on_text2_y_slider_changed)
        self.window.slider_sub_pos_y.valueChanged.connect(self._on_subtitle_y_slider_changed)
        self._refresh_logo_drag_pos_label()
        self._refresh_text_drag_pos_label()
        self._refresh_text2_drag_pos_label()
        self._refresh_sub_drag_pos_label()
        self._update_drag_ui()
        self.check_dependencies()


        
    def _update_drag_ui(self):
        logo_on = self.window.chk_use_logo.isChecked()
        text_on = self.window.chk_use_watermark.isChecked()
        text2_on = self.window.chk_use_text2.isChecked()
        sub_on = self.window.chk_use_subtitles.isChecked()
        self.window.lbl_preview.set_enabled_overlays(logo_on, text_on, sub_on, text2_on)
        if self._active_preview_drag_target and not self._is_drag_target_enabled(self._active_preview_drag_target):
            self._active_preview_drag_target = None
        if self._active_preview_drag_target is None:
            if text_on and not logo_on and not text2_on and not sub_on:
                self._active_preview_drag_target = 'text'
            elif text2_on and not logo_on and not text_on and not sub_on:
                self._active_preview_drag_target = 'text2'
            elif logo_on and not text_on and not text2_on and not sub_on:
                self._active_preview_drag_target = 'logo'
            elif sub_on and not logo_on and not text_on and not text2_on:
                self._active_preview_drag_target = 'subtitle'
        self.window.lbl_preview.set_active_drag_target(self._active_preview_drag_target)
        self._refresh_drag_hint()

    def _is_drag_target_enabled(self, target):
        if target == 'logo':
            return self.window.chk_use_logo.isChecked()
        if target == 'text':
            return self.window.chk_use_watermark.isChecked()
        if target == 'text2':
            return self.window.chk_use_text2.isChecked()
        if target == 'subtitle':
            return self.window.chk_use_subtitles.isChecked()
        return False

    def _set_active_preview_drag_target(self, target):
        if not self._is_drag_target_enabled(target):
            return
        self._active_preview_drag_target = target
        self.window.lbl_preview.set_active_drag_target(target)
        self._refresh_drag_hint()

    def _refresh_drag_hint(self):
        labels = {
            'logo': "Active drag: Image Logo Overlay",
            'text': "Active drag: Watermark / Text Overlay",
            'text2': "Active drag: Text Overlay 2",
            'subtitle': "Active drag: Subtitle Overlay",
        }
        if self._active_preview_drag_target and self._is_drag_target_enabled(self._active_preview_drag_target):
            self.window.lbl_drag_hint.setText(labels[self._active_preview_drag_target])
            self.window.lbl_drag_hint.setVisible(True)
            return
        if self.window.chk_use_logo.isChecked() or self.window.chk_use_watermark.isChecked() or self.window.chk_use_text2.isChecked() or self.window.chk_use_subtitles.isChecked():
            self.window.lbl_drag_hint.setText("Click directly on the visible overlay in preview to drag it")
            self.window.lbl_drag_hint.setVisible(True)
        else:
            self.window.lbl_drag_hint.setVisible(False)

    def _refresh_logo_drag_pos_label(self):
        if self.window.cmb_logo_pos.currentText() == "Manual (Drag)":
            self.window.lbl_logo_drag_pos.setText(
                f"Position: x={self._logo_x_norm:.2f} y={self._logo_y_norm:.2f} (drag on preview to move)"
            )
        else:
            self.window.lbl_logo_drag_pos.setText(
                f"Position: preset {self.window.cmb_logo_pos.currentText()}"
            )

    def _refresh_text_drag_pos_label(self):
        self.window.lbl_text_drag_pos.setText(
            f"Position: x={self._text_x_norm:.2f} y={self._text_y_norm:.2f} (drag on preview to move)"
        )

    def _refresh_text2_drag_pos_label(self):
        self.window.lbl_text2_drag_pos.setText(
            f"Position: x={self._text2_x_norm:.2f} y={self._text2_y_norm:.2f} (drag on preview to move)"
        )

    def _refresh_sub_drag_pos_label(self):
        self.window.lbl_sub_drag_pos.setText(
            f"Position: y={self._sub_y_norm:.2f} (drag on preview to move)"
        )

    def _on_logo_pos_changed(self, _text):
        self._set_active_preview_drag_target('logo')
        self._refresh_logo_drag_pos_label()

    def _normalize_watermark_text(self, text):
        text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\\n", "\n")
        lines = text.split("\n")
        if len(lines) > 2:
            text = "\n".join(lines[:2])
        return text

    def _on_watermark_text_changed(self):
        editor = self.window.txt_watermark
        text = editor.toPlainText()
        normalized = self._normalize_watermark_text(text)
        if normalized != text:
            cursor_pos = min(editor.textCursor().position(), len(normalized))
            editor.blockSignals(True)
            editor.setPlainText(normalized)
            cursor = editor.textCursor()
            cursor.setPosition(cursor_pos)
            editor.setTextCursor(cursor)
            editor.blockSignals(False)
        self._set_active_preview_drag_target('text')
        self.update_preview_settings_sync()

    def _on_text2_text_changed(self):
        editor = self.window.txt_text2
        text = editor.toPlainText()
        normalized = self._normalize_watermark_text(text)
        if normalized != text:
            cursor_pos = min(editor.textCursor().position(), len(normalized))
            editor.blockSignals(True)
            editor.setPlainText(normalized)
            cursor = editor.textCursor()
            cursor.setPosition(cursor_pos)
            editor.setTextCursor(cursor)
            editor.blockSignals(False)
        self._set_active_preview_drag_target('text2')
        self.update_preview_settings_sync()

    def check_dependencies(self):
        required_modules = {
            "PyQt6": "PyQt6",
            "OpenCV": "cv2",
            "NumPy": "numpy",
            "MoviePy": "moviepy",
            "imageio-ffmpeg": "imageio_ffmpeg",
        }
        missing = [
            label
            for label, module_name in required_modules.items()
            if importlib.util.find_spec(module_name) is None
        ]
        if missing:
            self.window.log_msg("Missing dependency: " + ", ".join(missing))
        else:
            self.window.log_msg("Dependency check passed.")

        try:
            from imageio_ffmpeg import get_ffmpeg_exe

            ffmpeg_path = get_ffmpeg_exe()
            if os.path.exists(ffmpeg_path):
                self.window.log_msg(f"FFmpeg ready: {ffmpeg_path}")
            else:
                self.window.log_msg("FFmpeg path was returned but the file does not exist.")
        except Exception as exc:
            self.window.log_msg(f"FFmpeg check failed: {exc}")

    def shutdown(self):
        if self.render_worker and self.render_worker.isRunning():
            self.window.log_msg("Stopping render worker before exit...")
            self.render_worker.cancel()
            self.render_worker.wait(3000)
        if self.preview_worker and self.preview_worker.isRunning():
            self.preview_worker.stop()
        if self.queue_manager:
            self.queue_manager.shutdown()



    def _on_logo_dragged(self, nx: float, ny: float):
        self._logo_x_norm = self._clamp_norm(nx)
        self._logo_y_norm = self._clamp_norm(ny)
        self._set_active_preview_drag_target('logo')
        # Auto-switch dropdown to Manual
        idx = self.window.cmb_logo_pos.findText("Manual (Drag)")
        if idx >= 0:
            self.window.cmb_logo_pos.blockSignals(True)
            self.window.cmb_logo_pos.setCurrentIndex(idx)
            self.window.cmb_logo_pos.blockSignals(False)
        self._refresh_logo_drag_pos_label()
        self.update_preview_settings_sync()

    def _on_text_dragged(self, nx: float, ny: float):
        self._text_x_norm = self._clamp_norm(nx)
        self._text_y_norm = self._clamp_norm(ny)
        self._set_active_preview_drag_target('text')
        self._refresh_text_drag_pos_label()
        
        # Sync slider
        self._sync_text_slider_from_norm()
        
        self.update_preview_settings_sync()

    def _on_text2_dragged(self, nx: float, ny: float):
        self._text2_x_norm = self._clamp_norm(nx)
        self._text2_y_norm = self._clamp_norm(ny)
        self._set_active_preview_drag_target('text2')
        self._refresh_text2_drag_pos_label()

        self._sync_text2_slider_from_norm()

        self.update_preview_settings_sync()

    def _on_subtitle_dragged(self, nx: float, ny: float):
        self._sub_x_norm = self._clamp_norm(nx)
        self._sub_y_norm = self._clamp_norm(ny)
        self._set_active_preview_drag_target('subtitle')
        # Sync slider (1.0 ny means 100%, but we use 1-99 range)
        self._sync_subtitle_slider_from_norm()
        self._refresh_sub_drag_pos_label()
        self.update_preview_settings_sync()

    def _clamp_norm(self, value):
        return max(0.0, min(1.0, float(value)))

    def _sync_text_slider_from_norm(self):
        val = int(round(self._text_y_norm * 100))
        val = max(self.window.slider_watermark_pos_y.minimum(), min(val, self.window.slider_watermark_pos_y.maximum()))
        self.window.slider_watermark_pos_y.blockSignals(True)
        self.window.slider_watermark_pos_y.setValue(val)
        self.window.slider_watermark_pos_y.blockSignals(False)
        self.window.val_watermark_pos_y.setText(f"{val}%")

    def _sync_text2_slider_from_norm(self):
        val = int(round(self._text2_y_norm * 100))
        val = max(self.window.slider_text2_pos_y.minimum(), min(val, self.window.slider_text2_pos_y.maximum()))
        self.window.slider_text2_pos_y.blockSignals(True)
        self.window.slider_text2_pos_y.setValue(val)
        self.window.slider_text2_pos_y.blockSignals(False)
        self.window.val_text2_pos_y.setText(f"{val}%")

    def _sync_subtitle_slider_from_norm(self):
        val = int(round(self._sub_y_norm * 100))
        val = max(self.window.slider_sub_pos_y.minimum(), min(val, self.window.slider_sub_pos_y.maximum()))
        self.window.slider_sub_pos_y.blockSignals(True)
        self.window.slider_sub_pos_y.setValue(val)
        self.window.slider_sub_pos_y.blockSignals(False)
        self.window.val_sub_pos_y.setText(f"{val}%")

    def _on_text_y_slider_changed(self, value):
        self._text_y_norm = self._clamp_norm(value / 100.0)
        self._set_active_preview_drag_target('text')
        self.window.val_watermark_pos_y.setText(f"{value}%")
        self._refresh_text_drag_pos_label()
        self.update_preview_settings_sync()

    def _on_text2_y_slider_changed(self, value):
        self._text2_y_norm = self._clamp_norm(value / 100.0)
        self._set_active_preview_drag_target('text2')
        self.window.val_text2_pos_y.setText(f"{value}%")
        self._refresh_text2_drag_pos_label()
        self.update_preview_settings_sync()

    def _on_subtitle_y_slider_changed(self, value):
        self._sub_y_norm = self._clamp_norm(value / 100.0)
        self._set_active_preview_drag_target('subtitle')
        self.window.val_sub_pos_y.setText(f"{value}%")
        self._refresh_sub_drag_pos_label()
        self.update_preview_settings_sync()

    def hook_ui_for_preview(self):
        """Bind all settings widgets to update the preview immediately"""
        controls = [
            (self.window.chk_use_duration, 'stateChanged'), (self.window.slider_duration, 'valueChanged'),
            (self.window.chk_use_ai_ratio, 'stateChanged'), (self.window.slider_ai_ratio, 'valueChanged'),
            (self.window.chk_scale_normal, 'stateChanged'), (self.window.slider_scale_cycle, 'valueChanged'),
            (self.window.chk_scale_zoom, 'stateChanged'), (self.window.slider_scale_start, 'valueChanged'),
            (self.window.chk_scale_zoom_ratio, 'stateChanged'), (self.window.slider_scale_zoom_ratio, 'valueChanged'),
            (self.window.slider_scale_easing, 'valueChanged'), (self.window.slider_pan_x, 'valueChanged'),
            (self.window.slider_pan_y, 'valueChanged'), (self.window.chk_mirror, 'stateChanged'),
            (self.window.chk_use_video_speed, 'stateChanged'), (self.window.slider_speed, 'valueChanged'),
            (self.window.chk_use_pitch, 'stateChanged'), (self.window.slider_pitch, 'valueChanged'),
            (self.window.chk_use_volume, 'stateChanged'), (self.window.slider_volume, 'valueChanged'),
            (self.window.chk_use_watermark, 'stateChanged'),
            (self.window.slider_watermark_size, 'valueChanged'),
            (self.window.chk_use_text2, 'stateChanged'),
            (self.window.slider_text2_size, 'valueChanged'),
            (self.window.chk_use_logo, 'stateChanged'), (self.window.txt_logo_path, 'textChanged'),
            (self.window.cmb_logo_pos, 'currentTextChanged'), (self.window.slider_logo_scale, 'valueChanged'),
            (self.window.slider_logo_opacity, 'valueChanged'),
            (self.window.chk_use_subtitles, 'stateChanged'), (self.window.txt_subtitle_path, 'textChanged'),
            (self.window.cmb_sub_font, 'currentTextChanged'), (self.window.slider_sub_bg_opacity, 'valueChanged'),
            (self.window.slider_sub_font_size, 'valueChanged'),
            (self.window.slider_sub_speed, 'valueChanged'),
            (self.window.slider_sub_width, 'valueChanged'),
            (self.window.slider_sub_line_spacing, 'valueChanged'), (self.window.slider_sub_padding, 'valueChanged'),
            (self.window.chk_use_bg_music, 'stateChanged'), (self.window.txt_bg_music_path, 'textChanged'),
            (self.window.slider_bgm_volume, 'valueChanged'), (self.window.slider_bgm_speed, 'valueChanged'),
            (self.window.chk_use_bg_image, 'stateChanged'), (self.window.txt_bg_image_path, 'textChanged'),
            (self.window.cmb_bg_image_fit, 'currentTextChanged'),
            (self.window.cmb_ratio, 'currentTextChanged'), (self.window.cmb_fit_mode, 'currentTextChanged'),
            (self.window.slider_blur, 'valueChanged')
        ]
        for widget, signal_name in controls:
            getattr(widget, signal_name).connect(self.update_preview_settings_sync)
            
    def update_preview_settings_sync(self, *args):
        # Forward the settings dict to the preview thread
        self.preview_worker.set_settings(self.get_render_settings())

    def on_queue_selection(self):
        items = self.window.queue_list.selectedItems()
        if not items:
            return
        
        # Primary: get filepath from the custom widget stored in the list item
        list_item = items[0]
        item_widget = self.window.queue_list.itemWidget(list_item)
        
        filepath = None
        if item_widget and hasattr(item_widget, 'filepath'):
            filepath = item_widget.filepath
        else:
            # Fallback: search through queue_manager by item reference
            for q in self.queue_manager.get_items():
                if q['item'] is list_item:
                    filepath = q['path']
                    break
        
        if not filepath:
            self.window.log_msg("Preview: Could not determine video path from selection.")
            return
        
        self.preview_worker.set_settings(self.get_render_settings())
        self.preview_worker.load_video(filepath)  # starts thread internally if not running
                
    def toggle_preview_playback(self):
        self.preview_worker.toggle_playback()
        self.window.btn_play_pause.setText("Pause" if self.preview_worker.is_playing else "Play")
        
    def seek_preview(self):
        pct = self.window.slider_seek.value() / 1000.0
        t = pct * self.preview_worker.duration
        self.preview_worker.seek(t)
        
    def update_preview_frame(self, qimg):
        self._last_preview_qimg = qimg.copy()
        self._rescale_cached_preview_frame()

    def _rescale_cached_preview_frame(self):
        if self._last_preview_qimg is None or self._last_preview_qimg.isNull():
            return
        pixmap = QPixmap.fromImage(self._last_preview_qimg)
        # Scaled to fit label while keeping aspect ratio
        w = max(1, self.window.lbl_preview.width())
        h = max(1, self.window.lbl_preview.height())
        scaled = pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.window.lbl_preview.setPixmap(scaled)
        self._update_preview_overlay_bounds()

    def _update_preview_overlay_bounds(self):
        processor = getattr(self.preview_worker, 'processor', None)
        if processor is None:
            self.window.lbl_preview.clear_overlay_bounds()
            return

        frame_w = int(getattr(processor, 'w', 0) or 0)
        frame_h = int(getattr(processor, 'h', 0) or 0)
        if frame_w <= 0 or frame_h <= 0:
            self.window.lbl_preview.clear_overlay_bounds()
            return

        logo_rect = None
        if self.window.chk_use_logo.isChecked() and getattr(processor, 'logo_alpha', None) is not None:
            logo_rect = (
                int(getattr(processor, 'logo_x', 0)),
                int(getattr(processor, 'logo_y', 0)),
                int(getattr(processor, 'logo_w', 0)),
                int(getattr(processor, 'logo_h', 0)),
            )

        text_rect = None
        if self.window.chk_use_watermark.isChecked() and getattr(processor, 'overlay_alpha', None) is not None:
            text_rect = (
                int(getattr(processor, 'overlay_x', 0)),
                int(getattr(processor, 'overlay_y', 0)),
                int(getattr(processor, 'box_w', 0)),
                int(getattr(processor, 'box_h', 0)),
            )

        text2_rect = None
        if self.window.chk_use_text2.isChecked() and getattr(processor, 'overlay2_alpha', None) is not None:
            text2_rect = (
                int(getattr(processor, 'overlay2_x', 0)),
                int(getattr(processor, 'overlay2_y', 0)),
                int(getattr(processor, 'box2_w', 0)),
                int(getattr(processor, 'box2_h', 0)),
            )

        subtitle_rect = None
        if self.window.chk_use_subtitles.isChecked() and getattr(processor, 'sub_alpha', None) is not None:
            subtitle_rect = (
                int(getattr(processor, 'sub_x', 0)),
                int(getattr(processor, 'sub_y', 0)),
                int(getattr(processor, 'sub_w', 0)),
                int(getattr(processor, 'sub_h', 0)),
            )

        self.window.lbl_preview.set_overlay_bounds(
            frame_w,
            frame_h,
            logo_rect=logo_rect,
            text_rect=text_rect,
            text2_rect=text2_rect,
            subtitle_rect=subtitle_rect,
        )
        
    def update_preview_duration(self, duration):
        pass # Handle in position update to format strings
        
    def update_preview_position(self, t):
        dur = self.preview_worker.duration
        if dur > 0 and not self.window.slider_seek.isSliderDown():
            self.window.slider_seek.setValue(int((t / dur) * 1000))
        def format_t(secs):
            m = int(secs) // 60
            s = int(secs) % 60
            return f"{m:02d}:{s:02d}"
        self.window.lbl_time.setText(f"{format_t(t)} / {format_t(dur)}")
        
    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self.window, "Select Output Folder")
        if folder:
            self.window.txt_output_folder.setText(folder)

    def open_output_folder(self):
        folder = self.window.txt_output_folder.text().strip()
        if not folder:
            self.window.log_msg("Output folder is empty.")
            return
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
        except Exception as exc:
            self.window.log_msg(f"Cannot open output folder: {exc}")
            
    def browse_logo(self):
        filename, _ = QFileDialog.getOpenFileName(self.window, "Select Logo Image", "", "Image Files (*.png *.jpg *.jpeg *.bmp)")
        if filename:
            self.window.txt_logo_path.setText(filename)
            
    def browse_subtitle(self):
        filename, _ = QFileDialog.getOpenFileName(self.window, "Select Subtitle File", "", "Subtitle Files (*.srt *.ass)")
        if filename:
            self.window.txt_subtitle_path.setText(filename)
            self.update_preview_settings_sync()
            
    def browse_bg_music(self):
        filename, _ = QFileDialog.getOpenFileName(self.window, "Select Background Music", "", "Audio Files (*.mp3 *.wav *.m4a *.aac *.ogg)")
        if filename:
            self.window.txt_bg_music_path.setText(filename)

    def browse_bg_image(self):
        filename, _ = QFileDialog.getOpenFileName(self.window, "Select Background Image", "", "Image Files (*.png *.jpg *.jpeg *.bmp *.webp)")
        if filename:
            self.window.txt_bg_image_path.setText(filename)
            self.update_preview_settings_sync()
            
    def choose_watermark_color(self):
        from PyQt6.QtWidgets import QColorDialog
        from PyQt6.QtGui import QColor
        color = QColorDialog.getColor(QColor(self.window.watermark_color), self.window, "Select Text Color")
        if color.isValid():
            self.window.watermark_color = color.name()
            text_col = "#000000" if color.lightness() > 128 else "#ffffff"
            self.window.btn_watermark_color.setStyleSheet(f"background-color: {self.window.watermark_color}; color: {text_col}; font-weight: bold;")
            self.window.btn_watermark_color.setText(self.window.watermark_color)
            self.update_preview_settings_sync()

    def choose_text2_color(self):
        from PyQt6.QtWidgets import QColorDialog
        from PyQt6.QtGui import QColor
        color = QColorDialog.getColor(QColor(self.window.text2_color), self.window, "Select Text 2 Color")
        if color.isValid():
            self.window.text2_color = color.name()
            text_col = "#000000" if color.lightness() > 128 else "#ffffff"
            self.window.btn_text2_color.setStyleSheet(f"background-color: {self.window.text2_color}; color: {text_col}; font-weight: bold;")
            self.window.btn_text2_color.setText(self.window.text2_color)
            self.update_preview_settings_sync()

    def choose_sub_color(self):
        from PyQt6.QtWidgets import QColorDialog
        from PyQt6.QtGui import QColor
        color = QColorDialog.getColor(QColor(self.window.subtitle_color), self.window, "Select Subtitle Color")
        if color.isValid():
            self.window.subtitle_color = color.name()
            text_col = "#000000" if color.lightness() > 128 else "#ffffff"
            self.window.btn_sub_color.setStyleSheet(f"background-color: {self.window.subtitle_color}; color: {text_col}; font-weight: bold;")
            self.window.btn_sub_color.setText(self.window.subtitle_color)
            self.update_preview_settings_sync()

    def choose_sub_bg_color(self):
        from PyQt6.QtWidgets import QColorDialog
        from PyQt6.QtGui import QColor
        color = QColorDialog.getColor(QColor(self.window.subtitle_bg_color), self.window, "Select Subtitle Bg Color")
        if color.isValid():
            self.window.subtitle_bg_color = color.name()
            text_col = "#000000" if color.lightness() > 128 else "#ffffff"
            self.window.btn_sub_bg_color.setStyleSheet(f"background-color: {self.window.subtitle_bg_color}; color: {text_col}; font-weight: bold;")
            self.window.btn_sub_bg_color.setText(self.window.subtitle_bg_color)
            self.update_preview_settings_sync()
            
    def get_ui_values(self):
        """Get pure raw values from UI for presets"""
        return {
            'use_duration': self.window.chk_use_duration.isChecked(),
            'duration': self.window.slider_duration.value(),
            'output_folder': self.window.txt_output_folder.text(),
            'use_ai_ratio': self.window.chk_use_ai_ratio.isChecked(),
            'ai_ratio': self.window.slider_ai_ratio.value(),
            'use_scale_normal': self.window.chk_scale_normal.isChecked(),
            'scale_normal': self.window.slider_scale_cycle.value(),
            'use_scale_zoom': self.window.chk_scale_zoom.isChecked(),
            'scale_zoom_hold': self.window.slider_scale_start.value(),
            'use_scale_zoom_ratio': self.window.chk_scale_zoom_ratio.isChecked(),
            'scale_zoom_ratio': self.window.slider_scale_zoom_ratio.value(),
            'scale_easing': self.window.slider_scale_easing.value(),
            'pan_x': self.window.slider_pan_x.value(),
            'pan_y': self.window.slider_pan_y.value(),
            'max_source': self.window.chk_max_source.isChecked(),
            'scene_gpu': self.window.chk_scene_gpu.isChecked(),
            'im_res': self.window.chk_im_res.isChecked(),
            'keep_hook': self.window.chk_keep_hook.isChecked(),
            'keep_hook_duration': self.window.spin_hook_dur.value(),
            'micro_speed': self.window.chk_micro_speed.isChecked(),
            'mirror_video': self.window.chk_mirror.isChecked(),
            'transition': self.window.slider_transition.value(),
            'date_folder': self.window.chk_date_folder.isChecked(),
            'use_video_speed': self.window.chk_use_video_speed.isChecked(),
            'video_speed': self.window.slider_speed.value(),
            'use_pitch': self.window.chk_use_pitch.isChecked(),
            'pitch_ratio': self.window.slider_pitch.value(),
            'use_volume': self.window.chk_use_volume.isChecked(),
            'volume': self.window.slider_volume.value(),
            'use_watermark': self.window.chk_use_watermark.isChecked(),
            'watermark_text': self._normalize_watermark_text(self.window.txt_watermark.toPlainText()),
            'watermark_size': self.window.slider_watermark_size.value(),
            'watermark_color': self.window.watermark_color,
            'watermark_pos_y': self.window.slider_watermark_pos_y.value(),
            'use_text2': self.window.chk_use_text2.isChecked(),
            'text2_text': self._normalize_watermark_text(self.window.txt_text2.toPlainText()),
            'text2_size': self.window.slider_text2_size.value(),
            'text2_color': self.window.text2_color,
            'text2_pos_y': self.window.slider_text2_pos_y.value(),
            'use_logo': self.window.chk_use_logo.isChecked(),
            'logo_path': self.window.txt_logo_path.text(),
            'logo_pos': self.window.cmb_logo_pos.currentText(),
            'logo_scale': self.window.slider_logo_scale.value(),
            'logo_opacity': self.window.slider_logo_opacity.value(),
            'logo_x_norm': self._logo_x_norm,
            'logo_y_norm': self._logo_y_norm,
            'text_x_norm': self._text_x_norm,
            'text_y_norm': self._text_y_norm,
            'text2_x_norm': self._text2_x_norm,
            'text2_y_norm': self._text2_y_norm,
            'use_subtitles': self.window.chk_use_subtitles.isChecked(),
            'subtitle_path': self.window.txt_subtitle_path.text(),
            'subtitle_font': self.window.cmb_sub_font.currentText(),
            'subtitle_color': self.window.subtitle_color,
            'subtitle_bg_color': self.window.subtitle_bg_color,
            'subtitle_bg_opacity': self.window.slider_sub_bg_opacity.value(),
            'subtitle_font_size': self.window.slider_sub_font_size.value(),
            'subtitle_speed': self.window.slider_sub_speed.value(),
            'subtitle_line_spacing': self.window.slider_sub_line_spacing.value(),
            'subtitle_padding': self.window.slider_sub_padding.value(),
            'subtitle_width': self.window.slider_sub_width.value(),
            'subtitle_pos_y': self.window.slider_sub_pos_y.value(),
            'sub_x_norm': self._sub_x_norm,
            'sub_y_norm': self._sub_y_norm,

            'use_bg_music': self.window.chk_use_bg_music.isChecked(),
            'bg_music_path': self.window.txt_bg_music_path.text(),
            'bgm_volume': self.window.slider_bgm_volume.value(),
            'bgm_speed': self.window.slider_bgm_speed.value(),
            'target_ratio': self.window.cmb_ratio.currentText(),
            'fit_mode': self.window.cmb_fit_mode.currentText(),
            'blur_intensity': self.window.slider_blur.value(),
            'use_bg_image': self.window.chk_use_bg_image.isChecked(),
            'bg_image_path': self.window.txt_bg_image_path.text(),
            'bg_image_fit': self.window.cmb_bg_image_fit.currentText()
        }

    def get_render_settings(self):
        """Computed/formatted settings for the render engine"""
        return {
            'use_duration': self.window.chk_use_duration.isChecked(),
            'duration': self.window.slider_duration.value(),         # 0 = full video, >0 = trim
            'use_ai_ratio': self.window.chk_use_ai_ratio.isChecked(),
            'ai_ratio': self.window.slider_ai_ratio.value(),         # 10-200 (%)
            'use_scale_normal': self.window.chk_scale_normal.isChecked(),
            'scale_normal': self.window.slider_scale_cycle.value(),
            'use_scale_zoom': self.window.chk_scale_zoom.isChecked(),
            'scale_zoom_hold': self.window.slider_scale_start.value(),
            'use_scale_zoom_ratio': self.window.chk_scale_zoom_ratio.isChecked(),
            'scale_zoom_ratio': self.window.slider_scale_zoom_ratio.value(),
            'scale_easing': self.window.slider_scale_easing.value(),
            'pan_x': self.window.slider_pan_x.value() / 100.0,
            'pan_y': self.window.slider_pan_y.value() / 100.0,
            'max_source': self.window.chk_max_source.isChecked(),
            'scene_gpu': self.window.chk_scene_gpu.isChecked(),
            'im_res': self.window.chk_im_res.isChecked(),
            'keep_hook': self.window.chk_keep_hook.isChecked(),
            'keep_hook_duration': self.window.spin_hook_dur.value(),
            'micro_speed': self.window.chk_micro_speed.isChecked(),
            'mirror_video': self.window.chk_mirror.isChecked(),
            'transition': self.window.slider_transition.value(),
            'date_folder': self.window.chk_date_folder.isChecked(),
            'use_video_speed': self.window.chk_use_video_speed.isChecked(),
            'video_speed': self.window.slider_speed.value() / 10.0,
            'use_pitch': self.window.chk_use_pitch.isChecked(),
            'pitch_ratio': self.window.slider_pitch.value() / 10.0,
            'use_volume': self.window.chk_use_volume.isChecked(),
            'volume': self.window.slider_volume.value() / 100.0,  # 0.0=mute, 1.0=normal, 3.0=3x
            'use_watermark': self.window.chk_use_watermark.isChecked(),
            'watermark_text': self._normalize_watermark_text(self.window.txt_watermark.toPlainText()),
            'watermark_size': self.window.slider_watermark_size.value() / 100.0,
            'watermark_color': self.window.watermark_color,
            'use_text2': self.window.chk_use_text2.isChecked(),
            'text2_text': self._normalize_watermark_text(self.window.txt_text2.toPlainText()),
            'text2_size': self.window.slider_text2_size.value() / 100.0,
            'text2_color': self.window.text2_color,
            'use_logo': self.window.chk_use_logo.isChecked(),
            'logo_path': self.window.txt_logo_path.text(),
            'logo_pos': self.window.cmb_logo_pos.currentText(),
            'logo_scale': self.window.slider_logo_scale.value() / 100.0,
            'logo_opacity': self.window.slider_logo_opacity.value() / 100.0,
            'logo_x_norm': self._logo_x_norm,
            'logo_y_norm': self._logo_y_norm,
            'text_x_norm': self._text_x_norm,
            'text_y_norm': self._text_y_norm,
            'text2_x_norm': self._text2_x_norm,
            'text2_y_norm': self._text2_y_norm,
            'use_subtitles': self.window.chk_use_subtitles.isChecked(),
            'subtitle_path': self.window.txt_subtitle_path.text(),
            'subtitle_font': self.window.cmb_sub_font.currentText(),
            'subtitle_color': self.window.subtitle_color,
            'subtitle_bg_color': self.window.subtitle_bg_color,
            'subtitle_bg_opacity': self.window.slider_sub_bg_opacity.value(),
            'subtitle_font_size': self.window.slider_sub_font_size.value(),
            'subtitle_speed': self.window.slider_sub_speed.value() / 10.0,
            'subtitle_line_spacing': self.window.slider_sub_line_spacing.value(),
            'subtitle_padding': self.window.slider_sub_padding.value(),
            'subtitle_width': self.window.slider_sub_width.value() / 100.0,
            'sub_y_norm': self._sub_y_norm,

            'use_bg_music': self.window.chk_use_bg_music.isChecked(),
            'bg_music_path': self.window.txt_bg_music_path.text(),
            'bgm_volume': self.window.slider_bgm_volume.value() / 100.0,
            'bgm_speed': self.window.slider_bgm_speed.value() / 10.0,
            'target_ratio': self.window.cmb_ratio.currentText(),
            'fit_mode': self.window.cmb_fit_mode.currentText(),
            'blur_intensity': self.window.slider_blur.value() | 1,  # ensure odd for Gaussian
            'use_bg_image': self.window.chk_use_bg_image.isChecked(),
            'bg_image_path': self.window.txt_bg_image_path.text(),
            'bg_image_fit': self.window.cmb_bg_image_fit.currentText()
        }

    def save_preset(self):
        filename, _ = QFileDialog.getSaveFileName(self.window, "Save Preset", "", "JSON Files (*.json)")
        if not filename: return
        import json
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(self.get_ui_values(), f, indent=4)
        self.window.log_msg(f"Preset saved: {filename}")

    def load_preset(self):
        filename, _ = QFileDialog.getOpenFileName(self.window, "Load Preset", "", "JSON Files (*.json)")
        if not filename: return
        import json
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                settings = json.load(f)
            self.window.txt_output_folder.setText(settings.get('output_folder', self.window.txt_output_folder.text()))
            self.window.chk_use_duration.setChecked(settings.get('use_duration', False))
            self.window.slider_duration.setValue(settings.get('duration', 0))
            self.window.chk_use_ai_ratio.setChecked(settings.get('use_ai_ratio', False))
            self.window.slider_ai_ratio.setValue(settings.get('ai_ratio', 100))
            self.window.chk_scale_normal.setChecked(settings.get('use_scale_normal', False))
            self.window.slider_scale_cycle.setValue(settings.get('scale_normal', 0))
            self.window.chk_scale_zoom.setChecked(settings.get('use_scale_zoom', False))
            self.window.slider_scale_start.setValue(settings.get('scale_zoom_hold', 0))
            self.window.chk_scale_zoom_ratio.setChecked(settings.get('use_scale_zoom_ratio', False))
            self.window.slider_scale_zoom_ratio.setValue(settings.get('scale_zoom_ratio', 120))
            self.window.slider_scale_easing.setValue(settings.get('scale_easing', 100))
            self.window.slider_pan_x.setValue(settings.get('pan_x', 0) if isinstance(settings.get('pan_x', 0), int) else int(settings.get('pan_x', 0) * 100))
            self.window.slider_pan_y.setValue(settings.get('pan_y', 0) if isinstance(settings.get('pan_y', 0), int) else int(settings.get('pan_y', 0) * 100))
            self.window.chk_max_source.setChecked(settings.get('max_source', False))
            self.window.chk_scene_gpu.setChecked(settings.get('scene_gpu', False))
            self.window.chk_im_res.setChecked(settings.get('im_res', False))
            self.window.chk_keep_hook.setChecked(settings.get('keep_hook', True))
            self.window.spin_hook_dur.setValue(settings.get('keep_hook_duration', 3))
            self.window.chk_micro_speed.setChecked(settings.get('micro_speed', True))
            self.window.chk_mirror.setChecked(settings.get('mirror_video', False))
            self.window.slider_transition.setValue(settings.get('transition', 300))
            self.window.chk_date_folder.setChecked(settings.get('date_folder', True))
            self.window.chk_use_video_speed.setChecked(settings.get('use_video_speed', False))
            self.window.slider_speed.setValue(settings.get('video_speed', 10))
            self.window.chk_use_pitch.setChecked(settings.get('use_pitch', False))
            self.window.slider_pitch.setValue(settings.get('pitch_ratio', 10))
            self.window.chk_use_volume.setChecked(settings.get('use_volume', False))
            self.window.slider_volume.setValue(settings.get('volume', 100))
            self.window.chk_use_watermark.setChecked(settings.get('use_watermark', False))
            self.window.txt_watermark.setPlainText(self._normalize_watermark_text(settings.get('watermark_text', '')))
            self.window.slider_watermark_size.setValue(settings.get('watermark_size', 8))
            self.window.slider_watermark_pos_y.setValue(settings.get('watermark_pos_y', 90))
            
            # Restore watermark color
            wm_color = settings.get('watermark_color', '#ffff00')
            self.window.watermark_color = wm_color
            from PyQt6.QtGui import QColor
            color_obj = QColor(wm_color)
            text_col = "#000000" if color_obj.lightness() > 128 else "#ffffff"
            self.window.btn_watermark_color.setStyleSheet(f"background-color: {wm_color}; color: {text_col}; font-weight: bold;")
            self.window.btn_watermark_color.setText(wm_color)
            self.window.chk_use_text2.setChecked(settings.get('use_text2', False))
            self.window.txt_text2.setPlainText(self._normalize_watermark_text(settings.get('text2_text', '')))
            self.window.slider_text2_size.setValue(settings.get('text2_size', 8))
            self.window.slider_text2_pos_y.setValue(settings.get('text2_pos_y', 20))
            text2_color = settings.get('text2_color', '#ffffff')
            self.window.text2_color = text2_color
            text2_color_obj = QColor(text2_color)
            text2_text_col = "#000000" if text2_color_obj.lightness() > 128 else "#ffffff"
            self.window.btn_text2_color.setStyleSheet(f"background-color: {text2_color}; color: {text2_text_col}; font-weight: bold;")
            self.window.btn_text2_color.setText(text2_color)
            self.window.chk_use_logo.setChecked(settings.get('use_logo', False))
            self.window.txt_logo_path.setText(settings.get('logo_path', ''))
            idx_logo = self.window.cmb_logo_pos.findText(settings.get('logo_pos', 'Bottom-Right'))
            if idx_logo >= 0: self.window.cmb_logo_pos.setCurrentIndex(idx_logo)
            self.window.slider_logo_scale.setValue(settings.get('logo_scale', 20))
            self.window.slider_logo_opacity.setValue(settings.get('logo_opacity', 80))
            
            self.window.chk_use_subtitles.setChecked(settings.get('use_subtitles', False))
            self.window.txt_subtitle_path.setText(settings.get('subtitle_path', ''))
            idx_font = self.window.cmb_sub_font.findText(settings.get('subtitle_font', 'Arial'))
            if idx_font >= 0: self.window.cmb_sub_font.setCurrentIndex(idx_font)
            
            sub_color = settings.get('subtitle_color', '#ffffff')
            self.window.subtitle_color = sub_color
            c_obj1 = QColor(sub_color)
            tc1 = "#000000" if c_obj1.lightness() > 128 else "#ffffff"
            self.window.btn_sub_color.setStyleSheet(f"background-color: {sub_color}; color: {tc1}; font-weight: bold;")
            self.window.btn_sub_color.setText(sub_color)
            
            sub_bg_color = settings.get('subtitle_bg_color', '#000000')
            self.window.subtitle_bg_color = sub_bg_color
            c_obj2 = QColor(sub_bg_color)
            tc2 = "#000000" if c_obj2.lightness() > 128 else "#ffffff"
            self.window.btn_sub_bg_color.setStyleSheet(f"background-color: {sub_bg_color}; color: {tc2}; font-weight: bold;")
            self.window.btn_sub_bg_color.setText(sub_bg_color)
            
            self.window.slider_sub_bg_opacity.setValue(settings.get('subtitle_bg_opacity', 50))
            self.window.slider_sub_font_size.setValue(settings.get('subtitle_font_size', 48))
            self.window.slider_sub_speed.setValue(settings.get('subtitle_speed', 10))
            self.window.slider_sub_line_spacing.setValue(settings.get('subtitle_line_spacing', 0))
            self.window.slider_sub_padding.setValue(settings.get('subtitle_padding', 10))
            self.window.slider_sub_width.setValue(settings.get('subtitle_width', 90))
            self.window.slider_sub_pos_y.setValue(settings.get('subtitle_pos_y', 90))

            idx_ratio = self.window.cmb_ratio.findText(settings.get('target_ratio', 'Original'))
            if idx_ratio >= 0:
                self.window.cmb_ratio.setCurrentIndex(idx_ratio)
            idx_fit = self.window.cmb_fit_mode.findText(settings.get('fit_mode', 'Crop to Fill'))
            if idx_fit >= 0:
                self.window.cmb_fit_mode.setCurrentIndex(idx_fit)
            self.window.slider_blur.setValue(settings.get('blur_intensity', 31))
            self.window.chk_use_bg_image.setChecked(settings.get('use_bg_image', False))
            self.window.txt_bg_image_path.setText(settings.get('bg_image_path', ''))
            idx_bg_fit = self.window.cmb_bg_image_fit.findText(settings.get('bg_image_fit', 'Cover'))
            if idx_bg_fit >= 0:
                self.window.cmb_bg_image_fit.setCurrentIndex(idx_bg_fit)


            
            self.window.chk_use_bg_music.setChecked(settings.get('use_bg_music', False))
            self.window.txt_bg_music_path.setText(settings.get('bg_music_path', ''))
            self.window.slider_bgm_volume.setValue(settings.get('bgm_volume', 100))
            self.window.slider_bgm_speed.setValue(settings.get('bgm_speed', 10))
            
            # Restore drag positions
            self._logo_x_norm = self._clamp_norm(settings.get('logo_x_norm', 0.85))
            self._logo_y_norm = self._clamp_norm(settings.get('logo_y_norm', 0.85))
            self._text_x_norm = self._clamp_norm(settings.get('text_x_norm', 0.5))
            self._text_y_norm = self._clamp_norm(settings.get('text_y_norm', settings.get('watermark_pos_y', 90) / 100.0))
            self._text2_x_norm = self._clamp_norm(settings.get('text2_x_norm', 0.5))
            self._text2_y_norm = self._clamp_norm(settings.get('text2_y_norm', settings.get('text2_pos_y', 20) / 100.0))
            self._sub_x_norm = self._clamp_norm(settings.get('sub_x_norm', 0.5))
            self._sub_y_norm = self._clamp_norm(settings.get('sub_y_norm', settings.get('subtitle_pos_y', 90) / 100.0))
            self._sync_text_slider_from_norm()
            self._sync_text2_slider_from_norm()
            self._sync_subtitle_slider_from_norm()
            self._refresh_logo_drag_pos_label()
            self._refresh_text_drag_pos_label()
            self._refresh_text2_drag_pos_label()
            self._refresh_sub_drag_pos_label()
            self._update_drag_ui()
            self.window.log_msg(f"Preset loaded: {filename}")
        except Exception as e:
            self.window.log_msg(f"Error loading preset: {e}")

            
    def start_render(self):
        if self.render_worker and self.render_worker.isRunning():
            self.render_worker.cancel()
            self.window.btn_start_render.setText("Cancelling...")
            self.window.btn_start_render.setEnabled(False)
            self.window.log_msg("Stopping render and cleaning up...")
            return
            
        items = self.queue_manager.get_items()
        if not items:
            self.window.log_msg("Queue is empty. Add videos first.")
            return
            
        settings = self.get_render_settings()
        
        out_folder = self.window.txt_output_folder.text()
        
        self.render_worker = RenderWorker(items, settings, out_folder)
        self.render_worker.log.connect(self.window.log_msg)
        self.render_worker.progress.connect(self.update_progress)
        self.render_worker.status.connect(self.update_status)
        self.render_worker.finished.connect(self.on_render_finished)
        
        self.window.btn_start_render.setText("Stop Render")
        self.window.btn_start_render.setStyleSheet("background-color: #dc2626; color: #ffffff; border-color: #b91c1c;")
        self.render_worker.start()
        
    def update_progress(self, filepath, percentage):
        for item in self.queue_manager.get_items():
            if item['path'] == filepath:
                item['widget'].update_progress(percentage, item.get('status', 'Rendering'))
                break
                
    def update_status(self, filepath, status_text):
        for item in self.queue_manager.get_items():
            if item['path'] == filepath:
                item['status'] = status_text
                item['widget'].update_progress(item['widget'].progress_bar.value(), status_text)
                break
                
    def on_render_finished(self):
        self.window.btn_start_render.setEnabled(True)
        self.window.btn_start_render.setText("Start Render")
        self.window.btn_start_render.setStyleSheet("") # reset to default style


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    import os
    style_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.qss")
    if os.path.exists(style_path):
        with open(style_path, "r") as f:
            app.setStyleSheet(f.read())
    
    window = MainWindow()
    window.resize(1200, 800)
    
    # Instantiate Controller to wire UI to Logic
    controller = AppController(window)
    
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
