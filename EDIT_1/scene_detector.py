import cv2
import numpy as np

class SceneDetector:
    def __init__(self, use_gpu=False):
        self.use_gpu = use_gpu

    def detect_scenes(self, video_path, threshold=30.0):
        """
        Detects scene changes in a video based on frame differencing.
        Returns a list of timestamps (in seconds) where scene changes occur.
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0

        scenes = []
        
        ret, prev_frame = cap.read()
        if not ret:
            cap.release()
            return scenes

        prev_frame_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        
        frame_idx = 1
        while True:
            ret, frame = cap.read()
            if not ret:
                break
                
            curr_frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Simple absolute difference between consecutive frames
            diff = cv2.absdiff(curr_frame_gray, prev_frame_gray)
            mean_diff = np.mean(diff)
            
            # If the difference is above a threshold, consider it a scene change
            if mean_diff > threshold:
                timestamp = frame_idx / fps
                scenes.append(timestamp)
                
            prev_frame_gray = curr_frame_gray
            frame_idx += 1
            
        cap.release()
        return scenes
