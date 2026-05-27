import os
import gc
import time
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from video_engine import process_video, get_render_plan



class RenderWorker(QThread):
    progress = pyqtSignal(str, float)  # (filepath, percentage)
    status = pyqtSignal(str, str)      # (filepath, status_text)
    log = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, queue_items, settings, output_folder):
        """
        queue_items: list of dicts from QueueManager
        settings: dict of UI settings
        output_folder: base output directory
        """
        super().__init__()
        self.queue_items = queue_items
        self.settings = settings
        self.output_folder = output_folder
        self.is_running = True
        self.active_outputs = []

    def _cleanup_incomplete_file(self, path):
        """Retry deleting incomplete file up to 5 times (Windows holds file handles)."""
        if not path:
            return

        files_to_delete = [path]
        
        # Also check for temp pitch file pattern
        dir_name = os.path.dirname(path)
        file_name = os.path.basename(path)
        temp_pitch_file = os.path.join(dir_name, "temp_" + file_name)
        if os.path.exists(temp_pitch_file):
            files_to_delete.append(temp_pitch_file)

        for file_path in files_to_delete:
            if not os.path.exists(file_path):
                continue

            filename = os.path.basename(file_path)
            for attempt in range(5):
                try:
                    gc.collect()
                    if attempt > 0:
                        time.sleep(1)
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            self.log.emit(f"Deleted incomplete file: {filename}")
                        except FileNotFoundError:
                            pass
                    break
                except PermissionError:
                    if attempt < 4:
                        self.log.emit(f"File still locked, retry {attempt+1}/5: {filename}")
                    else:
                        self.log.emit(f"Failed to delete after 5 retries: {filename}")
                except Exception as e:
                    self.log.emit(f"Could not delete {filename}: {e}")
                    break

    def run(self):
        self.log.emit(f"Starting Turbo GPU batch render for {len(self.queue_items)} videos.")

        for item in self.queue_items:
            if not self.is_running:
                break
            self._process_item(item)

        if not self.is_running:
            self.log.emit("Render queue cancelled.")

        self.log.emit("Batch render completed.")
        self.finished.emit()

    def _process_item(self, item):
        if not self.is_running:
            return

        filepath = item['path']
        filename = os.path.basename(filepath)

        out_dir = self.output_folder
        if self.settings.get('date_folder', False):
            date_str = datetime.now().strftime("%Y-%m-%d")
            out_dir = os.path.join(out_dir, date_str)

        os.makedirs(out_dir, exist_ok=True)

        out_name = f"viral_{filename}"
        out_path = os.path.join(out_dir, out_name)

        # Auto-increment filename if it already exists
        base_name, ext = os.path.splitext(out_name)
        counter = 1
        while os.path.exists(out_path):
            out_name = f"{base_name}({counter}){ext}"
            out_path = os.path.join(out_dir, out_name)
            counter += 1

        if out_path not in self.active_outputs:
            self.active_outputs.append(out_path)

        self.status.emit(filepath, "Rendering")
        self.progress.emit(filepath, 0.0)
        self.log.emit(f"Started GPU render: {filename}")

        try:
            def progress_callback(fraction):
                if not self.is_running:
                    raise Exception("Render cancelled by user")
                self.progress.emit(filepath, fraction * 100.0)
                return True

            process_video(
                filepath, out_path, self.settings, progress_callback, self.log.emit
            )

            if self.is_running:
                self.status.emit(filepath, "Finished")
                self.progress.emit(filepath, 100.0)
                self.log.emit(f"Completed: {filename}")
            else:
                self.status.emit(filepath, "Cancelled")
                self.log.emit(f"Cancelled rendering: {filename}")
                self._cleanup_incomplete_file(out_path)

        except Exception as e:
            error_msg = str(e)
            if "Render cancelled by user" in error_msg:
                self.status.emit(filepath, "Cancelled")
                self.log.emit(f"Cancelled rendering: {filename}")
                self._cleanup_incomplete_file(out_path)
            else:
                self.log.emit(f"Error processing {filename}: {error_msg}")
                self.status.emit(filepath, "Error")
                self._cleanup_incomplete_file(out_path)

        finally:
            if out_path in self.active_outputs:
                self.active_outputs.remove(out_path)


    def cancel(self):
        self.is_running = False
        if hasattr(self, 'active_outputs'):
            for path in list(self.active_outputs):
                self._cleanup_incomplete_file(path)
