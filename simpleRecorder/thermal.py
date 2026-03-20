"""Thermal camera backends for InfiRay (UVC) and Waveshare/Meridian (senxor).

InfiRay cameras (Topdon TC001/TC004, P2 Pro, etc.) present as standard UVC
devices with a doubled frame: top half is the visible image, bottom half is
raw 16-bit temperature data packed into pixel channels.

Waveshare/Meridian MI48 cameras use a proprietary serial-over-USB protocol
via the senxor/pysenxor library, returning a NumPy array of temperature values.

Both backends produce a unified ThermalFrame with:
  - raw_temps: 2D numpy array of temperatures in Celsius
  - colorized: BGR image with applied colormap
  - min/max temp and their locations
"""

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from senxor.mi48 import MI48
    from senxor.utils import connect_senxor
    HAS_SENXOR = True
except ImportError:
    HAS_SENXOR = False


# --- Colormaps ---

COLORMAPS = {
    "ironbow": cv2.COLORMAP_INFERNO if HAS_CV2 else None,
    "inferno": cv2.COLORMAP_INFERNO if HAS_CV2 else None,
    "jet": cv2.COLORMAP_JET if HAS_CV2 else None,
    "hot": cv2.COLORMAP_HOT if HAS_CV2 else None,
    "bone": cv2.COLORMAP_BONE if HAS_CV2 else None,
    "rainbow": cv2.COLORMAP_RAINBOW if HAS_CV2 else None,
    "magma": cv2.COLORMAP_MAGMA if HAS_CV2 else None,
    "turbo": cv2.COLORMAP_TURBO if HAS_CV2 else None,
    "white_hot": None,  # custom: simple grayscale
    "black_hot": None,  # custom: inverted grayscale
}

DEFAULT_COLORMAP = "inferno"


class ThermalFrame:
    """Unified thermal frame from any backend."""

    __slots__ = ("raw_temps", "colorized", "min_temp", "max_temp",
                 "min_loc", "max_loc", "width", "height")

    def __init__(self, raw_temps, colorized, min_temp, max_temp, min_loc, max_loc):
        self.raw_temps = raw_temps
        self.colorized = colorized
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.min_loc = min_loc  # (x, y)
        self.max_loc = max_loc  # (x, y)
        self.height, self.width = raw_temps.shape[:2]


def _normalize_and_colorize(temps, colormap_name):
    """Normalize temperature array to 0-255 and apply colormap.

    Args:
        temps: 2D numpy array of float temperatures (Celsius).
        colormap_name: Key from COLORMAPS dict.

    Returns:
        BGR image (uint8).
    """
    t_min = temps.min()
    t_max = temps.max()
    t_range = t_max - t_min
    if t_range < 0.01:
        t_range = 1.0  # avoid division by zero on uniform image

    normalized = ((temps - t_min) / t_range * 255).astype(np.uint8)

    if colormap_name == "white_hot":
        return cv2.cvtColor(normalized, cv2.COLOR_GRAY2BGR)
    elif colormap_name == "black_hot":
        return cv2.cvtColor(255 - normalized, cv2.COLOR_GRAY2BGR)

    cv_map = COLORMAPS.get(colormap_name, cv2.COLORMAP_INFERNO)
    if cv_map is None:
        cv_map = cv2.COLORMAP_INFERNO
    return cv2.applyColorMap(normalized, cv_map)


def _find_extremes(temps):
    """Find min/max temperatures and their (x, y) pixel locations."""
    min_val = float(temps.min())
    max_val = float(temps.max())
    min_idx = np.unravel_index(temps.argmin(), temps.shape)
    max_idx = np.unravel_index(temps.argmax(), temps.shape)
    # Convert (row, col) to (x, y)
    min_loc = (int(min_idx[1]), int(min_idx[0]))
    max_loc = (int(max_idx[1]), int(max_idx[0]))
    return min_val, max_val, min_loc, max_loc


# ---------------------------------------------------------------------------
# InfiRay UVC backend
# ---------------------------------------------------------------------------

