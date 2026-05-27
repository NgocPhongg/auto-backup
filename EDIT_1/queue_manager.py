import os
from PyQt6.QtWidgets import QListWidgetItem, QFileDialog
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from moviepy import VideoFileClip

from ui_main import QueueItemWidget


class VideoLoadWorker(QThread):
    loaded = pyqtSignal(str, float)
    failed = pyqtSignal(str, str)

    def __init__(self, files):
        super().__init__()
        self.files = files
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        for file_path in self.files:
            if self._cancelled:
                break
            clip = None
            try:
                clip = VideoFileClip(file_path)
                duration = float(clip.duration or 0.0)
                if self._cancelled:
                    break
                self.loaded.emit(file_path, duration)
            except Exception as exc:
                if not self._cancelled:
                    self.failed.emit(file_path, str(exc))
            finally:
                if clip is not None:
                    try:
                        clip.close()
                    except Exception:
                        pass


class QueueManager(QObject):
    log_signal = pyqtSignal(str)
    
    def __init__(self, main_window):
        super().__init__()
        self.window = main_window
        # List of dicts representing the queue items
        self.queue = [] 
        self._load_worker = None
        self._loading_paths = set()
        
        # Connect UI buttons
        self.window.btn_add_video.clicked.connect(self.add_video)
        self.window.btn_clear_queue.clicked.connect(self.clear_queue)
        
    def add_video(self):
        if self._load_worker and self._load_worker.isRunning():
            self.log_signal.emit("Video metadata is still loading. Please wait.")
            return

        files, _ = QFileDialog.getOpenFileNames(
            self.window, "Select Videos", "", "Video Files (*.mp4 *.mkv *.avi *.mov)"
        )
        selected = []
        for file_path in files:
            # Prevent duplicates
            if any(item['path'] == file_path for item in self.queue):
                continue
            if file_path in self._loading_paths:
                continue
            selected.append(file_path)

        if not selected:
            return

        self._loading_paths = set(selected)
        self.window.btn_add_video.setEnabled(False)
        self.window.btn_add_video.setText("Loading...")
        self.log_signal.emit(f"Loading metadata for {len(selected)} video(s)...")

        self._load_worker = VideoLoadWorker(selected)
        self._load_worker.loaded.connect(self._add_loaded_video)
        self._load_worker.failed.connect(self._on_load_failed)
        self._load_worker.finished.connect(self._on_load_finished)
        self._load_worker.start()

    def _add_loaded_video(self, file_path, duration):
        if any(item['path'] == file_path for item in self.queue):
            return

        # Create UI elements
        list_item = QListWidgetItem(self.window.queue_list)
        widget = QueueItemWidget(file_path, duration)
        list_item.setSizeHint(widget.sizeHint())

        self.window.queue_list.addItem(list_item)
        self.window.queue_list.setItemWidget(list_item, widget)

        # Connect remove button
        widget.remove_clicked.connect(self.remove_video)

        # Add to tracking
        queue_data = {
            'path': file_path,
            'item': list_item,
            'widget': widget,
            'duration': duration,
            'status': 'Ready'
        }
        self.queue.append(queue_data)
        self.log_signal.emit(f"Added video to queue: {os.path.basename(file_path)}")

    def _on_load_failed(self, file_path, error):
        self.log_signal.emit(f"Error loading {file_path}: {error}")

    def _on_load_finished(self):
        self._loading_paths.clear()
        self.window.btn_add_video.setEnabled(True)
        self.window.btn_add_video.setText("Add Video")
        self.log_signal.emit("Video metadata loading finished.")
        self._load_worker = None

    def remove_video(self, filepath):
        for item in self.queue:
            if item['path'] == filepath:
                row = self.window.queue_list.row(item['item'])
                self.window.queue_list.takeItem(row)
                self.queue.remove(item)
                self.log_signal.emit(f"Removed video: {os.path.basename(filepath)}")
                break

    def clear_queue(self):
        self.window.queue_list.clear()
        self.queue.clear()
        self.log_signal.emit("Queue cleared.")

    def shutdown(self):
        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.cancel()
            if not self._load_worker.wait(3000):
                self._load_worker.terminate()
                self._load_worker.wait(1000)
        
    def get_items(self):
        """Returns the internal list of trackable items."""
        return self.queue
