import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
    QTextEdit, QGroupBox, QSlider, QCheckBox, QComboBox, QFileDialog,
    QGridLayout, QScrollArea, QFrame, QSizePolicy, QSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QPoint
from PyQt6.QtGui import QPixmap, QCursor



class DraggablePreviewLabel(QLabel):
    """Preview label that emits normalized (0-1) drag coords for logo/text positioning."""
    logo_dragged = pyqtSignal(float, float)      # nx, ny in video frame space
    text_dragged = pyqtSignal(float, float)      # nx, ny in video frame space
    subtitle_dragged = pyqtSignal(float, float)  # nx, ny in video frame space

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._drag_target = None   # 'logo' | 'text' | 'subtitle' | None
        self._pixmap_rect = None   # QRect of the actual image inside label
        self._logo_enabled = False
        self._text_enabled = False
        self._subtitle_enabled = False

    def set_enabled_overlays(self, logo: bool, text: bool, subtitle: bool = False):
        self._logo_enabled = logo
        self._text_enabled = text
        self._subtitle_enabled = subtitle

    def setPixmap(self, pixmap):
        super().setPixmap(pixmap)
        # Compute where inside the label the image actually lives (KeepAspectRatio)
        if pixmap and not pixmap.isNull():
            lw, lh = self.width(), self.height()
            pw, ph = pixmap.width(), pixmap.height()
            if pw == 0 or ph == 0:
                self._pixmap_rect = None
                return
            scale = min(lw / pw, lh / ph)
            img_w, img_h = int(pw * scale), int(ph * scale)
            ox = (lw - img_w) // 2
            oy = (lh - img_h) // 2
            from PyQt6.QtCore import QRect
            self._pixmap_rect = QRect(ox, oy, img_w, img_h)
        else:
            self._pixmap_rect = None

    def _to_norm(self, pos: QPoint):
        """Convert label-local mouse pos → normalized (0..1, 0..1) video coords."""
        if self._pixmap_rect is None:
            return None
        r = self._pixmap_rect
        nx = (pos.x() - r.x()) / max(1, r.width())
        ny = (pos.y() - r.y()) / max(1, r.height())
        return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            coords = self._to_norm(ev.pos())
            if coords is None:
                return
            mods = ev.modifiers()
            # If multiple are enabled, priority is Logo > Subtitle > Text
            if self._logo_enabled and (mods & Qt.KeyboardModifier.AltModifier):
                 self._drag_target = 'logo'
            elif self._subtitle_enabled:
                 self._drag_target = 'subtitle'
            elif self._text_enabled:
                 self._drag_target = 'text'
            elif self._logo_enabled: # Fallback if no alt but logo is on
                 self._drag_target = 'logo'
                 
            if self._drag_target:
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag_target and (ev.buttons() & Qt.MouseButton.LeftButton):
            coords = self._to_norm(ev.pos())
            if coords:
                nx, ny = coords
                if self._drag_target == 'logo':
                    self.logo_dragged.emit(nx, ny)
                elif self._drag_target == 'subtitle':
                    self.subtitle_dragged.emit(nx, ny)
                else:
                    self.text_dragged.emit(nx, ny)
        elif self._logo_enabled or self._text_enabled or self._subtitle_enabled:
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._drag_target = None
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        super().mouseReleaseEvent(ev)

