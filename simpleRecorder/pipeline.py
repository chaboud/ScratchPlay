"""Unified recording pipeline: capture -> overlay -> preview -> encode.

This is the main workhorse that replaces the old separate capture paths.
It ties together:
  - FrameGrabber (OpenCV capture with pre-roll buffer)
  - TimecodeOverlay (optional burned-in timecodes)
  - AudioLevelMeter (optional audio monitoring)
  - OpenCV preview window (live view while recording)
  - ffmpeg encoding via pipe (H.264/HEVC to MOV/MP4, with audio)

For thermal cameras, use the thermal-specific preview path in preview.py
which handles colorization and radiometric recording.
"""

import atexit
import os
import signal
import subprocess
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from capture import FrameGrabber
from overlay import TimecodeOverlay
from audiometer import AudioLevelMeter, draw_audio_meter


class PipeEncoder:
    """Encodes frames by piping raw video to ffmpeg.

    This lets us use ffmpeg's full codec support (h264, hevc, etc.)
    while capturing frames through OpenCV.
    """

    def __init__(
        self,
        output_path,
        width,
        height,
        fps,
        codec="h264",
        crf=20,
        audio_device=None,
        pix_fmt="bgr24",
    ):
        self.output_path = output_path
        self.width = width
        self.height = height
        self.fps = fps
        self.codec = codec
        self.crf = crf
        self.audio_device = audio_device
        self.pix_fmt = pix_fmt
        self._process = None

    def _encoder_name(self):
        c = self.codec.lower()
        if c in ("h264", "avc"):
            return "libx264"
        elif c in ("h265", "hevc"):
            return "libx265"
        return "libx264"

    def open(self):
        """Start ffmpeg process, ready to receive piped frames."""
        cmd = ["ffmpeg", "-y"]

        # Video input: raw frames from pipe
        cmd += [
            "-f", "rawvideo",
            "-pix_fmt", self.pix_fmt,
            "-s", f"{self.width}x{self.height}",
            "-r", str(self.fps),
            "-i", "pipe:0",
        ]

        # Audio input (optional): capture from AVFoundation device
        if self.audio_device is not None:
            cmd += [
                "-f", "avfoundation",
                "-i", f"none:{self.audio_device}",
            ]

        # Video encoding
        cmd += [
            "-c:v", self._encoder_name(),
            "-crf", str(self.crf),
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
        ]

        # Audio encoding
        if self.audio_device is not None:
            cmd += ["-c:a", "aac", "-b:a", "192k"]

        cmd += ["-movflags", "+faststart"]
        cmd.append(self.output_path)

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        atexit.register(self._atexit_cleanup)

    def write_frame(self, frame):
        """Write a BGR frame to the encoder."""
        if self._process is None or self._process.poll() is not None:
            return False
        try:
            self._process.stdin.write(frame.tobytes())
            return True
        except (BrokenPipeError, OSError):
            return False

    def close(self):
        """Close the pipe and wait for ffmpeg to finish."""
        if self._process is None:
            return
        try:
            self._process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._kill_process()
        self._process = None

    def _kill_process(self):
        """Kill the ffmpeg process and its process group."""
        if self._process is None:
            return
        try:
            os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            self._process.kill()
        except OSError:
            pass
        try:
            self._process.wait(timeout=2)
        except Exception:
            pass

    def _atexit_cleanup(self):
        """Safety net: kill ffmpeg on interpreter exit."""
        if self._process is not None and self._process.poll() is None:
            self._kill_process()
            self._process = None

    def __del__(self):
        """Last-resort cleanup."""
        try:
            self._atexit_cleanup()
        except Exception:
            pass


