"""Core recording engine using ffmpeg subprocess for macOS AVFoundation capture.

Supports:
- H.264 (AVC) and H.265 (HEVC) video codecs
- MOV and MP4 containers
- Separate audio device selection
- Configurable resolution, frame rate, and quality (CRF)
"""

import os
import subprocess
import sys
import time
from datetime import datetime


CODEC_MAP = {
    "h264": "libx264",
    "avc": "libx264",
    "h265": "libx265",
    "hevc": "libx265",
}

# CRF ranges: lower = better quality, bigger file
# Good defaults: 18-23 for h264, 22-28 for h265
DEFAULT_CRF = {"libx264": 20, "libx265": 24}

CONTAINER_EXT = {
    "mov": ".mov",
    "mp4": ".mp4",
}


class RecordingSession:
    """Manages a single ffmpeg recording process."""

    def __init__(
        self,
        video_device=0,
        audio_device=None,
        width=1920,
        height=1080,
        fps=30,
        codec="h264",
        container="mov",
        crf=None,
        output_dir=".",
        base_name="recording",
        max_duration=None,
    ):
        self.video_device = video_device
        self.audio_device = audio_device
        self.width = width
        self.height = height
        self.fps = fps
        self.codec_name = codec.lower()
        self.encoder = CODEC_MAP.get(self.codec_name, "libx264")
        self.container = container.lower()
        self.crf = crf if crf is not None else DEFAULT_CRF.get(self.encoder, 20)
        self.output_dir = output_dir
        self.base_name = base_name
        self.max_duration = max_duration

        self._process = None
        self._output_path = None
        self._start_time = None

    def _build_output_path(self):
        os.makedirs(self.output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = CONTAINER_EXT.get(self.container, ".mov")
        filename = f"{self.base_name}_{ts}{ext}"
        return os.path.join(self.output_dir, filename)

    def _build_ffmpeg_cmd(self, output_path):
        cmd = ["ffmpeg", "-y"]

        # Input: AVFoundation
        cmd += ["-f", "avfoundation"]
        cmd += ["-framerate", str(self.fps)]
        cmd += ["-video_size", f"{self.width}x{self.height}"]

        # Device selection: "video_index:audio_index" or just "video_index"
        if self.audio_device is not None:
            cmd += ["-i", f"{self.video_device}:{self.audio_device}"]
        else:
            cmd += ["-i", f"{self.video_device}:none"]

        # Video encoding
        cmd += ["-c:v", self.encoder]
        cmd += ["-crf", str(self.crf)]
        cmd += ["-preset", "fast"]

        # Pixel format for compatibility
        cmd += ["-pix_fmt", "yuv420p"]

        # Audio encoding (if audio device selected)
        if self.audio_device is not None:
            cmd += ["-c:a", "aac", "-b:a", "192k"]

        # Duration limit
        if self.max_duration:
            cmd += ["-t", str(self.max_duration)]

        # Container-specific flags
        if self.container == "mov":
            cmd += ["-movflags", "+faststart"]
        elif self.container == "mp4":
            cmd += ["-movflags", "+faststart"]

        cmd.append(output_path)
        return cmd

    def start(self):
        """Start recording. Returns the output file path."""
        if self._process is not None:
            raise RuntimeError("Recording already in progress")

        self._output_path = self._build_output_path()
        cmd = self._build_ffmpeg_cmd(self._output_path)

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._start_time = time.time()
        return self._output_path

    def stop(self):
        """Stop recording gracefully. Returns (output_path, duration_seconds)."""
        if self._process is None:
            return None, 0

        # Send 'q' to ffmpeg stdin for graceful stop
        try:
            self._process.stdin.write(b"q")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

        duration = time.time() - self._start_time if self._start_time else 0
        path = self._output_path

        self._process = None
        self._output_path = None
        self._start_time = None

        return path, duration

    def is_recording(self):
        """Check if recording is currently active."""
        if self._process is None:
            return False
        return self._process.poll() is None

    @property
    def elapsed(self):
        """Seconds since recording started."""
        if self._start_time is None:
            return 0
        return time.time() - self._start_time


def one_shot_record(
    video_device=0,
    audio_device=None,
    width=1920,
    height=1080,
    fps=30,
    codec="h264",
    container="mov",
    crf=None,
    output_dir=".",
    base_name="recording",
    duration=10,
):
    """Record a single clip of specified duration, then exit."""
    session = RecordingSession(
        video_device=video_device,
        audio_device=audio_device,
        width=width,
        height=height,
        fps=fps,
        codec=codec,
        container=container,
        crf=crf,
        output_dir=output_dir,
        base_name=base_name,
        max_duration=duration,
    )

    output_path = session.start()
    print(f"Recording to: {output_path}")
    print(f"Duration: {duration}s | {width}x{height}@{fps} | {codec} | crf={session.crf}")

    # Wait for ffmpeg to finish (it will stop after -t duration)
    try:
        session._process.wait()
    except KeyboardInterrupt:
        session.stop()
        print("\nRecording interrupted.")
        return output_path

    # Clean up properly via stop() to close pipes and reset state
    session.stop()
    print(f"Done. Saved: {output_path}")
    return output_path
