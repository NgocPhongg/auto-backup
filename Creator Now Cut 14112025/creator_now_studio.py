import os
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt
from PyQt6.QtGui import QAction, QColor, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class WorkflowStep:
    title: str
    batch_file: str
    input_folder: str
    output_folder: str
    description: str
    dangerous: bool = False


WORKFLOW_STEPS = [
    WorkflowStep(
        "1. Cat stock",
        "01.CAT STOCK.bat",
        "00.videogoc",
        "01.catstock",
        "Cat video goc thanh cac doan stock theo thoi luong random.",
    ),
    WorkflowStep(
        "2. Edit video",
        "02.EDIT.bat",
        "00.videogoc",
        "02.Edit",
        "Xu ly video voi bo loc ffmpeg, am thanh nen va metadata sach.",
    ),
    WorkflowStep(
        "3. Tach anh",
        "03.tachanh.bat",
        "00.videogoc",
        "03.tachanh",
        "Trich xuat anh tu video goc theo chu ky trong batch hien tai.",
    ),
    WorkflowStep(
        "4. Tach mp3",
        "04.tachmp3.bat",
        "00.videogoc",
        "04.tachmp3",
        "Tach audio mp3 tu video goc.",
    ),
    WorkflowStep(
        "5. Gop le",
        "05.gop_le.bat",
        "04.tachmp3",
        "05.gop_le",
        "Gop cac thanh phan theo quy trinh le.",
    ),
    WorkflowStep(
        "6. Gop chan",
        "06.gop_chan.bat",
        "04.tachmp3",
        "06.gop_chan",
        "Gop cac thanh phan theo quy trinh chan.",
    ),
    WorkflowStep(
        "7. Reset",
        "07.reset.bat",
        ".",
        ".",
        "Don cac thu muc lam viec theo file reset hien co.",
        dangerous=True,
    ),
    WorkflowStep(
        "8. Xoa photo le",
        "08. xoa photo le.bat",
        "05.gop_le",
        "05.gop_le",
        "Xoa anh/video le theo batch hien co.",
        dangerous=True,
    ),
    WorkflowStep(
        "9. Xoa photo chan",
        "09. xoa photo chan.bat",
        "06.gop_chan",
        "06.gop_chan",
        "Xoa anh/video chan theo batch hien co.",
        dangerous=True,
    ),
    WorkflowStep(
        "10. Gop photo random",
        "10. gop photo random.bat",
        "03.tachanh",
        "ketqua",
        "Ghep anh random thanh video ket qua.",
    ),
    WorkflowStep(
        "11. Gop video stock random",
        "11. gop video stock random.bat",
        "01.catstock",
        "ketqua",
        "Ghep video stock random thanh video ket qua.",
    ),
]


WATCH_FOLDERS = [
    ("Video goc", "00.videogoc", (".mp4", ".mov", ".mkv", ".avi", ".webm")),
    ("Cat stock", "01.catstock", (".mp4", ".mov", ".mkv", ".avi", ".webm")),
    ("Edit", "02.Edit", (".mp4", ".mov", ".mkv", ".avi", ".webm")),
    ("Anh tach", "03.tachanh", (".jpg", ".jpeg", ".png", ".webp")),
    ("MP3", "04.tachmp3", (".mp3", ".wav", ".m4a", ".aac")),
    ("Gop le", "05.gop_le", (".mp4", ".mov", ".mkv", ".avi", ".jpg", ".jpeg", ".png")),
    ("Gop chan", "06.gop_chan", (".mp4", ".mov", ".mkv", ".avi", ".jpg", ".jpeg", ".png")),
    ("Ket qua", "ketqua", (".mp4", ".mov", ".mkv", ".avi", ".jpg", ".jpeg", ".png")),
    ("Nhac nen", "bg", (".mp3", ".wav", ".m4a", ".aac")),
]

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm")


def safe_path(relative: str) -> Path:
    candidate = (BASE_DIR / relative).resolve()
    if candidate != BASE_DIR and BASE_DIR not in candidate.parents:
        raise ValueError(f"Path khong hop le: {relative}")
    return candidate


def file_count(folder: Path, extensions: tuple[str, ...]) -> int:
    if not folder.exists():
        return 0
    return sum(1 for item in folder.iterdir() if item.is_file() and item.suffix.lower() in extensions)


def folder_size_mb(folder: Path) -> float:
    if not folder.exists():
        return 0.0
    total = 0
    for item in folder.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total / (1024 * 1024)


