"""Unified frame capture with OpenCV and circular pre-roll buffer.

This replaces direct ffmpeg device capture. Frames are grabbed by OpenCV,
making them available for preview, overlay, and encoding simultaneously.

The pre-roll buffer keeps the last N seconds of frames in memory so that
when recording starts, you get footage from before the button was pressed.
"""

import collections
import threading
import time

import cv2
import numpy as np


class CircularFrameBuffer:
    """Thread-safe circular buffer holding the last N seconds of frames.

    Frames are stored as (timestamp, numpy_array) tuples.
    """

    def __init__(self, max_seconds=5.0, fps=30):
        self._max_frames = int(max_seconds * fps)
        self._buffer = collections.deque(maxlen=self._max_frames)
        self._lock = threading.Lock()

    def push(self, frame, timestamp=None):
        if timestamp is None:
            timestamp = time.time()
        with self._lock:
            self._buffer.append((timestamp, frame))

    def drain(self):
        """Return all buffered frames and clear the buffer.

        Returns list of (timestamp, frame) tuples in chronological order.
        """
        with self._lock:
            frames = list(self._buffer)
            self._buffer.clear()
            return frames

    def peek_latest(self):
        """Return the most recent frame without removing it, or None."""
        with self._lock:
            if self._buffer:
                return self._buffer[-1]
            return None

    @property
    def count(self):
        with self._lock:
            return len(self._buffer)

    def resize(self, max_seconds, fps):
        with self._lock:
            self._max_frames = int(max_seconds * fps)
            self._buffer = collections.deque(self._buffer, maxlen=self._max_frames)


class FrameGrabber:
    """Captures frames from a camera in a background thread.

    Provides the latest frame for preview and pushes all frames into
    a circular buffer for pre-roll support.
    """

    def __init__(self, device_index=0, width=None, height=None, fps=30,
                 pre_roll_seconds=5.0):
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps

        self._cap = None
        self._running = False
        self._thread = None
        self._latest_frame = None
        self._latest_lock = threading.Lock()
        self._frame_count = 0
        self._start_time = None

        self.pre_roll = CircularFrameBuffer(pre_roll_seconds, fps)

        # Callbacks: list of functions called with (frame, timestamp) on each grab
        self._listeners = []
        self._listeners_lock = threading.Lock()

    def add_listener(self, callback):
        """Register a callback(frame, timestamp) invoked on each captured frame."""
        with self._listeners_lock:
            self._listeners.append(callback)

    def remove_listener(self, callback):
        with self._listeners_lock:
            self._listeners = [cb for cb in self._listeners if cb is not callback]

    def open(self):
        """Open camera and start capture thread."""
        self._cap = cv2.VideoCapture(self.device_index)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video device {self.device_index}")

        if self.width and self.height:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if self.fps:
            self._cap.set(cv2.CAP_PROP_FPS, self.fps)

        # Read actual settings back
        self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = self._cap.get(cv2.CAP_PROP_FPS) or 30

        self._running = True
        self._start_time = time.time()
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

        print(f"FrameGrabber: {self.width}x{self.height}@{self.fps:.0f} on device {self.device_index}")

    def _grab_loop(self):
        """Background thread: continuously grab frames."""
        while self._running:
            ret, frame = self._cap.read()
            if not ret or frame is None:
                time.sleep(0.001)
                continue

            ts = time.time()
            self._frame_count += 1

            with self._latest_lock:
                self._latest_frame = (ts, frame)

            self.pre_roll.push(frame, ts)

            # Notify listeners
            with self._listeners_lock:
                for cb in self._listeners:
                    try:
                        cb(frame, ts)
                    except Exception:
                        pass

    def get_latest(self):
        """Return (timestamp, frame) of the most recent frame, or (None, None)."""
        with self._latest_lock:
            if self._latest_frame is None:
                return None, None
            return self._latest_frame

    @property
    def actual_fps(self):
        """Measured FPS since capture started."""
        if self._start_time is None or self._frame_count == 0:
            return 0.0
        elapsed = time.time() - self._start_time
        return self._frame_count / elapsed if elapsed > 0 else 0.0

    def close(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()