class RecordingPipeline:
    """Unified pipeline: capture -> overlay -> preview -> encode.

    Supports preview-while-recording and pre-roll buffer.

    Usage:
        pipe = RecordingPipeline(
            device_index=0, width=1920, height=1080, fps=30,
            codec="h264", output_dir="./recordings",
            pre_roll_seconds=5, overlay=True,
            audio_device=0,
        )
        pipe.open()
        pipe.show_preview()  # blocks until Q pressed; R toggles recording
    """

    def __init__(
        self,
        device_index=0,
        width=None,
        height=None,
        fps=30,
        codec="h264",
        container="mov",
        crf=20,
        output_dir=".",
        base_name="recording",
        pre_roll_seconds=5.0,
        overlay=True,
        overlay_position="bottom_left",
        overlay_label="",
        audio_device=None,
        preview_timecode=True,
        preview_meters=True,
        record_timecode=False,
        record_meters=False,
    ):
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.codec = codec
        self.container = container
        self.crf = crf
        self.output_dir = output_dir
        self.base_name = base_name
        self.pre_roll_seconds = pre_roll_seconds
        self.audio_device = audio_device

        # Overlay visibility flags
        self.preview_timecode = preview_timecode if overlay else False
        self.preview_meters = preview_meters
        self.record_timecode = record_timecode
        self.record_meters = record_meters

        self._grabber = None
        self._encoder = None
        self._recording = False
        self._rec_start = None
        self._output_path = None
        self._clip_count = 0

        # Overlay renderer (always created so it can be toggled)
        self._overlay = TimecodeOverlay(
            show_timestamp=True,
            show_timecode=True,
            label=overlay_label,
            position=overlay_position,
        )

        # Audio meter
        self._audio_meter = None

    def _signal_cleanup(self, signum, frame):
        """Handle SIGINT/SIGTERM: clean up and re-raise."""
        self.close()
        # Re-raise with default handler so the process exits properly
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def open(self):
        """Open the camera and start capturing."""
        # Register signal handlers to clean up ffmpeg on Ctrl+C
        signal.signal(signal.SIGINT, self._signal_cleanup)
        signal.signal(signal.SIGTERM, self._signal_cleanup)

        self._grabber = FrameGrabber(
            device_index=self.device_index,
            width=self.width,
            height=self.height,
            fps=self.fps,
            pre_roll_seconds=self.pre_roll_seconds,
        )
        self._grabber.open()

        # Update actual dimensions from camera
        self.width = self._grabber.width
        self.height = self._grabber.height
        self.fps = self._grabber.fps

        # Start audio meter if audio device specified
        if self.audio_device is not None:
            self._audio_meter = AudioLevelMeter(self.audio_device)
            self._audio_meter.start()

    def close(self):
        """Stop everything and release resources."""
        self.stop_recording()
        if self._audio_meter is not None:
            self._audio_meter.stop()
            self._audio_meter = None
        if self._grabber is not None:
            self._grabber.close()
            self._grabber = None

    def start_recording(self):
        """Start recording, including pre-roll buffer flush."""
        if self._recording:
            return self._output_path

        os.makedirs(self.output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = ".mov" if self.container == "mov" else ".mp4"
        self._output_path = os.path.join(
            self.output_dir, f"{self.base_name}_{ts}{ext}"
        )

        self._encoder = PipeEncoder(
            output_path=self._output_path,
            width=self.width,
            height=self.height,
            fps=self.fps,
            codec=self.codec,
            crf=self.crf,
            audio_device=self.audio_device,
        )
        self._encoder.open()

        # Flush pre-roll buffer
        pre_roll_frames = self._grabber.pre_roll.drain()
        for frame_ts, frame in pre_roll_frames:
            self._encoder.write_frame(frame)

        if self._overlay:
            self._overlay.start_recording(self.fps)

        self._recording = True
        self._rec_start = time.time()
        self._clip_count += 1

        # Register listener for live encoding
        self._grabber.add_listener(self._on_frame)

        return self._output_path

    def stop_recording(self):
        """Stop recording and finalize the file."""
        if not self._recording:
            return None, 0

        self._grabber.remove_listener(self._on_frame)
        self._recording = False

        if self._overlay:
            self._overlay.stop_recording()

        if self._encoder is not None:
            self._encoder.close()
            self._encoder = None

        duration = time.time() - self._rec_start if self._rec_start else 0
        path = self._output_path
        self._output_path = None
        self._rec_start = None

        return path, duration

    def _on_frame(self, frame, timestamp):
        """Listener: encode each frame as it's captured."""
        if self._recording and self._encoder is not None:
            out = frame.copy()
            if self.record_timecode:
                self._overlay.render(out)
            if self.record_meters and self._audio_meter is not None:
                draw_audio_meter(out, self._audio_meter)
            self._encoder.write_frame(out)

    @property
    def is_recording(self):
        return self._recording

    @property
    def elapsed(self):
        if self._rec_start is None:
            return 0
        return time.time() - self._rec_start

    def show_preview(self):
        """Open a live preview window. Blocks until user quits.

        Keys:
            Q / ESC  — quit
            R        — toggle recording
            SPACE    — save snapshot
            O        — toggle overlay on/off
        """
        window = f"simpleRecorder [{self.device_index}]"
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)

        print(f"Pipeline preview: {self.width}x{self.height}@{self.fps:.0f}")
        print(f"Pre-roll: {self.pre_roll_seconds}s buffer")
        if self.audio_device is not None:
            print(f"Audio meter: device {self.audio_device}")
        print("Keys: Q=quit  R=record  SPACE=snapshot  T=toggle timecode  M=toggle meters")

        frame_count = 0
        fps_time = time.time()
        display_fps = 0.0

        try:
            while True:
                ts, frame = self._grabber.get_latest()
                if frame is None:
                    time.sleep(0.01)
                    continue

                display = frame.copy()

                # Apply overlays to preview display
                if self.preview_timecode:
                    self._overlay.render(display)

                # Audio meter on preview
                if self.preview_meters and self._audio_meter is not None:
                    draw_audio_meter(display, self._audio_meter)

                # FPS counter
                frame_count += 1
                elapsed = time.time() - fps_time
                if elapsed >= 1.0:
                    display_fps = frame_count / elapsed
                    frame_count = 0
                    fps_time = time.time()

                # HUD top bar
                h, w = display.shape[:2]
                rec_str = f"  ** REC {self._format_elapsed()} **" if self._recording else ""
                pre_str = f"  buf:{self._grabber.pre_roll.count}f" if not self._recording else ""
                hud = f"{display_fps:.0f}fps  {self.width}x{self.height}{rec_str}{pre_str}"
                cv2.rectangle(display, (0, 0), (w, 22), (0, 0, 0), -1)
                cv2.putText(display, hud, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (0, 0, 255) if self._recording else (200, 200, 200), 1)

                cv2.imshow(window, display)

                key = cv2.waitKey(1) & 0xFF
                if key in (ord('q'), ord('Q'), 27):
                    break
                elif key in (ord('r'), ord('R')):
                    if self._recording:
                        path, dur = self.stop_recording()
                        print(f"Stopped: {os.path.basename(path)} ({dur:.1f}s)")
                    else:
                        path = self.start_recording()
                        print(f"Recording #{self._clip_count}: {os.path.basename(path)}")
                elif key == ord(' '):
                    snap_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    snap_path = os.path.join(self.output_dir, f"snapshot_{snap_ts}.png")
                    cv2.imwrite(snap_path, frame)
                    print(f"Snapshot: {snap_path}")
                elif key in (ord('t'), ord('T')):
                    self.preview_timecode = not self.preview_timecode
                    print(f"Preview timecode: {'on' if self.preview_timecode else 'off'}")
                elif key in (ord('m'), ord('M')):
                    self.preview_meters = not self.preview_meters
                    print(f"Preview meters: {'on' if self.preview_meters else 'off'}")

        finally:
            if self._recording:
                path, dur = self.stop_recording()
                print(f"Stopped: {os.path.basename(path)} ({dur:.1f}s)")
            cv2.destroyWindow(window)

    def _format_elapsed(self):
        e = self.elapsed
        mins, secs = divmod(int(e), 60)
        hrs, mins = divmod(mins, 60)
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
