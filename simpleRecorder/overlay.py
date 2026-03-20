"""Timestamp and timecode overlay renderer.

Burns timecodes, timestamps, and custom text onto video frames.
Designed to be used inline in the capture pipeline.
"""

import time
from datetime import datetime

import cv2
import numpy as np


class TimecodeOverlay:
    """Renders timecode/timestamp text onto frames.

    Supports:
    - Wall clock timestamp (2024-01-15 14:30:22)
    - Recording timecode (HH:MM:SS:FF)
    - Custom label text
    - Configurable position, font size, background opacity
    """

    # Position presets
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"

    def __init__(
        self,
        show_timestamp=True,
        show_timecode=True,
        label="",
        position=BOTTOM_LEFT,
        font_scale=None,
        bg_opacity=0.5,
        text_color=(255, 255, 255),
        rec_indicator=True,
    ):
        self.show_timestamp = show_timestamp
        self.show_timecode = show_timecode
        self.label = label
        self.position = position
        self.font_scale = font_scale  # None = auto-scale to frame size
        self.bg_opacity = bg_opacity
        self.text_color = text_color
        self.rec_indicator = rec_indicator

        self._rec_start_time = None
        self._frame_count = 0
        self._fps = 30
        self._is_recording = False

    def start_recording(self, fps=30):
        """Mark the start of a recording for timecode calculation."""
        self._rec_start_time = time.time()
        self._frame_count = 0
        self._fps = fps
        self._is_recording = True

    def stop_recording(self):
        self._is_recording = False
        self._rec_start_time = None

    def render(self, frame):
        """Burn overlay text onto a frame (modifies in place). Returns frame."""
        h, w = frame.shape[:2]
        scale = self.font_scale or max(0.4, min(w, h) / 1200)
        font = cv2.FONT_HERSHEY_SIMPLEX
        thickness = max(1, int(scale * 2))

        lines = []

        if self.label:
            lines.append(self.label)

        if self.show_timestamp:
            lines.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        if self.show_timecode and self._is_recording and self._rec_start_time is not None:
            elapsed = time.time() - self._rec_start_time
            tc = self._format_timecode(elapsed, self._fps)
            prefix = "REC " if self.rec_indicator else ""
            lines.append(f"{prefix}{tc}")
            self._frame_count += 1

        if not lines:
            return frame

        # Measure text block size
        line_sizes = []
        for line in lines:
            (tw, th), baseline = cv2.getTextSize(line, font, scale, thickness)
            line_sizes.append((tw, th, baseline))

        line_height = max(th + baseline for _, th, baseline in line_sizes) + 4
        block_w = max(tw for tw, _, _ in line_sizes) + 16
        block_h = line_height * len(lines) + 8

        # Calculate position
        margin = 8
        if self.position == self.TOP_LEFT:
            x0, y0 = margin, margin
        elif self.position == self.TOP_RIGHT:
            x0, y0 = w - block_w - margin, margin
        elif self.position == self.BOTTOM_RIGHT:
            x0, y0 = w - block_w - margin, h - block_h - margin
        else:  # BOTTOM_LEFT
            x0, y0 = margin, h - block_h - margin

        # Draw semi-transparent background
        if self.bg_opacity > 0:
            overlay = frame.copy()
            cv2.rectangle(overlay, (x0, y0), (x0 + block_w, y0 + block_h), (0, 0, 0), -1)
            cv2.addWeighted(overlay, self.bg_opacity, frame, 1 - self.bg_opacity, 0, frame)

        # Draw text
        for i, line in enumerate(lines):
            ty = y0 + (i + 1) * line_height
            # Red dot for REC indicator
            if line.startswith("REC ") and self.rec_indicator:
                cv2.circle(frame, (x0 + 10, ty - 6), 5, (0, 0, 255), -1)
                cv2.putText(frame, line[4:], (x0 + 20, ty), font, scale,
                            self.text_color, thickness)
            else:
                cv2.putText(frame, line, (x0 + 8, ty), font, scale,
                            self.text_color, thickness)

        return frame

    @staticmethod
    def _format_timecode(elapsed_seconds, fps):
        """Format elapsed time as HH:MM:SS:FF timecode."""
        total_frames = int(elapsed_seconds * fps)
        ff = total_frames % int(fps)
        total_seconds = total_frames // int(fps)
        ss = total_seconds % 60
        mm = (total_seconds // 60) % 60
        hh = total_seconds // 3600
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"
