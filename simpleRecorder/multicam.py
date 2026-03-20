"""Multi-camera simultaneous recording session manager.

Orchestrates multiple FrameGrabbers and encoding pipelines, keeping them
synchronized. Each camera gets its own capture thread, encoder, and
optional preview window.
"""

import os
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from capture import FrameGrabber
from overlay import TimecodeOverlay


class CameraConfig:
    """Configuration for a single camera in a multi-cam session."""

    def __init__(
        self,
        device_index=0,
        name=None,
        width=None,
        height=None,
        fps=30,
        codec="h264",
        pre_roll_seconds=5.0,
        overlay=True,
        overlay_label=None,
    ):
        self.device_index = device_index
        self.name = name or f"cam{device_index}"
        self.width = width
        self.height = height
        self.fps = fps
        self.codec = codec
        self.pre_roll_seconds = pre_roll_seconds
        self.overlay = overlay
        self.overlay_label = overlay_label or self.name


class CameraStream:
    """A single camera's capture + encode pipeline within a multi-cam session."""

    def __init__(self, config, output_dir, base_name):
        self.config = config
        self.output_dir = output_dir
        self.base_name = base_name

        self.grabber = FrameGrabber(
            device_index=config.device_index,
            width=config.width,
            height=config.height,
            fps=config.fps,
            pre_roll_seconds=config.pre_roll_seconds,
        )

        self.timecode = TimecodeOverlay(
            show_timestamp=True,
            show_timecode=True,
            label=config.overlay_label,
        ) if config.overlay else None

        self._writer = None
        self._recording = False
        self._output_path = None
        self._rec_start = None
        self._frames_written = 0

    def open(self):
        self.grabber.open()

    def close(self):
        self.stop_recording()
        self.grabber.close()

    def start_recording(self):
        """Start recording to a new file, flushing pre-roll buffer first."""
        if self._recording:
            return self._output_path

        os.makedirs(self.output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = ".mov" if self.config.codec in ("h264", "avc") else ".mov"
        self._output_path = os.path.join(
            self.output_dir, f"{self.base_name}_{self.config.name}_{ts}{ext}"
        )

        # Get frame size from grabber
        w = self.grabber.width
        h = self.grabber.height
        fps = self.grabber.fps

        fourcc = cv2.VideoWriter_fourcc(*"avc1")
        self._writer = cv2.VideoWriter(self._output_path, fourcc, fps, (w, h))

        if not self._writer.isOpened():
            # Fallback to XVID
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            self._output_path = self._output_path.rsplit(".", 1)[0] + ".avi"
            self._writer = cv2.VideoWriter(self._output_path, fourcc, fps, (w, h))

        # Flush pre-roll buffer into the file
        pre_roll_frames = self.grabber.pre_roll.drain()
        for frame_ts, frame in pre_roll_frames:
            out = self._apply_overlay(frame, is_preroll=True)
            self._writer.write(out)
            self._frames_written += 1

        if self.timecode:
            self.timecode.start_recording(fps)

        self._recording = True
        self._rec_start = time.time()

        # Register as listener for live frames
        self.grabber.add_listener(self._on_frame)

        return self._output_path

    def stop_recording(self):
        """Stop recording and finalize the file."""
        if not self._recording:
            return None, 0

        self.grabber.remove_listener(self._on_frame)
        self._recording = False

        if self.timecode:
            self.timecode.stop_recording()

        if self._writer is not None:
            self._writer.release()
            self._writer = None

        duration = time.time() - self._rec_start if self._rec_start else 0
        path = self._output_path
        self._output_path = None
        self._rec_start = None
        self._frames_written = 0

        return path, duration

    def _on_frame(self, frame, timestamp):
        """Listener callback: write frame to video file."""
        if self._recording and self._writer is not None:
            out = self._apply_overlay(frame)
            self._writer.write(out)
            self._frames_written += 1

    def _apply_overlay(self, frame, is_preroll=False):
        """Apply timecode overlay if configured."""
        if self.timecode is None:
            return frame
        out = frame.copy()
        if is_preroll:
            # Don't burn timecode on pre-roll frames, just the label + timestamp
            return out
        return self.timecode.render(out)

    def get_preview_frame(self):
        """Get the latest frame for preview display."""
        ts, frame = self.grabber.get_latest()
        if frame is None:
            return None
        return frame.copy()

    @property
    def is_recording(self):
        return self._recording

    @property
    def elapsed(self):
        if self._rec_start is None:
            return 0
        return time.time() - self._rec_start


class MultiCamSession:
    """Manages multiple cameras recording simultaneously.

    Usage:
        session = MultiCamSession(output_dir="./recordings", base_name="shoot")
        session.add_camera(CameraConfig(device_index=0, name="front"))
        session.add_camera(CameraConfig(device_index=1, name="side"))
        session.open_all()
        session.start_all()   # start recording on all cameras
        # ... time passes ...
        session.stop_all()
        session.close_all()
    """

    def __init__(self, output_dir=".", base_name="multicam"):
        self.output_dir = output_dir
        self.base_name = base_name
        self.streams = []

    def add_camera(self, config):
        """Add a camera configuration to the session."""
        stream = CameraStream(config, self.output_dir, self.base_name)
        self.streams.append(stream)
        return stream

    def open_all(self):
        """Open all cameras (start capture threads)."""
        for stream in self.streams:
            stream.open()
            print(f"  Opened: {stream.config.name} (device {stream.config.device_index})")

    def close_all(self):
        """Close all cameras."""
        for stream in self.streams:
            stream.close()

    def start_all(self):
        """Start recording on all cameras simultaneously."""
        paths = []
        for stream in self.streams:
            path = stream.start_recording()
            paths.append(path)
            print(f"  Recording: {stream.config.name} -> {os.path.basename(path)}")
        return paths

    def stop_all(self):
        """Stop recording on all cameras."""
        results = []
        for stream in self.streams:
            path, dur = stream.stop_recording()
            results.append((stream.config.name, path, dur))
            if path:
                print(f"  Stopped: {stream.config.name} -> {os.path.basename(path)} ({dur:.1f}s)")
        return results

    @property
    def is_recording(self):
        return any(s.is_recording for s in self.streams)

    def get_composite_preview(self, max_width=1280):
        """Build a composite preview image showing all camera feeds in a grid.

        Returns a single BGR image with all feeds tiled, or None.
        """
        frames = []
        for stream in self.streams:
            f = stream.get_preview_frame()
            if f is not None:
                frames.append((stream.config.name, f, stream.is_recording))

        if not frames:
            return None

        n = len(frames)
        # Grid layout: aim for roughly square
        cols = 1
        if n >= 2:
            cols = 2
        if n >= 5:
            cols = 3
        rows = (n + cols - 1) // cols

        # Determine cell size
        cell_w = max_width // cols
        cell_h = None

        resized = []
        for name, frame, recording in frames:
            fh, fw = frame.shape[:2]
            scale = cell_w / fw
            new_h = int(fh * scale)
            if cell_h is None:
                cell_h = new_h
            thumb = cv2.resize(frame, (cell_w, cell_h))

            # Label
            cv2.rectangle(thumb, (0, 0), (cell_w, 20), (0, 0, 0), -1)
            color = (0, 0, 255) if recording else (200, 200, 200)
            indicator = " [REC]" if recording else ""
            cv2.putText(thumb, f"{name}{indicator}", (4, 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
            resized.append(thumb)

        # Pad to fill grid
        if cell_h is None:
            cell_h = cell_w * 9 // 16
        while len(resized) < rows * cols:
            resized.append(np.zeros((cell_h, cell_w, 3), dtype=np.uint8))

        # Assemble grid
        row_imgs = []
        for r in range(rows):
            row_frames = resized[r * cols:(r + 1) * cols]
            row_imgs.append(np.hstack(row_frames))

        return np.vstack(row_imgs)

    def show_preview(self):
        """Open a composite preview window. Blocks until Q is pressed."""
        window = "MultiCam Preview"
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        print("MultiCam preview: Q=quit, R=toggle recording on all, SPACE=snapshot")

        try:
            while True:
                composite = self.get_composite_preview()
                if composite is not None:
                    cv2.imshow(window, composite)

                key = cv2.waitKey(33) & 0xFF
                if key in (ord('q'), ord('Q'), 27):
                    break
                elif key in (ord('r'), ord('R')):
                    if self.is_recording:
                        self.stop_all()
                    else:
                        self.start_all()
                elif key == ord(' '):
                    if composite is not None:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        path = os.path.join(self.output_dir, f"multicam_snapshot_{ts}.png")
                        cv2.imwrite(path, composite)
                        print(f"  Snapshot: {path}")
        finally:
            cv2.destroyWindow(window)
