"""Live preview window using OpenCV highgui.

Works with three camera types:
  1. Regular cameras — uses unified pipeline (capture+overlay+encode)
  2. InfiRay thermal — colorized thermal with temp overlay + radiometric export
  3. Waveshare thermal — colorized thermal with temp overlay + radiometric export

Features:
  - Preview-while-recording (all modes)
  - Pre-roll buffer (regular mode via pipeline)
  - Temperature range lock (thermal modes)
  - Radiometric sidecar recording (thermal modes)
  - Audio level meter overlay
"""

import os
import time

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from thermal import (
    InfiRayCamera, WaveshareCamera, ThermalFrame,
    RadiometricRecorder, draw_temp_overlay, COLORMAPS, DEFAULT_COLORMAP,
)
from audiometer import AudioLevelMeter, draw_audio_meter

# Minimum display size — thermal cameras are tiny, so we upscale
MIN_DISPLAY_WIDTH = 640
MIN_DISPLAY_HEIGHT = 480


def _upscale_for_display(image, min_w=MIN_DISPLAY_WIDTH, min_h=MIN_DISPLAY_HEIGHT):
    """Upscale a small image so it's usable on screen. Returns (image, scale)."""
    h, w = image.shape[:2]
    scale = 1.0
    if w < min_w or h < min_h:
        scale = max(min_w / w, min_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
    return image, scale


class PreviewWindow:
    """OpenCV highgui preview window for any camera type.

    For regular cameras, delegates to RecordingPipeline for preview-while-record.
    For thermal cameras, handles colorized preview + radiometric recording.

    Usage:
        # Regular camera (with preview-while-record and pre-roll)
        preview = PreviewWindow(mode="regular", device_index=0)

        # InfiRay thermal with range lock
        preview = PreviewWindow(mode="infiray", device_index=2,
                                colormap="inferno", range_lock=(20, 45))

        preview.run()  # blocks until user quits
    """

    def __init__(
        self,
        mode="regular",
        device_index=0,
        width=1920,
        height=1080,
        fps=30,
        colormap=DEFAULT_COLORMAP,
        waveshare_fps=15,
        codec="h264",
        container="mov",
        crf=20,
        output_dir=".",
        base_name="recording",
        pre_roll_seconds=5.0,
        range_lock=None,
        audio_device=None,
        overlay=True,
    ):
        if not HAS_CV2:
            raise RuntimeError("opencv-python is required for preview")

        self.mode = mode
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.colormap = colormap
        self.waveshare_fps = waveshare_fps
        self.codec = codec
        self.container = container
        self.crf = crf
        self.output_dir = output_dir
        self.base_name = base_name
        self.pre_roll_seconds = pre_roll_seconds
        self.range_lock = range_lock
        self.audio_device = audio_device
        self.overlay = overlay

        self._cam = None
        self._writer = None
        self._radiometric = None
        self._audio_meter = None
        self._recording = False
        self._rec_start = None
        self._clip_count = 0
        self._colormap_names = list(COLORMAPS.keys())
        self._colormap_idx = (
            self._colormap_names.index(colormap)
            if colormap in self._colormap_names else 0
        )

    def run(self):
        """Open preview window and block until user quits."""
        # For regular cameras, use the full pipeline
        if self.mode == "regular":
            self._run_pipeline()
            return

        # For thermal cameras, use direct thermal backend
        self._run_thermal()

    def _run_pipeline(self):
        """Regular camera: delegate to RecordingPipeline."""
        from pipeline import RecordingPipeline

        pipe = RecordingPipeline(
            device_index=self.device_index,
            width=self.width,
            height=self.height,
            fps=self.fps,
            codec=self.codec,
            container=self.container,
            crf=self.crf,
            output_dir=self.output_dir,
            base_name=self.base_name,
            pre_roll_seconds=self.pre_roll_seconds,
            overlay=self.overlay,
            audio_device=self.audio_device,
        )
        pipe.open()
        try:
            pipe.show_preview()
        finally:
            pipe.close()

    def _run_thermal(self):
        """Thermal camera preview with recording + radiometric export."""
        self._open_camera()

        # Audio meter
        if self.audio_device is not None:
            self._audio_meter = AudioLevelMeter(self.audio_device)
            self._audio_meter.start()

        window_name = f"simpleRecorder Thermal [{self.mode}]"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

        range_str = f"  range:{self.range_lock}" if self.range_lock else "  range:auto"
        print(f"Thermal preview: {self.mode} mode{range_str}")
        print("Keys: Q=quit  R=record  C=cycle colormap  SPACE=snapshot")
        print("       L=lock range to current  U=unlock range (auto)")

        frame_count = 0
        fps_time = time.time()
        display_fps = 0.0

        try:
            while True:
                image, thermal_frame = self._read_frame()
                if image is None:
                    print("Preview: lost camera feed")
                    break

                display, scale = _upscale_for_display(image)

                # Draw thermal overlay
                if thermal_frame is not None:
                    draw_temp_overlay(display, thermal_frame, scale)

                    # Write radiometric data if recording
                    if self._recording and self._radiometric is not None:
                        self._radiometric.write_frame(thermal_frame)

                # Audio meter
                if self._audio_meter is not None:
                    draw_audio_meter(display, self._audio_meter)

                # FPS counter
                frame_count += 1
                elapsed = time.time() - fps_time
                if elapsed >= 1.0:
                    display_fps = frame_count / elapsed
                    frame_count = 0
                    fps_time = time.time()

                # HUD
                h, w = display.shape[:2]
                rec_str = f"  ** REC {self._format_elapsed()} **" if self._recording else ""
                lock_str = f"  [{self.range_lock[0]:.0f}-{self.range_lock[1]:.0f}C]" if self.range_lock else ""
                hud = f"{display_fps:.0f}fps  {self.mode}  {self.colormap}{lock_str}{rec_str}"
                cv2.rectangle(display, (0, 0), (w, 22), (0, 0, 0), -1)
                cv2.putText(display, hud, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (0, 0, 255) if self._recording else (200, 200, 200), 1)

                # Write colorized frame to video if recording
                if self._recording and self._writer is not None:
                    self._writer.write(image)

                cv2.imshow(window_name, display)

                key = cv2.waitKey(1) & 0xFF
                if key in (ord('q'), ord('Q'), 27):
                    break
                elif key in (ord('r'), ord('R')):
                    self._toggle_recording(image, thermal_frame)
                elif key in (ord('c'), ord('C')):
                    self._cycle_colormap()
                elif key == ord(' '):
                    self._save_snapshot(image, thermal_frame)
                elif key in (ord('l'), ord('L')):
                    if thermal_frame is not None:
                        self.range_lock = (thermal_frame.min_temp, thermal_frame.max_temp)
                        self._cam.range_lock = self.range_lock
                        print(f"Range locked: {self.range_lock[0]:.1f} - {self.range_lock[1]:.1f} C")
                elif key in (ord('u'), ord('U')):
                    self.range_lock = None
                    self._cam.range_lock = None
                    print("Range unlocked (auto-scale)")

        finally:
            self._cleanup()
            cv2.destroyAllWindows()

    def _open_camera(self):
        """Open the appropriate thermal camera backend."""
        if self.mode == "infiray":
            self._cam = InfiRayCamera(self.device_index, self.colormap, self.range_lock)
            self._cam.open()
        elif self.mode == "waveshare":
            self._cam = WaveshareCamera(self.colormap, self.waveshare_fps, self.range_lock)
            self._cam.open()
        else:
            raise ValueError(f"Unknown thermal mode: {self.mode}")

    def _read_frame(self):
        """Read one frame. Returns (bgr_image, ThermalFrame_or_None)."""
        tf = self._cam.read()
        if tf is None:
            return None, None
        return tf.colorized.copy(), tf

    def _toggle_recording(self, current_frame, thermal_frame=None):
        """Toggle video + radiometric recording on/off."""
        if not self._recording:
            os.makedirs(self.output_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            video_path = os.path.join(
                self.output_dir, f"{self.base_name}_thermal_{ts}.avi"
            )

            h, w = current_frame.shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            fps = self.waveshare_fps if self.mode == "waveshare" else 15
            self._writer = cv2.VideoWriter(video_path, fourcc, fps, (w, h))

            # Start radiometric sidecar
            if thermal_frame is not None:
                self._radiometric = RadiometricRecorder(video_path)
                self._radiometric.open(
                    thermal_frame.width, thermal_frame.height, fps
                )

            self._recording = True
            self._rec_start = time.time()
            self._clip_count += 1
            print(f"Recording #{self._clip_count}: {os.path.basename(video_path)}")
            if self._radiometric:
                print(f"  Radiometric: {os.path.basename(self._radiometric.csv_path)}")
        else:
            if self._writer is not None:
                self._writer.release()
                self._writer = None
            if self._radiometric is not None:
                self._radiometric.close()
                self._radiometric = None
            dur = time.time() - self._rec_start if self._rec_start else 0
            self._recording = False
            self._rec_start = None
            print(f"Recording stopped ({dur:.1f}s)")

    def _cycle_colormap(self):
        """Cycle to the next colormap."""
        self._colormap_idx = (self._colormap_idx + 1) % len(self._colormap_names)
        self.colormap = self._colormap_names[self._colormap_idx]
        if hasattr(self._cam, 'colormap'):
            self._cam.colormap = self.colormap
        print(f"Colormap: {self.colormap}")

    def _save_snapshot(self, image, thermal_frame):
        """Save a PNG snapshot (and raw temps if thermal)."""
        ts = time.strftime("%Y%m%d_%H%M%S")
        snap_dir = self.output_dir
        os.makedirs(snap_dir, exist_ok=True)

        filename = os.path.join(snap_dir, f"snapshot_{ts}.png")
        cv2.imwrite(filename, image)
        print(f"Snapshot: {filename}")

        if thermal_frame is not None:
            raw_file = os.path.join(snap_dir, f"snapshot_{ts}_raw.npy")
            np.save(raw_file, thermal_frame.raw_temps)
            print(f"Raw temps: {raw_file}")

    def _format_elapsed(self):
        if self._rec_start is None:
            return "00:00:00"
        e = time.time() - self._rec_start
        mins, secs = divmod(int(e), 60)
        hrs, mins = divmod(mins, 60)
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"

    def _cleanup(self):
        """Release all resources."""
        if self._recording:
            if self._writer is not None:
                self._writer.release()
                self._writer = None
            if self._radiometric is not None:
                self._radiometric.close()
                self._radiometric = None

        if self._audio_meter is not None:
            self._audio_meter.stop()
            self._audio_meter = None

        if self._cam is not None:
            if hasattr(self._cam, 'close'):
                self._cam.close()
            elif hasattr(self._cam, 'release'):
                self._cam.release()
            self._cam = None
