"""Audio level meter using ffmpeg to capture audio device levels.

Provides real-time audio level monitoring for any AVFoundation audio device.
Uses ffmpeg's ebur128 or volumedetect filters to measure levels without
needing pyaudio or portaudio dependencies.

Can be displayed as:
  - Terminal VU meter (text bar)
  - OpenCV overlay on preview window
"""

import re
import subprocess
import threading
import time

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


class AudioLevelMeter:
    """Monitors audio levels from an AVFoundation audio device via ffmpeg.

    Runs ffmpeg in a background thread, parsing volume levels from the
    astats filter output in real-time.
    """

    def __init__(self, audio_device_index=0, sample_rate=44100):
        self.audio_device_index = audio_device_index
        self.sample_rate = sample_rate

        self._process = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()

        # Current levels (dBFS)
        self._peak_db = -60.0
        self._rms_db = -60.0
        # Smoothed for display
        self._display_peak = -60.0
        self._display_rms = -60.0
        self._peak_hold = -60.0
        self._peak_hold_time = 0

    @property
    def peak_db(self):
        with self._lock:
            return self._display_peak

    @property
    def rms_db(self):
        with self._lock:
            return self._display_rms

    @property
    def peak_hold_db(self):
        with self._lock:
            return self._peak_hold

    def start(self):
        """Start monitoring audio levels."""
        cmd = [
            "ffmpeg",
            "-f", "avfoundation",
            "-i", f"none:{self.audio_device_index}",
            "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.Peak_level:key=lavfi.astats.Overall.RMS_level",
            "-f", "null", "-",
        ]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            print("Audio meter: ffmpeg not found")
            return

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self):
        """Parse ffmpeg stderr for audio level metadata."""
        peak_pattern = re.compile(r'Peak_level=(-?\d+\.?\d*)')
        rms_pattern = re.compile(r'RMS_level=(-?\d+\.?\d*)')

        while self._running and self._process:
            line = self._process.stderr.readline()
            if not line:
                if self._process.poll() is not None:
                    break
                continue

            line = line.decode("utf-8", errors="replace")

            peak_m = peak_pattern.search(line)
            rms_m = rms_pattern.search(line)

            with self._lock:
                now = time.time()

                if peak_m:
                    val = float(peak_m.group(1))
                    if val != float('-inf'):
                        self._peak_db = val

                if rms_m:
                    val = float(rms_m.group(1))
                    if val != float('-inf'):
                        self._rms_db = val

                # Smoothing: fast attack, slow decay
                if self._peak_db > self._display_peak:
                    self._display_peak = self._peak_db
                else:
                    self._display_peak += (self._peak_db - self._display_peak) * 0.3

                if self._rms_db > self._display_rms:
                    self._display_rms = self._rms_db
                else:
                    self._display_rms += (self._rms_db - self._display_rms) * 0.2

                # Peak hold: stays at max for 2 seconds then decays
                if self._display_peak > self._peak_hold:
                    self._peak_hold = self._display_peak
                    self._peak_hold_time = now
                elif now - self._peak_hold_time > 2.0:
                    self._peak_hold += (self._display_peak - self._peak_hold) * 0.1

    def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


def db_to_bar(db, min_db=-60.0, max_db=0.0, width=40):
    """Convert dB value to a text bar string."""
    db = max(min_db, min(max_db, db))
    fraction = (db - min_db) / (max_db - min_db)
    filled = int(fraction * width)
    bar = "#" * filled + "-" * (width - filled)

    # Color zones by position
    if db > -3:
        label = "CLIP!"
    elif db > -12:
        label = "HOT  "
    else:
        label = "     "

    return f"|{bar}| {db:6.1f} dB {label}"


def format_meter_text(meter, width=40):
    """Format a complete VU meter display for terminal."""
    peak = meter.peak_db
    rms = meter.rms_db
    hold = meter.peak_hold_db

    lines = [
        f"Peak: {db_to_bar(peak, width=width)}",
        f"RMS:  {db_to_bar(rms, width=width)}",
        f"Hold: {hold:6.1f} dB",
    ]
    return "\n".join(lines)


def draw_audio_meter(image, meter, x=None, y=None, width=None, height=20):
    """Draw an audio level meter bar on an OpenCV image.

    Args:
        image: BGR image to draw on (modified in place).
        meter: AudioLevelMeter instance.
        x, y: Position (defaults to bottom-right area).
        width: Bar width in pixels (defaults to 1/3 of image width).
        height: Bar height in pixels.
    """
    if not HAS_CV2:
        return image

    img_h, img_w = image.shape[:2]
    if width is None:
        width = img_w // 3
    if x is None:
        x = img_w - width - 8
    if y is None:
        y = img_h - height - 32  # above the thermal HUD bar if present

    peak = meter.peak_db
    rms = meter.rms_db
    hold = meter.peak_hold_db

    min_db, max_db = -60.0, 0.0

    def _db_to_x(db):
        db = max(min_db, min(max_db, db))
        frac = (db - min_db) / (max_db - min_db)
        return x + int(frac * width)

    # Background
    cv2.rectangle(image, (x - 1, y - 1), (x + width + 1, y + height + 1),
                  (40, 40, 40), -1)

    # RMS bar (green -> yellow -> red)
    rms_x = _db_to_x(rms)
    green_end = _db_to_x(-12)
    yellow_end = _db_to_x(-3)

    if rms_x > x:
        # Green zone
        gx = min(rms_x, green_end)
        if gx > x:
            cv2.rectangle(image, (x, y), (gx, y + height), (0, 180, 0), -1)
        # Yellow zone
        if rms_x > green_end:
            yx = min(rms_x, yellow_end)
            cv2.rectangle(image, (green_end, y), (yx, y + height), (0, 200, 200), -1)
        # Red zone
        if rms_x > yellow_end:
            cv2.rectangle(image, (yellow_end, y), (rms_x, y + height), (0, 0, 220), -1)

    # Peak indicator (white line)
    peak_x = _db_to_x(peak)
    cv2.line(image, (peak_x, y), (peak_x, y + height), (255, 255, 255), 2)

    # Peak hold indicator (yellow tick)
    hold_x = _db_to_x(hold)
    cv2.line(image, (hold_x, y), (hold_x, y + height), (0, 255, 255), 1)

    # Scale markings
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.3
    for db_mark in [-48, -36, -24, -12, -6, -3, 0]:
        mx = _db_to_x(db_mark)
        cv2.line(image, (mx, y + height), (mx, y + height + 3), (150, 150, 150), 1)
        cv2.putText(image, str(db_mark), (mx - 6, y + height + 12),
                    font, font_scale, (150, 150, 150), 1)

    # Label
    cv2.putText(image, "AUDIO", (x, y - 3), font, 0.35, (200, 200, 200), 1)

    return image