class InfiRayCamera:
    """InfiRay thermal camera via standard UVC/OpenCV.

    These cameras (TC001, TC004, P2 Pro, etc.) present as a USB video device
    with a doubled frame. For a 256x192 sensor, the device reports 256x384:
      - Top 192 rows: pseudo-color or grayscale thermal image
      - Bottom 192 rows: raw 16-bit temperature data packed into YUV/RGB channels

    Temperature formula: temp_C = (hi_byte * 256 + lo_byte) / 64 - 273.15
    """

    def __init__(self, device_index=0, colormap=DEFAULT_COLORMAP):
        if not HAS_CV2:
            raise RuntimeError("opencv-python is required for InfiRay cameras")

        self.device_index = device_index
        self.colormap = colormap
        self._cap = None
        self._sensor_height = None  # detected on first frame

    def open(self):
        """Open the camera device."""
        self._cap = cv2.VideoCapture(self.device_index)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video device {self.device_index}")

        # Let the camera deliver its native format — don't force resolution
        # Read one frame to detect geometry
        ret, frame = self._cap.read()
        if not ret or frame is None:
            raise RuntimeError(f"Cannot read from video device {self.device_index}")

        h, w = frame.shape[:2]
        if h % 2 != 0:
            raise RuntimeError(
                f"Frame height {h} is odd — doesn't look like an InfiRay doubled frame"
            )
        self._sensor_height = h // 2
        print(f"InfiRay: detected {w}x{h} frame -> sensor is {w}x{self._sensor_height}")

    def read(self):
        """Read one frame, return ThermalFrame or None on failure."""
        if self._cap is None:
            return None

        ret, frame = self._cap.read()
        if not ret or frame is None:
            return None

        h = self._sensor_height
        # Split frame: top = visible image, bottom = raw thermal data
        imdata = frame[:h, :, :]
        thdata = frame[h:, :, :]

        # Extract raw 16-bit temperature from thermal half
        # Data is packed: low byte in green channel, high byte in red channel
        # (this is the common InfiRay UVC packing for YUYV)
        raw_temps = self._extract_temperatures(thdata)

        min_val, max_val, min_loc, max_loc = _find_extremes(raw_temps)
        colorized = _normalize_and_colorize(raw_temps, self.colormap)

        return ThermalFrame(raw_temps, colorized, min_val, max_val, min_loc, max_loc)

    def read_visible(self):
        """Read one frame and return just the visible/pseudo-color top half."""
        if self._cap is None:
            return None
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return None
        return frame[:self._sensor_height, :, :]

    def _extract_temperatures(self, thdata):
        """Extract temperature array from raw thermal data half.

        The raw 16-bit value is packed across channels. Common packings:
        - YUYV: Y channel carries data, or split across two channels
        - BGR: lo in channel 0 or 1, hi in channel 1 or 2

        We try the standard InfiRay packing (green=lo, red=hi in BGR),
        then validate the temperature range is sane.
        """
        # Primary: green channel = lo byte, red channel = hi byte (BGR order)
        lo = thdata[:, :, 1].astype(np.float64)  # green
        hi = thdata[:, :, 2].astype(np.float64)  # red
        raw16 = hi * 256.0 + lo
        temps = raw16 / 64.0 - 273.15

        # Sanity check: if temps are wildly out of range, try alternate packing
        median_temp = np.median(temps)
        if median_temp < -100 or median_temp > 1000:
            # Try: blue=lo, green=hi
            lo = thdata[:, :, 0].astype(np.float64)
            hi = thdata[:, :, 1].astype(np.float64)
            raw16 = hi * 256.0 + lo
            temps = raw16 / 64.0 - 273.15

            median_temp = np.median(temps)
            if median_temp < -100 or median_temp > 1000:
                # Last resort: red=lo, green=hi
                lo = thdata[:, :, 2].astype(np.float64)
                hi = thdata[:, :, 1].astype(np.float64)
                raw16 = hi * 256.0 + lo
                temps = raw16 / 64.0 - 273.15

        return temps

    def close(self):
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


# ---------------------------------------------------------------------------
# Waveshare / Meridian MI48 backend
# ---------------------------------------------------------------------------

class WaveshareCamera:
    """Waveshare thermal camera via senxor/MI48 library.

    These cameras use a proprietary serial-over-USB protocol. The senxor
    library handles communication and returns temperature arrays directly.

    Typical sensors: 80x62 or 160x120 pixels.
    """

    def __init__(self, colormap=DEFAULT_COLORMAP, fps=15):
        if not HAS_SENXOR:
            raise RuntimeError(
                "senxor library is required for Waveshare cameras. "
                "Install with: pip install senxor"
            )
        self.colormap = colormap
        self.fps = fps
        self._mi48 = None
        self._with_header = True

    def open(self):
        """Connect to the MI48 sensor."""
        connection = connect_senxor()
        self._mi48 = MI48(connection)
        self._mi48.set_fps(self.fps)
        self._mi48.set_filter_1(6)   # rolling average for noise reduction
        self._mi48.start(stream=True, with_header=self._with_header)
        print(f"Waveshare MI48: connected, streaming at {self.fps} fps")

    def read(self):
        """Read one frame, return ThermalFrame or None."""
        if self._mi48 is None:
            return None

        data, header = self._mi48.read()
        if data is None:
            return None

        # data is a 1D array of temperatures; reshape based on sensor size
        # MI48 sensors are typically 80x62 or 160x120
        total = data.size
        if total == 80 * 62:
            temps = data.reshape((62, 80))
        elif total == 160 * 120:
            temps = data.reshape((120, 160))
        else:
            # Try to figure it out — assume wider than tall
            # Common aspect ratios: 4:3
            w = int(np.sqrt(total * 4 / 3))
            h = total // w
            if w * h != total:
                w = int(np.sqrt(total))
                h = total // w
            temps = data.reshape((h, w))

        temps = temps.astype(np.float64)
        min_val, max_val, min_loc, max_loc = _find_extremes(temps)
        colorized = _normalize_and_colorize(temps, self.colormap)

        return ThermalFrame(temps, colorized, min_val, max_val, min_loc, max_loc)

    def close(self):
        if self._mi48 is not None:
            self._mi48.stop()
            self._mi48 = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


