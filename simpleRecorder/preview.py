"""Live preview window using OpenCV highgui.

Works with three camera types:
  1. Regular cameras — shows the raw video feed via ffmpeg capture
  2. InfiRay thermal — shows colorized thermal with temp overlay
  3. Waveshare thermal — shows colorized thermal with temp overlay

The preview can optionally record while showing the live view.
"""

import time
import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from thermal import (
    InfiRayCamera, WaveshareCamera, ThermalFrame,
    draw_temp_overlay, COLORMAPS, DEFAULT_COLORMAP,
)

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

    Usage:
        # Regular camera
        preview = PreviewWindow(mode="regular", device_index=0, width=1920, height=1080, fps=30)

        # InfiRay thermal
        preview = PreviewWindow(mode="infiray", device_index=2, colormap="inferno")

        # Waveshare thermal
        preview = PreviewWindow(mode="waveshare", colormap="turbo")

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
        record=False,
        output_path=None,
        codec="h264",
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
        self.record = record
        self.output_path = output_path
        self.codec = codec

        self._cam = None
        self._writer = None
        self._recording = False
        self._colormap_names = list(COLORMAPS.keys())
        self._colormap_idx = self._colormap_names.index(colormap) if colormap in self._colormap_names else 0

    def run(self):
        """Open preview window and block until user quits."""
        self._open_camera()

        window_name = f"simpleRecorder Preview [{self.mode}]"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

        print(f"Preview: {self.mode} mode")
        print("Keys: Q/ESC=quit  R=toggle recording  C=cycle colormap  SPACE=snapshot")

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

                # Draw thermal overlay if applicable
                if thermal_frame is not None:
                    draw_temp_overlay(display, thermal_frame, scale)

                # FPS counter
                frame_count += 1
                elapsed = time.time() - fps_time
                if elapsed >= 1.0:
                    display_fps = frame_count / elapsed
                    frame_count = 0
                    fps_time = time.time()

                # HUD: top bar
                h, w = display.shape[:2]
                rec_indicator = "  ** REC **" if self._recording else ""
                hud = f"{display_fps:.0f}fps  {self.mode}  cmap:{self.colormap}{rec_indicator}"
                cv2.rectangle(display, (0, 0), (w, 22), (0, 0, 0), -1)
                cv2.putText(display, hud, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (0, 255, 0) if self._recording else (200, 200, 200), 1)

                # Write to video if recording
                if self._recording and self._writer is not None:
                    self._writer.write(image)

                cv2.imshow(window_name, display)

                key = cv2.waitKey(1) & 0xFF
                if key in (ord('q'), ord('Q'), 27):  # q or ESC
                    break
                elif key in (ord('r'), ord('R')):
                    self._toggle_recording(image)
                elif key in (ord('c'), ord('C')):
                    self._cycle_colormap()
                elif key == ord(' '):
                    self._save_snapshot(image, thermal_frame)

        finally:
            self._cleanup()
            cv2.destroyAllWindows()

    def _open_camera(self):
        """Open the appropriate camera backend."""
        if self.mode == "infiray":
            self._cam = InfiRayCamera(self.device_index, self.colormap)
            self._cam.open()
        elif self.mode == "waveshare":
            self._cam = WaveshareCamera(self.colormap, self.waveshare_fps)
            self._cam.open()
        elif self.mode == "regular":
            self._cam = cv2.VideoCapture(self.device_index)
            if not self._cam.isOpened():
                raise RuntimeError(f"Cannot open video device {self.device_index}")
            self._cam.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self._cam.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self._cam.set(cv2.CAP_PROP_FPS, self.fps)
        else:
            raise ValueError(f"Unknown mode: {self.mode}")

    def _read_frame(self):
        """Read one frame. Returns (bgr_image, ThermalFrame_or_None)."""
        if self.mode == "infiray":
            tf = self._cam.read()
            if tf is None:
                return None, None
            return tf.colorized.copy(), tf

        elif self.mode == "waveshare":
            tf = self._cam.read()
            if tf is None:
                return None, None
            return tf.colorized.copy(), tf

        elif self.mode == "regular":
            ret, frame = self._cam.read()
            if not ret or frame is None:
                return None, None
            return frame, None

        return None, None

    def _toggle_recording(self, current_frame):
        """Toggle video recording on/off."""
        if not self._recording:
            if self.output_path is None:
                ts = time.strftime("%Y%m%d_%H%M%S")
                self.output_path = f"preview_{ts}.avi"

            h, w = current_frame.shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            fps = self.fps if self.mode == "regular" else 15
            self._writer = cv2.VideoWriter(self.output_path, fourcc, fps, (w, h))
            self._recording = True
            print(f"Recording started: {self.output_path}")
        else:
            if self._writer is not None:
                self._writer.release()
                self._writer = None
            self._recording = False
            print(f"Recording stopped: {self.output_path}")
            self.output_path = None  # next recording gets a new name

    def _cycle_colormap(self):
        """Cycle to the next colormap (thermal modes only)."""
        if self.mode == "regular":
            return

        self._colormap_idx = (self._colormap_idx + 1) % len(self._colormap_names)
        self.colormap = self._colormap_names[self._colormap_idx]

        if hasattr(self._cam, 'colormap'):
            self._cam.colormap = self.colormap

        print(f"Colormap: {self.colormap}")

    def _save_snapshot(self, image, thermal_frame):
        """Save a PNG snapshot (and raw temps if thermal)."""
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"snapshot_{ts}.png"
        cv2.imwrite(filename, image)
        print(f"Snapshot saved: {filename}")

        if thermal_frame is not None:
            raw_file = f"snapshot_{ts}_raw.npy"
            np.save(raw_file, thermal_frame.raw_temps)
            print(f"Raw temps saved: {raw_file}")

    def _cleanup(self):
        """Release all resources."""
        if self._writer is not None:
            self._writer.release()
            self._writer = None

        if self._cam is not None:
            if hasattr(self._cam, 'close'):
                self._cam.close()
            elif hasattr(self._cam, 'release'):
                self._cam.release()
            self._cam = None