class DropBox(QFrame):
    def __init__(self, on_files_dropped):
        super().__init__()
        self.on_files_dropped = on_files_dropped
        self.setAcceptDrops(True)
        self.setObjectName("dropBox")
        layout = QVBoxLayout(self)
        title = QLabel("Keo tha video vao day")
        title.setObjectName("dropTitle")
        hint = QLabel("File se duoc copy vao thu muc 00.videogoc")
        hint.setObjectName("muted")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addStretch()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        files = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if files:
            self.on_files_dropped(files)
            event.acceptProposedAction()


class CreatorNowStudio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Creator Now Studio")
        self.resize(1280, 780)
        self.setMinimumSize(1100, 700)
        self.process: QProcess | None = None
        self.process_kind = ""
        self.current_step: WorkflowStep | None = None
        self.queue: list[WorkflowStep] = []
        self.running_all = False
        self.cut_tasks: list[dict] = []
        self.cut_total = 0
        self.cut_done = 0

        self._build_ui()
        self._wire_menu()
        self._apply_styles()
        self.refresh_dashboard()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_dashboard)
        self.refresh_timer.start(4000)

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Creator Now Studio")
        title.setObjectName("appTitle")
        subtitle = QLabel(str(BASE_DIR))
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        top.addLayout(title_box, stretch=1)

        self.refresh_btn = QPushButton("Lam moi")
        self.refresh_btn.setMinimumWidth(96)
        self.refresh_btn.clicked.connect(self.refresh_dashboard)
        self.run_all_btn = QPushButton("Chay toan bo")
        self.run_all_btn.setMinimumWidth(120)
        self.run_all_btn.setObjectName("primaryButton")
        self.run_all_btn.clicked.connect(self.run_all)
        self.stop_btn = QPushButton("Dung")
        self.stop_btn.setMinimumWidth(86)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_process)
        top.addWidget(self.refresh_btn)
        top.addWidget(self.run_all_btn)
        top.addWidget(self.stop_btn)
        root_layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.step_list = QListWidget()
        self.step_list.setMinimumWidth(240)
        self.step_list.setUniformItemSizes(True)
        for step in WORKFLOW_STEPS:
            item = QListWidgetItem(step.title)
            item.setData(Qt.ItemDataRole.UserRole, step)
            if step.dangerous:
                item.setForeground(QColor("#b45309"))
            self.step_list.addItem(item)
        self.step_list.currentItemChanged.connect(self.on_step_changed)
        splitter.addWidget(self.step_list)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_dashboard_tab(), "Tong quan")
        self.tabs.addTab(self._build_step_tab(), "Buoc xu ly")
        self.tabs.addTab(self._build_log_tab(), "Log")
        splitter.addWidget(self.tabs)
        splitter.setSizes([260, 1020])

        root_layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())

        self.step_list.setCurrentRow(0)

    def _wire_menu(self):
        menu = self.menuBar().addMenu("File")
        open_base = QAction("Mo thu muc app", self)
        open_base.triggered.connect(lambda: self.open_folder("."))
        menu.addAction(open_base)
        open_result = QAction("Mo ket qua", self)
        open_result.triggered.connect(lambda: self.open_folder("ketqua"))
        menu.addAction(open_result)
        menu.addSeparator()
        quit_action = QAction("Thoat", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

    def _build_dashboard_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        self.drop_box = DropBox(self.copy_input_files)
        self.drop_box.setMinimumHeight(120)
        layout.addWidget(self.drop_box)

        self.stats_table = QTableWidget(0, 4)
        self.stats_table.setHorizontalHeaderLabels(["Khu vuc", "Thu muc", "So file", "Dung luong"])
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.stats_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.stats_table.setAlternatingRowColors(True)
        self.stats_table.horizontalHeader().setStretchLastSection(True)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.stats_table.verticalHeader().setDefaultSectionSize(38)
        self.stats_table.setMinimumHeight(360)
        layout.addWidget(self.stats_table, stretch=1)

        action_row = QHBoxLayout()
        for label, folder in [
            ("Mo video goc", "00.videogoc"),
            ("Mo stock", "01.catstock"),
            ("Mo edit", "02.Edit"),
            ("Mo anh", "03.tachanh"),
            ("Mo ket qua", "ketqua"),
        ]:
            btn = QPushButton(label)
            btn.setMinimumWidth(118)
            btn.clicked.connect(lambda checked=False, f=folder: self.open_folder(f))
            action_row.addWidget(btn)
        action_row.addStretch()
        layout.addLayout(action_row)
        return page

    def _build_step_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        info = QFrame()
        info.setObjectName("panel")
        info_layout = QGridLayout(info)
        info_layout.setColumnStretch(1, 1)
        self.step_title = QLabel()
        self.step_title.setObjectName("sectionTitle")
        self.step_desc = QLabel()
        self.step_desc.setWordWrap(True)
        self.step_desc.setObjectName("muted")
        self.step_batch = QLabel()
        self.step_input = QLabel()
        self.step_output = QLabel()
        info_layout.addWidget(self.step_title, 0, 0, 1, 2)
        info_layout.addWidget(self.step_desc, 1, 0, 1, 2)
        info_layout.addWidget(QLabel("Batch"), 2, 0)
        info_layout.addWidget(self.step_batch, 2, 1)
        info_layout.addWidget(QLabel("Input"), 3, 0)
        info_layout.addWidget(self.step_input, 3, 1)
        info_layout.addWidget(QLabel("Output"), 4, 0)
        info_layout.addWidget(self.step_output, 4, 1)
        layout.addWidget(info)

        self.cut_options = self._build_cut_stock_options()
        layout.addWidget(self.cut_options)

        button_row = QHBoxLayout()
        self.run_step_btn = QPushButton("Chay buoc nay")
        self.run_step_btn.setMinimumWidth(130)
        self.run_step_btn.setObjectName("primaryButton")
        self.run_step_btn.clicked.connect(self.run_selected_step)
        self.open_input_btn = QPushButton("Mo input")
        self.open_input_btn.setMinimumWidth(100)
        self.open_input_btn.clicked.connect(lambda: self.open_folder(self.current_step.input_folder if self.current_step else "."))
        self.open_output_btn = QPushButton("Mo output")
        self.open_output_btn.setMinimumWidth(100)
        self.open_output_btn.clicked.connect(lambda: self.open_folder(self.current_step.output_folder if self.current_step else "."))
        self.preview_cmd_btn = QPushButton("Xem lenh")
        self.preview_cmd_btn.setMinimumWidth(100)
        self.preview_cmd_btn.clicked.connect(self.preview_command)
        button_row.addWidget(self.run_step_btn)
        button_row.addWidget(self.open_input_btn)
        button_row.addWidget(self.open_output_btn)
        button_row.addWidget(self.preview_cmd_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("San sang")
        layout.addWidget(self.progress)

        self.command_preview = QTextEdit()
        self.command_preview.setReadOnly(True)
        self.command_preview.setMinimumHeight(120)
        layout.addWidget(self.command_preview)
        return page

    def _build_cut_stock_options(self) -> QWidget:
        box = QGroupBox("Cau hinh cat stock")
        box.setObjectName("optionsBox")
        layout = QGridLayout(box)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

        self.cut_random_check = QCheckBox("Random thoi luong moi doan")
        self.cut_random_check.setChecked(True)
        self.cut_random_check.stateChanged.connect(self.update_command_preview)

        self.cut_min_minutes = QDoubleSpinBox()
        self.cut_min_minutes.setRange(0.1, 999.0)
        self.cut_min_minutes.setDecimals(1)
        self.cut_min_minutes.setSingleStep(0.5)
        self.cut_min_minutes.setSuffix(" phut")
        self.cut_min_minutes.setValue(12.9)
        self.cut_min_minutes.valueChanged.connect(self.update_command_preview)

        self.cut_max_minutes = QDoubleSpinBox()
        self.cut_max_minutes.setRange(0.1, 999.0)
        self.cut_max_minutes.setDecimals(1)
        self.cut_max_minutes.setSingleStep(0.5)
        self.cut_max_minutes.setSuffix(" phut")
        self.cut_max_minutes.setValue(13.9)
        self.cut_max_minutes.valueChanged.connect(self.update_command_preview)

        self.cut_head_seconds = QSpinBox()
        self.cut_head_seconds.setRange(0, 86400)
        self.cut_head_seconds.setSuffix(" giay")
        self.cut_head_seconds.setValue(0)
        self.cut_head_seconds.valueChanged.connect(self.update_command_preview)

        self.cut_tail_seconds = QSpinBox()
        self.cut_tail_seconds.setRange(0, 86400)
        self.cut_tail_seconds.setSuffix(" giay")
        self.cut_tail_seconds.setValue(0)
        self.cut_tail_seconds.valueChanged.connect(self.update_command_preview)

        self.cut_video_limit = QSpinBox()
        self.cut_video_limit.setRange(0, 99999)
        self.cut_video_limit.setSpecialValueText("Tat ca")
        self.cut_video_limit.setValue(0)
        self.cut_video_limit.valueChanged.connect(self.update_command_preview)

        self.cut_clip_limit = QSpinBox()
        self.cut_clip_limit.setRange(0, 99999)
        self.cut_clip_limit.setSpecialValueText("Tat ca")
        self.cut_clip_limit.setValue(0)
        self.cut_clip_limit.valueChanged.connect(self.update_command_preview)

        self.cut_keep_short_check = QCheckBox("Giu doan cuoi neu ngan hon")
        self.cut_keep_short_check.setChecked(True)
        self.cut_keep_short_check.stateChanged.connect(self.update_command_preview)

        self.cut_mode_combo = QComboBox()
        self.cut_mode_combo.addItem("Nhanh - copy stream", "copy")
        self.cut_mode_combo.addItem("Chinh xac - render lai", "encode")
        self.cut_mode_combo.currentIndexChanged.connect(self.update_command_preview)

        layout.addWidget(self.cut_random_check, 0, 0, 1, 2)
        layout.addWidget(QLabel("Do dai tu"), 1, 0)
        layout.addWidget(self.cut_min_minutes, 1, 1)
        layout.addWidget(QLabel("Den"), 1, 2)
        layout.addWidget(self.cut_max_minutes, 1, 3)
        layout.addWidget(QLabel("Cat bo dau"), 2, 0)
        layout.addWidget(self.cut_head_seconds, 2, 1)
        layout.addWidget(QLabel("Cat bo cuoi"), 2, 2)
        layout.addWidget(self.cut_tail_seconds, 2, 3)
        layout.addWidget(QLabel("So video toi da"), 3, 0)
        layout.addWidget(self.cut_video_limit, 3, 1)
        layout.addWidget(QLabel("So doan/video"), 3, 2)
        layout.addWidget(self.cut_clip_limit, 3, 3)
        layout.addWidget(QLabel("Che do cat"), 4, 0)
        layout.addWidget(self.cut_mode_combo, 4, 1)
        layout.addWidget(self.cut_keep_short_check, 4, 2, 1, 2)
        return box

    def _build_log_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.log_view)

        row = QHBoxLayout()
        clear_btn = QPushButton("Xoa log")
        clear_btn.setMinimumWidth(100)
        clear_btn.clicked.connect(self.log_view.clear)
        row.addWidget(clear_btn)
        row.addStretch()
        layout.addLayout(row)
        return page

    def _apply_styles(self):
        self.setStyleSheet(
            """
            * {
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 12px;
                color: #111827;
            }
            QMainWindow, QWidget {
                background: #f3f5f8;
            }
            QMenuBar {
                background: #f3f5f8;
                color: #111827;
                padding: 4px;
            }
            QMenuBar::item {
                background: transparent;
                color: #111827;
                padding: 6px 10px;
                border-radius: 4px;
            }
            QMenuBar::item:selected {
                background: #e5eaf2;
            }
            QMenu {
                background: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
            }
            QMenu::item {
                padding: 7px 24px;
            }
            QMenu::item:selected {
                background: #dbeafe;
                color: #0f172a;
            }
            QLabel { color: #111827; }
            #appTitle { font-size: 24px; font-weight: 700; }
            #sectionTitle { font-size: 18px; font-weight: 700; }
            #muted { color: #475569; }
            #panel {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
            }
            #dropBox {
                background: #ffffff;
                border: 2px dashed #94a3b8;
                border-radius: 8px;
            }
            #dropTitle { font-size: 18px; font-weight: 700; }
            QGroupBox {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                margin-top: 12px;
                padding: 12px;
                font-weight: 700;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #0f172a;
                background: #ffffff;
            }
            QSpinBox, QDoubleSpinBox, QComboBox {
                background: #ffffff;
                color: #111827;
                border: 1px solid #94a3b8;
                border-radius: 6px;
                padding: 6px 8px;
                min-height: 24px;
            }
            QCheckBox {
                color: #111827;
                spacing: 8px;
            }
            QPushButton {
                background: #ffffff;
                color: #111827;
                border: 1px solid #94a3b8;
                border-radius: 6px;
                padding: 8px 12px;
            }
            QPushButton:hover { background: #eef2f7; }
            QPushButton:disabled { color: #98a2b3; background: #f2f4f7; }
            #primaryButton {
                background: #2563eb;
                color: #ffffff;
                border: 1px solid #1d4ed8;
                font-weight: 600;
            }
            #primaryButton:hover { background: #1d4ed8; }
            QSplitter::handle {
                background: #cbd5e1;
            }
            QTabWidget::pane {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                top: -1px;
            }
            QTabBar::tab {
                background: #e5e7eb;
                color: #111827;
                border: 1px solid #cbd5e1;
                padding: 8px 14px;
                margin-right: 3px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #0f172a;
                border-bottom-color: #ffffff;
            }
            QTabBar::tab:hover {
                background: #f8fafc;
            }
            QListWidget, QTableWidget, QPlainTextEdit, QTextEdit {
                background: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
            }
            QListWidget {
                alternate-background-color: #f8fafc;
            }
            QListWidget::item {
                padding: 11px;
                color: #111827;
                border-bottom: 1px solid #e5e7eb;
            }
            QListWidget::item:selected {
                background: #dbeafe;
                color: #102a56;
            }
            QListWidget::item:hover {
                background: #eff6ff;
                color: #102a56;
            }
            QTableWidget {
                gridline-color: #d7dde7;
                alternate-background-color: #f8fafc;
                selection-background-color: #dbeafe;
                selection-color: #0f172a;
            }
            QHeaderView::section {
                background: #334155;
                color: #ffffff;
                border: 0;
                border-right: 1px solid #475569;
                padding: 8px;
                font-weight: 700;
            }
            QProgressBar {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                color: #111827;
                text-align: center;
                min-height: 22px;
            }
            QProgressBar::chunk {
                background: #2563eb;
                border-radius: 5px;
            }
            QStatusBar {
                background: #f3f5f8;
                color: #334155;
            }
            """
        )

    def selected_step(self) -> WorkflowStep | None:
        item = self.step_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def is_cut_stock_step(self, step: WorkflowStep | None) -> bool:
        return bool(step and step.batch_file == "01.CAT STOCK.bat")

    def on_step_changed(self):
        self.current_step = self.selected_step()
        if not self.current_step:
            return
        step = self.current_step
        self.step_title.setText(step.title)
        self.step_desc.setText(step.description)
        self.step_batch.setText("Xu ly truc tiep trong app" if self.is_cut_stock_step(step) else str(safe_path(step.batch_file)))
        self.step_input.setText(str(safe_path(step.input_folder)))
        self.step_output.setText(str(safe_path(step.output_folder)))
        self.cut_options.setVisible(self.is_cut_stock_step(step))
        self.update_command_preview()

    def update_command_preview(self):
        step = self.selected_step()
        if step:
            self.command_preview.setPlainText(self.build_command_text(step))

    def cut_settings(self) -> dict:
        min_seconds = int(round(self.cut_min_minutes.value() * 60))
        max_seconds = int(round(self.cut_max_minutes.value() * 60))
        if max_seconds < min_seconds:
            max_seconds = min_seconds
        return {
            "random_duration": self.cut_random_check.isChecked(),
            "min_seconds": min_seconds,
            "max_seconds": max_seconds,
            "head_seconds": self.cut_head_seconds.value(),
            "tail_seconds": self.cut_tail_seconds.value(),
            "video_limit": self.cut_video_limit.value(),
            "clip_limit": self.cut_clip_limit.value(),
            "keep_short": self.cut_keep_short_check.isChecked(),
            "mode": self.cut_mode_combo.currentData(),
        }

    def build_command_text(self, step: WorkflowStep) -> str:
        if self.is_cut_stock_step(step):
            settings = self.cut_settings()
            duration_text = (
                f"random {settings['min_seconds']}s - {settings['max_seconds']}s"
                if settings["random_duration"]
                else f"co dinh {settings['min_seconds']}s"
            )
            video_limit = "tat ca" if settings["video_limit"] == 0 else str(settings["video_limit"])
            clip_limit = "tat ca" if settings["clip_limit"] == 0 else str(settings["clip_limit"])
            mode = "copy stream" if settings["mode"] == "copy" else "render lai"
            return (
                "Cat stock bang app\n"
                f"Input: {safe_path(step.input_folder)}\n"
                f"Output: {safe_path(step.output_folder)}\n"
                f"Do dai moi doan: {duration_text}\n"
                f"Cat bo dau: {settings['head_seconds']}s | Cat bo cuoi: {settings['tail_seconds']}s\n"
                f"So video toi da: {video_limit} | So doan moi video: {clip_limit}\n"
                f"Doan cuoi ngan: {'giu lai' if settings['keep_short'] else 'bo qua'}\n"
                f"Che do: {mode}"
            )
        batch_path = safe_path(step.batch_file)
        return f'cmd.exe /d /s /c call "{batch_path.name}" ^< nul\nWorking directory: {BASE_DIR}'

    def append_log(self, text: str):
        if not text:
            return
        self.log_view.appendPlainText(text.rstrip())
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def refresh_dashboard(self):
        self.stats_table.setRowCount(len(WATCH_FOLDERS))
        for row, (label, folder_name, extensions) in enumerate(WATCH_FOLDERS):
            folder = safe_path(folder_name)
            folder.mkdir(exist_ok=True)
            values = [
                label,
                folder_name,
                str(file_count(folder, extensions)),
                f"{folder_size_mb(folder):.1f} MB",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col in (2, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.stats_table.setItem(row, col, item)
        ffmpeg_ok = safe_path("ffmpeg.exe").exists()
        self.statusBar().showMessage("ffmpeg.exe san sang" if ffmpeg_ok else "Khong thay ffmpeg.exe trong thu muc app")

    def copy_input_files(self, files: list[Path]):
        target_dir = safe_path("00.videogoc")
        target_dir.mkdir(exist_ok=True)
        copied = 0
        skipped = 0
        for src in files:
            if not src.is_file():
                skipped += 1
                continue
            if src.suffix.lower() not in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
                skipped += 1
                continue
            dest = target_dir / src.name
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                n = 2
                while (target_dir / f"{stem}_{n}{suffix}").exists():
                    n += 1
                dest = target_dir / f"{stem}_{n}{suffix}"
            try:
                shutil.copy2(src, dest)
                copied += 1
            except OSError as exc:
                self.append_log(f"Loi copy {src}: {exc}")
        self.append_log(f"Da copy {copied} file vao 00.videogoc. Bo qua {skipped} file.")
        self.refresh_dashboard()

    def confirm_if_needed(self, step: WorkflowStep) -> bool:
        if not step.dangerous:
            return True
        answer = QMessageBox.warning(
            self,
            "Xac nhan thao tac",
            f"{step.title} co the xoa file trong thu muc lam viec.\nBan co muon chay khong?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def run_selected_step(self):
        step = self.selected_step()
        if step:
            self.run_step(step)

    def run_all(self):
        if self.process:
            QMessageBox.information(self, "Dang chay", "Dang co tien trinh dang chay.")
            return
        self.queue = [step for step in WORKFLOW_STEPS if not step.dangerous]
        self.running_all = True
        self.append_log("=== Bat dau chay toan bo workflow khong bao gom cac buoc xoa/reset ===")
        self.run_next_in_queue()

    def run_next_in_queue(self):
        if not self.queue:
            self.running_all = False
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.progress.setFormat("Hoan thanh")
            self.append_log("=== Da chay xong workflow ===")
            self.set_running_state(False)
            self.refresh_dashboard()
            return
        step = self.queue.pop(0)
        self.run_step(step, from_queue=True)

    def run_step(self, step: WorkflowStep, from_queue: bool = False):
        if self.process:
            QMessageBox.information(self, "Dang chay", "Dang co tien trinh dang chay.")
            return
        if not from_queue and not self.confirm_if_needed(step):
            return
        if self.is_cut_stock_step(step):
            self.run_cut_stock(step)
            return
        batch_path = safe_path(step.batch_file)
        if not batch_path.exists():
            QMessageBox.critical(self, "Thieu file", f"Khong thay batch file:\n{batch_path}")
            return

        self.current_step = step
        self.tabs.setCurrentIndex(2)
        self.append_log(f"\n=== Chay: {step.title} ===")
        self.append_log(self.build_command_text(step))
        self.progress.setRange(0, 0)
        self.progress.setFormat("Dang chay...")
        self.set_running_state(True)

        self.process = QProcess(self)
        self.process_kind = "batch"
        env = QProcessEnvironment.systemEnvironment()
        current_path = env.value("PATH", "")
        env.insert("PATH", f"{BASE_DIR}{os.pathsep}{current_path}")
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(BASE_DIR))
        self.process.setProgram("cmd.exe")
        self.process.setArguments(["/d", "/s", "/c", f'call "{batch_path.name}" < nul'])
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.on_process_output)
        self.process.finished.connect(self.on_process_finished)
        self.process.errorOccurred.connect(self.on_process_error)
        self.process.start()

    def ffmpeg_program(self) -> str:
        local = safe_path("ffmpeg.exe")
        if local.exists():
            return str(local)
        found = shutil.which("ffmpeg")
        return found or "ffmpeg"

    def video_duration_seconds(self, video_path: Path) -> float | None:
        try:
            result = subprocess.run(
                [self.ffmpeg_program(), "-hide_banner", "-i", str(video_path)],
                cwd=str(BASE_DIR),
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.append_log(f"Khong doc duoc thoi luong {video_path.name}: {exc}")
            return None
        output = (
            result.stdout.decode("utf-8", errors="replace")
            + "\n"
            + result.stderr.decode("utf-8", errors="replace")
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
        if not match:
            self.append_log(f"Khong tim thay thoi luong trong video: {video_path.name}")
            return None
        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    def build_cut_tasks(self, step: WorkflowStep) -> list[dict]:
        settings = self.cut_settings()
        input_dir = safe_path(step.input_folder)
        output_dir = safe_path(step.output_folder)
        output_dir.mkdir(exist_ok=True)

        videos = sorted(
            [item for item in input_dir.iterdir() if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS],
            key=lambda item: item.name.lower(),
        )
        if settings["video_limit"]:
            videos = videos[: settings["video_limit"]]

        tasks = []
        self.append_log(f"Tim thay {len(videos)} video de cat trong {input_dir.name}.")
        for video_index, video_path in enumerate(videos, start=1):
            duration = self.video_duration_seconds(video_path)
            if duration is None:
                continue

            usable_start = settings["head_seconds"]
            usable_end = max(0, duration - settings["tail_seconds"])
            usable_duration = usable_end - usable_start
            if usable_duration <= 0:
                self.append_log(
                    f"Bo qua {video_path.name}: sau khi cat dau/cuoi khong con thoi luong hop le."
                )
                continue

            self.append_log(
                f"Video {video_index}/{len(videos)}: {video_path.name} | "
                f"{duration:.1f}s, cat dau {settings['head_seconds']}s, cat cuoi {settings['tail_seconds']}s."
            )

            start_time = float(usable_start)
            part = 1
            while start_time < usable_end:
                if settings["clip_limit"] and part > settings["clip_limit"]:
                    break

                if settings["random_duration"]:
                    clip_duration = random.randint(settings["min_seconds"], settings["max_seconds"])
                else:
                    clip_duration = settings["min_seconds"]

                time_left = usable_end - start_time
                if time_left < clip_duration:
                    if not settings["keep_short"]:
                        break
                    clip_duration = time_left

                if clip_duration <= 0:
                    break

                output_path = output_dir / f"{video_path.stem}_part{part}.mp4"
                tasks.append(
                    {
                        "input": video_path,
                        "output": output_path,
                        "start": start_time,
                        "duration": clip_duration,
                        "part": part,
                    }
                )
                start_time += clip_duration
                part += 1

        return tasks

    def run_cut_stock(self, step: WorkflowStep):
        ffmpeg = self.ffmpeg_program()
        if ffmpeg == "ffmpeg" and not shutil.which("ffmpeg"):
            QMessageBox.critical(self, "Thieu ffmpeg", "Khong thay ffmpeg.exe trong thu muc app hoac PATH.")
            return

        settings = self.cut_settings()
        if settings["min_seconds"] <= 0:
            QMessageBox.warning(self, "Cau hinh chua dung", "Thoi luong cat phai lon hon 0.")
            return
        if settings["max_seconds"] < settings["min_seconds"]:
            QMessageBox.warning(self, "Cau hinh chua dung", "Thoi luong den phai lon hon hoac bang thoi luong tu.")
            return

        self.current_step = step
        self.tabs.setCurrentIndex(2)
        self.append_log(f"\n=== Chay: {step.title} ===")
        self.append_log(self.build_command_text(step))
        self.set_running_state(True)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Dang quet video...")

        self.cut_tasks = self.build_cut_tasks(step)
        self.cut_total = len(self.cut_tasks)
        self.cut_done = 0
        if not self.cut_tasks:
            self.append_log("Khong co clip nao can cat. Kiem tra input va cau hinh cat dau/cuoi.")
            self.set_running_state(False)
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setFormat("Khong co viec")
            if self.running_all:
                self.running_all = False
            return

        self.append_log(f"Bat dau cat {self.cut_total} clip.")
        self.progress.setRange(0, self.cut_total)
        self.progress.setValue(0)
        self.progress.setFormat("Dang cat %v/%m")
        self.start_next_cut_task()

    def start_next_cut_task(self):
        if not self.cut_tasks:
            self.append_log(f"=== Hoan thanh cat stock: {self.cut_done}/{self.cut_total} clip ===")
            self.process_kind = ""
            self.set_running_state(False)
            self.refresh_dashboard()
            self.progress.setRange(0, max(1, self.cut_total))
            self.progress.setValue(self.cut_done)
            self.progress.setFormat("Hoan thanh")
            if self.running_all:
                self.run_next_in_queue()
            return

        task = self.cut_tasks.pop(0)
        settings = self.cut_settings()
        ffmpeg = self.ffmpeg_program()
        if settings["mode"] == "copy":
            args = [
                "-y",
                "-ss",
                f"{task['start']:.3f}",
                "-i",
                str(task["input"]),
                "-t",
                f"{task['duration']:.3f}",
                "-c",
                "copy",
                str(task["output"]),
            ]
        else:
            args = [
                "-y",
                "-ss",
                f"{task['start']:.3f}",
                "-i",
                str(task["input"]),
                "-t",
                f"{task['duration']:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(task["output"]),
            ]

        self.append_log(
            f"Cat clip {self.cut_done + 1}/{self.cut_total}: "
            f"{task['input'].name} part {task['part']} | start {task['start']:.1f}s | dai {task['duration']:.1f}s"
        )

        self.process = QProcess(self)
        self.process_kind = "cut"
        env = QProcessEnvironment.systemEnvironment()
        current_path = env.value("PATH", "")
        env.insert("PATH", f"{BASE_DIR}{os.pathsep}{current_path}")
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(BASE_DIR))
        self.process.setProgram(ffmpeg)
        self.process.setArguments(args)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.on_process_output)
        self.process.finished.connect(self.on_process_finished)
        self.process.errorOccurred.connect(self.on_process_error)
        self.process.start()

    def on_process_output(self):
        if not self.process:
            return
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self.append_log(data)

    def on_process_error(self, error):
        self.append_log(f"Loi tien trinh: {error}")

    def on_process_finished(self, exit_code: int, exit_status):
        kind = self.process_kind
        step_title = self.current_step.title if self.current_step else "Unknown"
        if kind == "cut":
            self.process = None
            if exit_code == 0:
                self.cut_done += 1
                self.progress.setValue(self.cut_done)
                self.refresh_dashboard()
                self.start_next_cut_task()
            else:
                self.append_log(f"=== Loi cat stock: {step_title} | exit code {exit_code} ===")
                self.cut_tasks = []
                self.process_kind = ""
                self.running_all = False
                self.set_running_state(False)
                self.refresh_dashboard()
                self.progress.setRange(0, max(1, self.cut_total))
                self.progress.setValue(self.cut_done)
                self.progress.setFormat("Loi")
            return

        self.append_log(f"=== Ket thuc: {step_title} | exit code {exit_code} ===")
        self.process = None
        self.process_kind = ""
        self.refresh_dashboard()

        if self.running_all:
            if exit_code == 0:
                self.run_next_in_queue()
            else:
                self.append_log("Dung workflow vi buoc hien tai bi loi.")
                self.running_all = False
                self.set_running_state(False)
                self.progress.setRange(0, 1)
                self.progress.setValue(0)
                self.progress.setFormat("Loi")
            return

        self.set_running_state(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if exit_code == 0 else 0)
        self.progress.setFormat("Hoan thanh" if exit_code == 0 else "Loi")

    def set_running_state(self, running: bool):
        self.run_all_btn.setEnabled(not running)
        self.run_step_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.step_list.setEnabled(not running)

    def stop_process(self):
        if not self.process:
            return
        self.append_log("Dang dung tien trinh...")
        self.running_all = False
        self.queue = []
        self.cut_tasks = []
        self.process_kind = ""
        self.process.kill()

    def preview_command(self):
        step = self.selected_step()
        if step:
            self.command_preview.setPlainText(self.build_command_text(step))
            self.tabs.setCurrentIndex(1)

    def open_folder(self, relative: str):
        folder = safe_path(relative)
        folder.mkdir(exist_ok=True)
        os.startfile(folder)

    def closeEvent(self, event):
        if self.process:
            answer = QMessageBox.question(
                self,
                "Dang chay",
                "Tien trinh van dang chay. Ban co muon dung va thoat khong?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.process.kill()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = CreatorNowStudio()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