# ---------------------------------------------------------------------------
# Overlay helpers
# ---------------------------------------------------------------------------

def draw_temp_overlay(image, frame, scale=1.0):
    """Draw min/max temperature markers and text on an image.

    Args:
        image: BGR image to draw on (will be modified in place).
        frame: ThermalFrame with temp data.
        scale: scale factor if image has been resized from raw resolution.
    """
    def _scaled(pt):
        return (int(pt[0] * scale), int(pt[1] * scale))

    h, w = image.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = max(0.35, min(w, h) / 600)
    thickness = max(1, int(font_scale * 2))

    # Max temp marker (red)
    mx = _scaled(frame.max_loc)
    cv2.drawMarker(image, mx, (0, 0, 255), cv2.MARKER_CROSS, 12, thickness)
    cv2.putText(image, f"{frame.max_temp:.1f}C", (mx[0] + 8, mx[1] - 4),
                font, font_scale, (0, 0, 255), thickness)

    # Min temp marker (blue)
    mn = _scaled(frame.min_loc)
    cv2.drawMarker(image, mn, (255, 100, 0), cv2.MARKER_CROSS, 12, thickness)
    cv2.putText(image, f"{frame.min_temp:.1f}C", (mn[0] + 8, mn[1] - 4),
                font, font_scale, (255, 100, 0), thickness)

    # Center crosshair with temperature
    cy, cx = frame.height // 2, frame.width // 2
    center_temp = frame.raw_temps[cy, cx]
    cp = _scaled((cx, cy))
    cv2.drawMarker(image, cp, (255, 255, 255), cv2.MARKER_CROSSHAIR, 10, 1)
    cv2.putText(image, f"{center_temp:.1f}C", (cp[0] + 8, cp[1] - 4),
                font, font_scale, (255, 255, 255), thickness)

    # HUD bar at bottom
    bar_text = f"Min:{frame.min_temp:.1f}C  Max:{frame.max_temp:.1f}C  Center:{center_temp:.1f}C"
    cv2.rectangle(image, (0, h - 24), (w, h), (0, 0, 0), -1)
    cv2.putText(image, bar_text, (4, h - 6), font, font_scale * 0.8,
                (200, 200, 200), max(1, thickness - 1))

    return image


# ---------------------------------------------------------------------------
# Auto-detect helper
# ---------------------------------------------------------------------------

def detect_thermal_cameras():
    """Try to detect connected thermal cameras.

    Returns list of dicts with keys: type ('infiray' or 'waveshare'),
    device_index (for infiray), name.
    """
    found = []

    # Check for Waveshare/senxor
    if HAS_SENXOR:
        try:
            conn = connect_senxor()
            if conn:
                found.append({
                    "type": "waveshare",
                    "device_index": None,
                    "name": "Waveshare MI48 (senxor)",
                })
        except Exception:
            pass

    # Probe UVC devices for InfiRay-style doubled frames
    if HAS_CV2:
        for idx in range(10):
            cap = cv2.VideoCapture(idx)
            if not cap.isOpened():
                cap.release()
                continue
            ret, frame = cap.read()
            cap.release()
            if not ret or frame is None:
                continue

            h, w = frame.shape[:2]
            # InfiRay signature: height is exactly 2x a known sensor height,
            # and width matches known sensor widths
            known_sensors = {(256, 384), (240, 480), (160, 240), (128, 256)}
            if (w, h) in known_sensors:
                found.append({
                    "type": "infiray",
                    "device_index": idx,
                    "name": f"InfiRay-style UVC [{idx}] ({w}x{h // 2} sensor)",
                })

    return found