class QueueItemWidget(QWidget):
    remove_clicked = pyqtSignal(str) # Emits the filepath

    def __init__(self, filepath, duration, parent=None):
        super().__init__(parent)
        self.setObjectName("queueItem")
        self.filepath = filepath
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(10, 8, 10, 8)
        self.layout.setSpacing(10)

        filename = os.path.basename(filepath)
        
        # Info layout
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)
        self.lbl_name = QLabel(filename)
        self.lbl_name.setObjectName("queueFileName")
        self.lbl_name.setWordWrap(True)
        self.lbl_duration = QLabel(f"Duration: {duration:.2f}s")
        self.lbl_duration.setObjectName("queueMeta")
        
        info_layout.addWidget(self.lbl_name)
        info_layout.addWidget(self.lbl_duration)
        
        # Status layout
        status_layout = QVBoxLayout()
        status_layout.setSpacing(5)
        self.lbl_status = QLabel("Ready")
        self.lbl_status.setObjectName("queueStatus")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        
        status_layout.addWidget(self.lbl_status)
        status_layout.addWidget(self.progress_bar)

        # Remove button
        self.btn_remove = QPushButton("X")
        self.btn_remove.setObjectName("dangerSmallButton")
        self.btn_remove.setFixedSize(30, 30)
        self.btn_remove.clicked.connect(lambda: self.remove_clicked.emit(self.filepath))

        self.layout.addLayout(info_layout, stretch=2)
        self.layout.addLayout(status_layout, stretch=3)
        self.layout.addWidget(self.btn_remove)

    def update_progress(self, percentage, status_text):
        self.progress_bar.setValue(int(percentage))
        self.lbl_status.setText(status_text)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Viral Studio - Dynamic Video Editor")
        self.setMinimumSize(1180, 760)
        self.shutdown_callback = None
        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(14)

        # ====== LEFT PANEL ======
        left_panel = QWidget()
        left_panel.setObjectName("leftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # Preview Widget
        self.preview_container = QWidget()
        self.preview_container.setObjectName("previewPanel")
        self.preview_container.setMinimumHeight(390)
        
        preview_layout = QVBoxLayout(self.preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        
        self.lbl_preview = DraggablePreviewLabel("Select a video from queue to preview")
        self.lbl_preview.setObjectName("previewLabel")
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Drag mode hint label
        self.lbl_drag_hint = QLabel("Drag on preview to move overlay positions")
        self.lbl_drag_hint.setObjectName("helperText")
        self.lbl_drag_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_drag_hint.setVisible(False)
        
        preview_layout.addWidget(self.lbl_preview)
        preview_layout.addWidget(self.lbl_drag_hint)
        
        # Playback Controls
        self.playback_container = QWidget()
        self.playback_container.setObjectName("playbackBar")
        playback_layout = QHBoxLayout(self.playback_container)
        playback_layout.setContentsMargins(0, 4, 0, 10)
        playback_layout.setSpacing(10)
        
        self.btn_play_pause = QPushButton("Play")
        self.btn_play_pause.setObjectName("secondaryButton")
        self.btn_play_pause.setFixedWidth(86)
        
        self.slider_seek = QSlider(Qt.Orientation.Horizontal)
        self.slider_seek.setRange(0, 1000)
        
        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setObjectName("timeLabel")
        self.lbl_time.setFixedWidth(100)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        playback_layout.addWidget(self.btn_play_pause)
        playback_layout.addWidget(self.slider_seek)
        playback_layout.addWidget(self.lbl_time)
        
        left_layout.addWidget(self.preview_container, stretch=3)
        left_layout.addWidget(self.playback_container)

        # Queue Manager Header
        queue_header_layout = QHBoxLayout()
        queue_header_layout.setSpacing(8)
        self.btn_add_video = QPushButton("Add Video")
        self.btn_add_video.setObjectName("secondaryButton")
        self.btn_clear_queue = QPushButton("Clear Queue")
        self.btn_clear_queue.setObjectName("secondaryButton")
        self.btn_start_render = QPushButton("Start Render")
        self.btn_start_render.setObjectName("primaryButton")
        
        queue_header_layout.addWidget(self.btn_add_video)
        queue_header_layout.addWidget(self.btn_clear_queue)
        queue_header_layout.addWidget(self.btn_start_render)

        # Queue List
        self.queue_list = QListWidget()
        self.queue_list.setObjectName("queueList")
        
        # Log Panel
        self.log_panel = QTextEdit()
        self.log_panel.setObjectName("logPanel")
        self.log_panel.setReadOnly(True)
        self.log_panel.setPlaceholderText("System Logs will appear here...")
        self.log_panel.setMaximumHeight(170)

        left_layout.addLayout(queue_header_layout)
        queue_label = QLabel("Video Queue")
        queue_label.setObjectName("sectionLabel")
        left_layout.addWidget(queue_label)
        left_layout.addWidget(self.queue_list)
        log_label = QLabel("System Logs")
        log_label.setObjectName("sectionLabel")
        left_layout.addWidget(log_label)
        left_layout.addWidget(self.log_panel)

        # ====== RIGHT PANEL ======
        right_panel = QWidget()
        right_panel.setObjectName("rightPanel")
        right_panel.setMinimumWidth(460)
        right_panel.setMaximumWidth(560)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        
        # Right Panel Header (Presets)
        right_header = QHBoxLayout()
        right_header.setSpacing(8)
        self.btn_save_preset = QPushButton("Save Preset")
        self.btn_save_preset.setObjectName("secondaryButton")
        self.btn_load_preset = QPushButton("Load Preset")
        self.btn_load_preset.setObjectName("secondaryButton")
        right_header.addWidget(self.btn_load_preset)
        right_header.addWidget(self.btn_save_preset)
        right_layout.addLayout(right_header)
        
        scroll_area = QScrollArea()
        scroll_area.setObjectName("settingsScroll")
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_content.setObjectName("settingsContent")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)

        # --- SECTION: VIDEO ---
        group_video = QGroupBox("VIDEO SETTINGS")
        vg_layout = QVBoxLayout(group_video)

        # Output Folder
        out_layout = QHBoxLayout()
        self.txt_output_folder = QLineEdit()
        self.txt_output_folder.setPlaceholderText("Select output folder...")
        self.txt_output_folder.setText(os.path.join(os.path.expanduser("~"), "Videos", "ViralStudio"))
        self.btn_browse_out = QPushButton("Browse")
        self.btn_browse_out.setObjectName("secondaryButton")
        self.btn_open_out = QPushButton("Open")
        self.btn_open_out.setObjectName("secondaryButton")
        out_layout.addWidget(self.txt_output_folder)
        out_layout.addWidget(self.btn_browse_out)
        out_layout.addWidget(self.btn_open_out)
        vg_layout.addWidget(QLabel("Output Folder:"))
        vg_layout.addLayout(out_layout)

        # Date Folder Toggle
        self.chk_date_folder = QCheckBox("Enable Date Subfolder (YYYY-MM-DD)")
        self.chk_date_folder.setChecked(True)
        vg_layout.addWidget(self.chk_date_folder)

        # Sliders layout
        grid_sliders = QGridLayout()
        grid_sliders.setColumnMinimumWidth(0, 140) # Label column
        grid_sliders.setColumnStretch(1, 1)        # Slider column (flexible)
        grid_sliders.setColumnMinimumWidth(2, 90)  # Value column (fixed width to prevent jump)

        # Duration
        self.chk_use_duration = QCheckBox("Duration (s):")
        self.chk_use_duration.setChecked(False)
        grid_sliders.addWidget(self.chk_use_duration, 0, 0)
        self.val_duration = QLabel("0s (Full)")
        self.val_duration.setFixedWidth(90)
        self.slider_duration = QSlider(Qt.Orientation.Horizontal)
        self.slider_duration.setRange(0, 1800)  # 0=full video, up to 30 minutes
        self.slider_duration.setValue(0)
        self.slider_duration.valueChanged.connect(
            lambda v: self.val_duration.setText("0s (Full)" if v == 0 else f"{v}s")
        )
        grid_sliders.addWidget(self.slider_duration, 0, 1)
        grid_sliders.addWidget(self.val_duration, 0, 2)


        # Scale (%)
        self.chk_use_ai_ratio = QCheckBox("Scale (%):")
        self.chk_use_ai_ratio.setChecked(False)
        grid_sliders.addWidget(self.chk_use_ai_ratio, 1, 0)
        self.val_ai_ratio = QLabel("100% (Original)")
        self.val_ai_ratio.setFixedWidth(90)
        self.slider_ai_ratio = QSlider(Qt.Orientation.Horizontal)
        self.slider_ai_ratio.setRange(10, 300)   # 10% to 300%
        self.slider_ai_ratio.setValue(100)       # Default: Original size
        self.slider_ai_ratio.valueChanged.connect(
            lambda v: self.val_ai_ratio.setText("100% (Original)" if v == 100 else f"{v}%")
        )
        grid_sliders.addWidget(self.slider_ai_ratio, 1, 1)
        grid_sliders.addWidget(self.val_ai_ratio, 1, 2)


        # Scale Normal (s) — hold duration at base Scale (%) level
        self.chk_scale_normal = QCheckBox("Scale Normal (s):")
        self.chk_scale_normal.setChecked(False)
        grid_sliders.addWidget(self.chk_scale_normal, 2, 0)
        self.val_scale_normal = QLabel("0s")
        self.val_scale_normal.setFixedWidth(90)
        self.slider_scale_cycle = QSlider(Qt.Orientation.Horizontal)
        self.slider_scale_cycle.setRange(0, 60)
        self.slider_scale_cycle.setValue(0)
        self.slider_scale_cycle.valueChanged.connect(
            lambda v: self.val_scale_normal.setText("0s" if v == 0 else f"{v}s")
        )
        grid_sliders.addWidget(self.slider_scale_cycle, 2, 1)
        grid_sliders.addWidget(self.val_scale_normal, 2, 2)

        # Scale Zoom (%) — target zoom level for animation
        self.chk_scale_zoom = QCheckBox("Scale Zoom (s):")
        self.chk_scale_zoom.setChecked(False)
        grid_sliders.addWidget(self.chk_scale_zoom, 3, 0)
        self.val_scale_zoom = QLabel("0s")
        self.val_scale_zoom.setFixedWidth(90)
        self.slider_scale_start = QSlider(Qt.Orientation.Horizontal)
        self.slider_scale_start.setRange(0, 60)
        self.slider_scale_start.setValue(0)
        self.slider_scale_start.valueChanged.connect(
            lambda v: self.val_scale_zoom.setText("0s" if v == 0 else f"{v}s")
        )
        grid_sliders.addWidget(self.slider_scale_start, 3, 1)
        grid_sliders.addWidget(self.val_scale_zoom, 3, 2)

        # Scale Zoom (%) — target zoom percentage for animation (with checkbox)
        self.chk_scale_zoom_ratio = QCheckBox("Scale Zoom (%):")
        self.chk_scale_zoom_ratio.setChecked(False)
        grid_sliders.addWidget(self.chk_scale_zoom_ratio, 4, 0)
        self.val_scale_zoom_ratio = QLabel("120%")
        self.val_scale_zoom_ratio.setFixedWidth(90)
        self.slider_scale_zoom_ratio = QSlider(Qt.Orientation.Horizontal)
        self.slider_scale_zoom_ratio.setRange(10, 300)
        self.slider_scale_zoom_ratio.setValue(120)
        self.slider_scale_zoom_ratio.valueChanged.connect(
            lambda v: self.val_scale_zoom_ratio.setText(f"{v}%")
        )
        grid_sliders.addWidget(self.slider_scale_zoom_ratio, 4, 1)
        grid_sliders.addWidget(self.val_scale_zoom_ratio, 4, 2)

        # Scale Easing — transition smoothness (0 = instant hard cut)
        grid_sliders.addWidget(QLabel("Scale Easing:"), 5, 0)
        self.val_scale_easing = QLabel("100 (Sine)")
        self.val_scale_easing.setFixedWidth(90)
        self.slider_scale_easing = QSlider(Qt.Orientation.Horizontal)
        self.slider_scale_easing.setRange(0, 100)
        self.slider_scale_easing.setValue(100)
        self.slider_scale_easing.valueChanged.connect(
            lambda v: self.val_scale_easing.setText(
                "0 (Instant)" if v == 0 else f"{v} ({'Sine' if v >= 80 else 'Linear' if v <= 20 else 'Mixed'})"
            )
        )
        grid_sliders.addWidget(self.slider_scale_easing, 5, 1)
        grid_sliders.addWidget(self.val_scale_easing, 5, 2)

        # Pan X (%)
        grid_sliders.addWidget(QLabel("Pan X (%):"), 6, 0)
        self.val_pan_x = QLabel("0%")
        self.val_pan_x.setFixedWidth(90)
        self.slider_pan_x = QSlider(Qt.Orientation.Horizontal)
        self.slider_pan_x.setRange(-100, 100)
        self.slider_pan_x.setValue(0)
        self.slider_pan_x.valueChanged.connect(lambda v: self.val_pan_x.setText(f"{v}%"))
        grid_sliders.addWidget(self.slider_pan_x, 6, 1)
        grid_sliders.addWidget(self.val_pan_x, 6, 2)

        # Pan Y (%)
        grid_sliders.addWidget(QLabel("Pan Y (%):"), 7, 0)
        self.val_pan_y = QLabel("0%")
        self.val_pan_y.setFixedWidth(90)
        self.slider_pan_y = QSlider(Qt.Orientation.Horizontal)
        self.slider_pan_y.setRange(-100, 100)
        self.slider_pan_y.setValue(0)
        self.slider_pan_y.valueChanged.connect(lambda v: self.val_pan_y.setText(f"{v}%"))
        grid_sliders.addWidget(self.slider_pan_y, 7, 1)
        grid_sliders.addWidget(self.val_pan_y, 7, 2)

        # Transition
        grid_sliders.addWidget(QLabel("Transition (ms):"), 8, 0)
        self.val_transition = QLabel("300ms")
        self.val_transition.setFixedWidth(90)
        self.slider_transition = QSlider(Qt.Orientation.Horizontal)
        self.slider_transition.setRange(100, 500)
        self.slider_transition.setValue(300)
        self.slider_transition.valueChanged.connect(lambda v: self.val_transition.setText(f"{v}ms"))
        grid_sliders.addWidget(self.slider_transition, 8, 1)
        grid_sliders.addWidget(self.val_transition, 8, 2)
        
        # Video Speed
        self.chk_use_video_speed = QCheckBox("Video Speed (x)")
        self.chk_use_video_speed.setChecked(False)
        grid_sliders.addWidget(self.chk_use_video_speed, 9, 0)
        self.val_speed = QLabel("1.0x")
        self.val_speed.setFixedWidth(90)
        self.slider_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_speed.setRange(5, 20)
        self.slider_speed.setValue(10)
        self.slider_speed.valueChanged.connect(lambda v: self.val_speed.setText(f"{v/10.0:.1f}x"))
        grid_sliders.addWidget(self.slider_speed, 9, 1)
        grid_sliders.addWidget(self.val_speed, 9, 2)
        
        # Audio Pitch
        self.chk_use_pitch = QCheckBox("Audio Pitch (x)")
        self.chk_use_pitch.setChecked(False)
        grid_sliders.addWidget(self.chk_use_pitch, 10, 0)
        self.val_pitch = QLabel("1.0x")
        self.val_pitch.setFixedWidth(90)
        self.slider_pitch = QSlider(Qt.Orientation.Horizontal)
        self.slider_pitch.setRange(5, 20)
        self.slider_pitch.setValue(10)
        self.slider_pitch.valueChanged.connect(lambda v: self.val_pitch.setText(f"{v/10.0:.1f}x"))
        grid_sliders.addWidget(self.slider_pitch, 10, 1)
        grid_sliders.addWidget(self.val_pitch, 10, 2)

        # Volume
        self.chk_use_volume = QCheckBox("Volume (%)")
        self.chk_use_volume.setChecked(False)
        grid_sliders.addWidget(self.chk_use_volume, 11, 0)
        self.val_volume = QLabel("100%")
        self.val_volume.setFixedWidth(90)
        self.slider_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_volume.setRange(0, 300)   # 0% (mute) to 300% (3x louder)
        self.slider_volume.setValue(100)       # Default: no change
        self.slider_volume.valueChanged.connect(lambda v: self.val_volume.setText(f"{v}%"))
        grid_sliders.addWidget(self.slider_volume, 11, 1)
        grid_sliders.addWidget(self.val_volume, 11, 2)


        vg_layout.addLayout(grid_sliders)

        # Toggles Grid
        grid_toggles = QGridLayout()
        self.chk_max_source = QCheckBox("Max Source")
        self.chk_scene_gpu = QCheckBox("Scene GPU")
        self.chk_im_res = QCheckBox("In Medias Res")
        self.chk_keep_hook = QCheckBox("Keep Hook")
        self.chk_keep_hook.setChecked(True)
        self.spin_hook_dur = QSpinBox()
        self.spin_hook_dur.setRange(1, 10)
        self.spin_hook_dur.setValue(3)
        self.spin_hook_dur.setSuffix("s")
        self.chk_keep_hook.toggled.connect(self.spin_hook_dur.setEnabled)
        
        lay_hook = QHBoxLayout()
        lay_hook.addWidget(self.chk_keep_hook)
        lay_hook.addWidget(self.spin_hook_dur)
        lay_hook.setContentsMargins(0,0,0,0)
        
        hook_widget = QWidget()
        hook_widget.setLayout(lay_hook)
        self.chk_micro_speed = QCheckBox("Micro Speed")
        self.chk_micro_speed.setChecked(True)
        self.chk_mirror = QCheckBox("Mirror Video")

        grid_toggles.addWidget(self.chk_max_source, 0, 0)
        grid_toggles.addWidget(self.chk_scene_gpu, 0, 1)
        grid_toggles.addWidget(self.chk_im_res, 1, 0)
        grid_toggles.addWidget(hook_widget, 1, 1)
        grid_toggles.addWidget(self.chk_micro_speed, 2, 0)
        grid_toggles.addWidget(self.chk_mirror, 2, 1)
        
        vg_layout.addLayout(grid_toggles)
        scroll_layout.addWidget(group_video)

        # --- SECTION: LAYOUT & RESOLUTION ---
        group_layout = QGroupBox("LAYOUT & RESOLUTION")
        lay_layout = QVBoxLayout(group_layout)

        grid_lay = QGridLayout()
        grid_lay.setColumnMinimumWidth(0, 140)
        grid_lay.setColumnStretch(1, 1)
        grid_lay.setColumnMinimumWidth(2, 90)

        # Target Aspect Ratio
        grid_lay.addWidget(QLabel("Target Ratio:"), 0, 0)
        self.cmb_ratio = QComboBox()
        self.cmb_ratio.addItems(["Original", "9:16 (Vertical)", "16:9 (Horizontal)"])
        grid_lay.addWidget(self.cmb_ratio, 0, 1)

        # Fitting Mode
        grid_lay.addWidget(QLabel("Fitting Mode:"), 1, 0)
        self.cmb_fit_mode = QComboBox()
        self.cmb_fit_mode.addItems(["Crop to Fill", "Fit (Black Bars)", "Fit with Blur"])
        grid_lay.addWidget(self.cmb_fit_mode, 1, 1)

        # Blur intensity (only active when Fit with Blur is chosen)
        grid_lay.addWidget(QLabel("Blur Intensity:"), 2, 0)
        self.val_blur = QLabel("31")
        self.val_blur.setFixedWidth(90)
        self.slider_blur = QSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(5, 101)
        self.slider_blur.setValue(31)
        self.slider_blur.valueChanged.connect(
            lambda v: self.val_blur.setText(str(v if v % 2 != 0 else v + 1))
        )
        grid_lay.addWidget(self.slider_blur, 2, 1)
        grid_lay.addWidget(self.val_blur, 2, 2)

        lay_layout.addLayout(grid_lay)
        scroll_layout.addWidget(group_layout)

        # --- SECTION: WATERMARK / TEXT OVERLAY ---
        group_watermark = QGroupBox("WATERMARK & TEXT OVERLAY")
        wm_layout = QVBoxLayout(group_watermark)
        
        self.chk_use_watermark = QCheckBox("Enable Text Overlay")
        self.chk_use_watermark.setChecked(False)
        wm_layout.addWidget(self.chk_use_watermark)
        
        self.txt_watermark = QLineEdit()
        self.txt_watermark.setPlaceholderText("Enter text to display at the bottom (Use \\n to break lines)...")
        wm_layout.addWidget(QLabel("Text Content:"))
        wm_layout.addWidget(self.txt_watermark)
        
        # Font Size Slider
        wm_size_layout = QHBoxLayout()
        wm_size_layout.addWidget(QLabel("Font Size:"))
        self.val_watermark_size = QLabel("8%")
        self.slider_watermark_size = QSlider(Qt.Orientation.Horizontal)
        self.slider_watermark_size.setRange(2, 20)
        self.slider_watermark_size.setValue(8)
        self.slider_watermark_size.valueChanged.connect(lambda v: self.val_watermark_size.setText(f"{v}%"))
        wm_size_layout.addWidget(self.slider_watermark_size)
        wm_size_layout.addWidget(self.val_watermark_size)
        
        wm_layout.addLayout(wm_size_layout)
        
        # Color picker
        wm_color_layout = QHBoxLayout()
        wm_color_layout.addWidget(QLabel("Text Color:"))
        self.btn_watermark_color = QPushButton("#ffff00")
        self.btn_watermark_color.setObjectName("colorButton")
        self.btn_watermark_color.setStyleSheet("background-color: #ffff00; color: #000000; font-weight: bold;")
        self.watermark_color = "#ffff00"
        wm_color_layout.addWidget(self.btn_watermark_color)
        wm_layout.addLayout(wm_color_layout)
        
        # Drag hint for text overlay
        self.lbl_text_drag_pos = QLabel("Position: default (drag on preview to move)")
        self.lbl_text_drag_pos.setObjectName("helperText")
        wm_layout.addWidget(self.lbl_text_drag_pos)
        
        # Text Position (Y) slider
        wm_pos_y_layout = QHBoxLayout()
        wm_pos_y_layout.addWidget(QLabel("Position (Y):"))
        self.val_watermark_pos_y = QLabel("90%")
        self.val_watermark_pos_y.setFixedWidth(40)
        self.slider_watermark_pos_y = QSlider(Qt.Orientation.Horizontal)
        self.slider_watermark_pos_y.setRange(0, 100)
        self.slider_watermark_pos_y.setValue(90)
        self.slider_watermark_pos_y.valueChanged.connect(lambda v: self.val_watermark_pos_y.setText(f"{v}%"))
        wm_pos_y_layout.addWidget(self.slider_watermark_pos_y)
        wm_pos_y_layout.addWidget(self.val_watermark_pos_y)
        wm_layout.addLayout(wm_pos_y_layout)
        
        scroll_layout.addWidget(group_watermark)

        # --- SECTION: IMAGE LOGO OVERLAY ---
        group_logo = QGroupBox("IMAGE LOGO OVERLAY")
        logo_layout = QVBoxLayout(group_logo)
        
        self.chk_use_logo = QCheckBox("Enable Image Logo")
        self.chk_use_logo.setChecked(False)
        logo_layout.addWidget(self.chk_use_logo)
        
        # Logo File Selection
        logo_file_layout = QHBoxLayout()
        self.txt_logo_path = QLineEdit()
        self.txt_logo_path.setPlaceholderText("Select logo image file...")
        self.btn_browse_logo = QPushButton("Browse")
        self.btn_browse_logo.setObjectName("secondaryButton")
        logo_file_layout.addWidget(self.txt_logo_path)
        logo_file_layout.addWidget(self.btn_browse_logo)
        logo_layout.addLayout(logo_file_layout)
        
        # Logo Position
        logo_pos_layout = QHBoxLayout()
        logo_pos_layout.addWidget(QLabel("Position:"))
        self.cmb_logo_pos = QComboBox()
        self.cmb_logo_pos.addItems(["Top-Left", "Top-Right", "Bottom-Left", "Bottom-Right", "Center", "Manual (Drag)"])
        logo_pos_layout.addWidget(self.cmb_logo_pos)
        logo_layout.addLayout(logo_pos_layout)
        
        # Logo Scale
        logo_scale_layout = QHBoxLayout()
        logo_scale_layout.addWidget(QLabel("Scale:"))
        self.val_logo_scale = QLabel("20%")
        self.val_logo_scale.setFixedWidth(40)
        self.slider_logo_scale = QSlider(Qt.Orientation.Horizontal)
        self.slider_logo_scale.setRange(5, 100)
        self.slider_logo_scale.setValue(20)
        self.slider_logo_scale.valueChanged.connect(lambda v: self.val_logo_scale.setText(f"{v}%"))
        logo_scale_layout.addWidget(self.slider_logo_scale)
        logo_scale_layout.addWidget(self.val_logo_scale)
        logo_layout.addLayout(logo_scale_layout)
        
        # Logo Opacity
        logo_opacity_layout = QHBoxLayout()
        logo_opacity_layout.addWidget(QLabel("Opacity:"))
        self.val_logo_opacity = QLabel("80%")
        self.val_logo_opacity.setFixedWidth(40)
        self.slider_logo_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_logo_opacity.setRange(10, 100)
        self.slider_logo_opacity.setValue(80)
        self.slider_logo_opacity.valueChanged.connect(lambda v: self.val_logo_opacity.setText(f"{v}%"))
        logo_opacity_layout.addWidget(self.slider_logo_opacity)
        logo_opacity_layout.addWidget(self.val_logo_opacity)
        logo_layout.addLayout(logo_opacity_layout)
        
        scroll_layout.addWidget(group_logo)

        # --- SECTION: SUBTITLES ---
        group_subtitles = QGroupBox("SUBTITLES (.SRT / .ASS)")
        subtitles_layout = QVBoxLayout(group_subtitles)
        
        self.chk_use_subtitles = QCheckBox("Enable Subtitles")
        self.chk_use_subtitles.setChecked(False)
        subtitles_layout.addWidget(self.chk_use_subtitles)
        
        # Subtitle File Selection
        subtitle_file_layout = QHBoxLayout()
        self.txt_subtitle_path = QLineEdit()
        self.txt_subtitle_path.setPlaceholderText("Select subtitle file (.srt, .ass)...")
        self.btn_browse_subtitle = QPushButton("Browse")
        self.btn_browse_subtitle.setObjectName("secondaryButton")
        subtitle_file_layout.addWidget(self.txt_subtitle_path)
        subtitle_file_layout.addWidget(self.btn_browse_subtitle)
        subtitles_layout.addLayout(subtitle_file_layout)
        
        # Subtitle Font
        sub_font_layout = QHBoxLayout()
        sub_font_layout.addWidget(QLabel("Font:"))
        self.cmb_sub_font = QComboBox()
        self.cmb_sub_font.addItems(["Arial", "Times New Roman", "Courier New", "Verdana", "Tahoma", "Trebuchet MS", "Impact"])
        sub_font_layout.addWidget(self.cmb_sub_font)
        subtitles_layout.addLayout(sub_font_layout)
        
        # Subtitle Font Size
        sub_size_layout = QHBoxLayout()
        sub_size_layout.addWidget(QLabel("Size:"))
        self.val_sub_font_size = QLabel("48")
        self.val_sub_font_size.setFixedWidth(40)
        self.slider_sub_font_size = QSlider(Qt.Orientation.Horizontal)
        self.slider_sub_font_size.setRange(10, 200)
        self.slider_sub_font_size.setValue(48)
        self.slider_sub_font_size.valueChanged.connect(lambda v: self.val_sub_font_size.setText(str(v)))
        sub_size_layout.addWidget(self.slider_sub_font_size)
        sub_size_layout.addWidget(self.val_sub_font_size)
        subtitles_layout.addLayout(sub_size_layout)
        
        # Subtitle Max Width (%)
        sub_width_layout = QHBoxLayout()
        sub_width_layout.addWidget(QLabel("Width (%):"))
        self.val_sub_width = QLabel("90%")
        self.val_sub_width.setFixedWidth(40)
        self.slider_sub_width = QSlider(Qt.Orientation.Horizontal)
        self.slider_sub_width.setRange(30, 100)
        self.slider_sub_width.setValue(90)
        self.slider_sub_width.valueChanged.connect(lambda v: self.val_sub_width.setText(f"{v}%"))
        sub_width_layout.addWidget(self.slider_sub_width)
        sub_width_layout.addWidget(self.val_sub_width)
        subtitles_layout.addLayout(sub_width_layout)
        
        # Subtitle Colors
        sub_color_layout = QHBoxLayout()
        sub_color_layout.addWidget(QLabel("Text Color:"))
        self.btn_sub_color = QPushButton("#ffffff")
        self.btn_sub_color.setObjectName("colorButton")
        self.btn_sub_color.setStyleSheet("background-color: #ffffff; color: #000000; font-weight: bold;")
        self.subtitle_color = "#ffffff"
        sub_color_layout.addWidget(self.btn_sub_color)
        
        sub_color_layout.addWidget(QLabel("  Bg Color:"))
        self.btn_sub_bg_color = QPushButton("#000000")
        self.btn_sub_bg_color.setObjectName("colorButton")
        self.btn_sub_bg_color.setStyleSheet("background-color: #000000; color: #ffffff; font-weight: bold;")
        self.subtitle_bg_color = "#000000"
        sub_color_layout.addWidget(self.btn_sub_bg_color)
        subtitles_layout.addLayout(sub_color_layout)
        
        # Subtitle Line Spacing & Padding
        sub_layout_extra = QGridLayout()
        
        sub_layout_extra.addWidget(QLabel("Line Spacing:"), 0, 0)
        self.val_sub_line_spacing = QLabel("0")
        self.val_sub_line_spacing.setFixedWidth(30)
        self.slider_sub_line_spacing = QSlider(Qt.Orientation.Horizontal)
        self.slider_sub_line_spacing.setRange(-50, 100)
        self.slider_sub_line_spacing.setValue(0)
        self.slider_sub_line_spacing.valueChanged.connect(lambda v: self.val_sub_line_spacing.setText(str(v)))
        sub_layout_extra.addWidget(self.slider_sub_line_spacing, 0, 1)
        sub_layout_extra.addWidget(self.val_sub_line_spacing, 0, 2)
        
        sub_layout_extra.addWidget(QLabel("Padding:"), 1, 0)
        self.val_sub_padding = QLabel("10")
        self.val_sub_padding.setFixedWidth(30)
        self.slider_sub_padding = QSlider(Qt.Orientation.Horizontal)
        self.slider_sub_padding.setRange(0, 100)
        self.slider_sub_padding.setValue(10)
        self.slider_sub_padding.valueChanged.connect(lambda v: self.val_sub_padding.setText(str(v)))
        sub_layout_extra.addWidget(self.slider_sub_padding, 1, 1)
        sub_layout_extra.addWidget(self.val_sub_padding, 1, 2)
        
        subtitles_layout.addLayout(sub_layout_extra)
        
        # Subtitle Bg Opacity
        sub_op_layout = QHBoxLayout()
        sub_op_layout.addWidget(QLabel("Bg Opacity:"))
        self.val_sub_bg_opacity = QLabel("50%")
        self.val_sub_bg_opacity.setFixedWidth(40)
        self.slider_sub_bg_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_sub_bg_opacity.setRange(0, 100)
        self.slider_sub_bg_opacity.setValue(50)
        self.slider_sub_bg_opacity.valueChanged.connect(lambda v: self.val_sub_bg_opacity.setText(f"{v}%"))
        sub_op_layout.addWidget(self.slider_sub_bg_opacity)
        sub_op_layout.addWidget(self.val_sub_bg_opacity)
        subtitles_layout.addLayout(sub_op_layout)
        
        # Subtitle Position (Y)
        sub_pos_layout = QHBoxLayout()
        sub_pos_layout.addWidget(QLabel("Position (Y):"))
        self.val_sub_pos_y = QLabel("90%")
        self.val_sub_pos_y.setFixedWidth(40)
        self.slider_sub_pos_y = QSlider(Qt.Orientation.Horizontal)
        self.slider_sub_pos_y.setRange(0, 100)
        self.slider_sub_pos_y.setValue(90)
        self.slider_sub_pos_y.valueChanged.connect(lambda v: self.val_sub_pos_y.setText(f"{v}%"))
        sub_pos_layout.addWidget(self.slider_sub_pos_y)
        sub_pos_layout.addWidget(self.val_sub_pos_y)
        subtitles_layout.addLayout(sub_pos_layout)

        # Subtitle Speed (Manual Offset)
        sub_speed_layout = QHBoxLayout()
        sub_speed_layout.addWidget(QLabel("Speed (x):"))
        self.val_sub_speed = QLabel("1.0x")
        self.val_sub_speed.setFixedWidth(40)
        self.slider_sub_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_sub_speed.setRange(5, 20)  # 0.5x to 2.0x
        self.slider_sub_speed.setValue(10)     # 1.0x
        self.slider_sub_speed.valueChanged.connect(lambda v: self.val_sub_speed.setText(f"{v/10.0:.1f}x"))
        sub_speed_layout.addWidget(self.slider_sub_speed)
        sub_speed_layout.addWidget(self.val_sub_speed)
        subtitles_layout.addLayout(sub_speed_layout)

        
        # Drag hint
        self.lbl_sub_drag_pos = QLabel("Position: (Drag on preview to move)")
        self.lbl_sub_drag_pos.setObjectName("helperText")
        subtitles_layout.addWidget(self.lbl_sub_drag_pos)
        
        
        scroll_layout.addWidget(group_subtitles)



        # --- SECTION: BACKGROUND MUSIC ---
        group_bgm = QGroupBox("BACKGROUND MUSIC")
        bgm_layout = QVBoxLayout(group_bgm)
        
        self.chk_use_bg_music = QCheckBox("Enable Background Music")
        self.chk_use_bg_music.setChecked(False)
        bgm_layout.addWidget(self.chk_use_bg_music)
        
        # Music File Selection
        bgm_file_layout = QHBoxLayout()
        self.txt_bg_music_path = QLineEdit()
        self.txt_bg_music_path.setPlaceholderText("Select audio file (mp3, wav, etc.)...")
        self.btn_browse_bg_music = QPushButton("Browse")
        self.btn_browse_bg_music.setObjectName("secondaryButton")
        bgm_file_layout.addWidget(self.txt_bg_music_path)
        bgm_file_layout.addWidget(self.btn_browse_bg_music)
        bgm_layout.addLayout(bgm_file_layout)
        
        # Music Volume
        bgm_volume_layout = QHBoxLayout()
        bgm_volume_layout.addWidget(QLabel("Volume:"))
        self.val_bgm_volume = QLabel("100%")
        self.val_bgm_volume.setFixedWidth(40)
        self.slider_bgm_volume = QSlider(Qt.Orientation.Horizontal)
        self.slider_bgm_volume.setRange(0, 200) # 0% to 200%
        self.slider_bgm_volume.setValue(100)
        self.slider_bgm_volume.valueChanged.connect(lambda v: self.val_bgm_volume.setText(f"{v}%"))
        bgm_volume_layout.addWidget(self.slider_bgm_volume)
        bgm_volume_layout.addWidget(self.val_bgm_volume)
        bgm_layout.addLayout(bgm_volume_layout)
        
        # Music Speed
        bgm_speed_layout = QHBoxLayout()
        bgm_speed_layout.addWidget(QLabel("Speed (x):"))
        self.val_bgm_speed = QLabel("1.0x")
        self.val_bgm_speed.setFixedWidth(40)
        self.slider_bgm_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_bgm_speed.setRange(5, 20) # 0.5x to 2.0x
        self.slider_bgm_speed.setValue(10)
        self.slider_bgm_speed.valueChanged.connect(lambda v: self.val_bgm_speed.setText(f"{v/10.0:.1f}x"))
        bgm_speed_layout.addWidget(self.slider_bgm_speed)
        bgm_speed_layout.addWidget(self.val_bgm_speed)
        bgm_layout.addLayout(bgm_speed_layout)
        
        scroll_layout.addWidget(group_bgm)
        
        scroll_layout.addStretch()

        scroll_area.setWidget(scroll_content)
        right_layout.addWidget(scroll_area)

        # Combine Panels
        main_layout.addWidget(left_panel, stretch=2)
        main_layout.addWidget(right_panel, stretch=1)

        # Connect toggles to enable/disable sliders for better UX
        self.chk_use_ai_ratio.toggled.connect(self.slider_ai_ratio.setEnabled)
        self.chk_scale_normal.toggled.connect(self.slider_scale_cycle.setEnabled)
        self.chk_scale_zoom.toggled.connect(self.slider_scale_start.setEnabled)
        self.chk_scale_zoom_ratio.toggled.connect(self.slider_scale_zoom_ratio.setEnabled)
        self.chk_use_logo.toggled.connect(self.txt_logo_path.setEnabled)
        self.chk_use_logo.toggled.connect(self.btn_browse_logo.setEnabled)
        self.chk_use_logo.toggled.connect(self.cmb_logo_pos.setEnabled)
        self.chk_use_logo.toggled.connect(self.slider_logo_scale.setEnabled)
        self.chk_use_logo.toggled.connect(self.slider_logo_opacity.setEnabled)
        self.chk_use_watermark.toggled.connect(self.txt_watermark.setEnabled)
        self.chk_use_watermark.toggled.connect(self.slider_watermark_size.setEnabled)
        self.chk_use_watermark.toggled.connect(self.btn_watermark_color.setEnabled)
        self.chk_use_watermark.toggled.connect(self.slider_watermark_pos_y.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.txt_subtitle_path.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.btn_browse_subtitle.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.cmb_sub_font.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.btn_sub_color.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.btn_sub_bg_color.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.slider_sub_bg_opacity.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.slider_sub_font_size.setEnabled)
        
        self.chk_use_subtitles.toggled.connect(self.slider_sub_pos_y.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.slider_sub_speed.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.slider_sub_width.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.slider_sub_line_spacing.setEnabled)
        self.chk_use_subtitles.toggled.connect(self.slider_sub_padding.setEnabled)

        self.chk_use_bg_music.toggled.connect(self.txt_bg_music_path.setEnabled)
        self.chk_use_bg_music.toggled.connect(self.btn_browse_bg_music.setEnabled)
        self.chk_use_bg_music.toggled.connect(self.slider_bgm_volume.setEnabled)
        self.chk_use_bg_music.toggled.connect(self.slider_bgm_speed.setEnabled)
        self.chk_use_pitch.toggled.connect(self.slider_pitch.setEnabled)
        self.chk_use_volume.toggled.connect(self.slider_volume.setEnabled)
        
        # Set initial states
        self.slider_ai_ratio.setEnabled(self.chk_use_ai_ratio.isChecked())
        self.slider_scale_cycle.setEnabled(self.chk_scale_normal.isChecked())
        self.slider_scale_start.setEnabled(self.chk_scale_zoom.isChecked())
        self.slider_scale_zoom_ratio.setEnabled(self.chk_scale_zoom_ratio.isChecked())
        self.txt_logo_path.setEnabled(self.chk_use_logo.isChecked())
        self.btn_browse_logo.setEnabled(self.chk_use_logo.isChecked())
        self.cmb_logo_pos.setEnabled(self.chk_use_logo.isChecked())
        self.slider_logo_scale.setEnabled(self.chk_use_logo.isChecked())
        self.slider_logo_opacity.setEnabled(self.chk_use_logo.isChecked())
        self.txt_watermark.setEnabled(self.chk_use_watermark.isChecked())
        self.slider_watermark_size.setEnabled(self.chk_use_watermark.isChecked())
        self.btn_watermark_color.setEnabled(self.chk_use_watermark.isChecked())
        self.slider_watermark_pos_y.setEnabled(self.chk_use_watermark.isChecked())
        self.txt_subtitle_path.setEnabled(self.chk_use_subtitles.isChecked())
        self.btn_browse_subtitle.setEnabled(self.chk_use_subtitles.isChecked())
        self.cmb_sub_font.setEnabled(self.chk_use_subtitles.isChecked())
        self.btn_sub_color.setEnabled(self.chk_use_subtitles.isChecked())
        self.btn_sub_bg_color.setEnabled(self.chk_use_subtitles.isChecked())
        self.slider_sub_bg_opacity.setEnabled(self.chk_use_subtitles.isChecked())
        self.slider_sub_font_size.setEnabled(self.chk_use_subtitles.isChecked())
        self.slider_sub_pos_y.setEnabled(self.chk_use_subtitles.isChecked())
        self.slider_sub_width.setEnabled(self.chk_use_subtitles.isChecked())
        self.slider_sub_line_spacing.setEnabled(self.chk_use_subtitles.isChecked())
        self.slider_sub_padding.setEnabled(self.chk_use_subtitles.isChecked())

        self.txt_bg_music_path.setEnabled(self.chk_use_bg_music.isChecked())
        self.btn_browse_bg_music.setEnabled(self.chk_use_bg_music.isChecked())
        self.slider_bgm_volume.setEnabled(self.chk_use_bg_music.isChecked())
        self.slider_bgm_speed.setEnabled(self.chk_use_bg_music.isChecked())
        self.slider_pitch.setEnabled(self.chk_use_pitch.isChecked())
        self.slider_volume.setEnabled(self.chk_use_volume.isChecked())

        # --- Aspect Ratio smart toggle ---
        def _ratio_changed(index):
            """Enable/disable fitting controls based on selected ratio."""
            not_orig = (index != 0)
            self.cmb_fit_mode.setEnabled(not_orig)
            # index 2 = "Fit with Blur"
            self.slider_blur.setEnabled(not_orig and self.cmb_fit_mode.currentIndex() == 2)
            self.val_blur.setEnabled(not_orig and self.cmb_fit_mode.currentIndex() == 2)

        def _fit_changed(index):
            not_orig = (self.cmb_ratio.currentIndex() != 0)
            # index 2 = "Fit with Blur"
            self.slider_blur.setEnabled(not_orig and index == 2)
            self.val_blur.setEnabled(not_orig and index == 2)

        self.cmb_ratio.currentIndexChanged.connect(_ratio_changed)
        self.cmb_fit_mode.currentIndexChanged.connect(_fit_changed)

        # Initial states for aspect ratio controls
        self.cmb_fit_mode.setEnabled(False)   # starts as "Original"
        self.slider_blur.setEnabled(False)
        self.val_blur.setEnabled(False)

    def log_msg(self, msg):
        self.log_panel.append(msg)

    def closeEvent(self, event):
        if callable(self.shutdown_callback):
            self.shutdown_callback()
        event.accept()
