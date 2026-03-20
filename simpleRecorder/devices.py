"""Device enumeration for cameras and audio sources on macOS."""

import json
import re
import subprocess
import sys


def _run_ffmpeg(args):
    """Run an ffmpeg/ffprobe command and return stderr output."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=10
        )
        # ffmpeg often writes to stderr
        return result.stdout + result.stderr
    except FileNotFoundError:
        print("Error: ffmpeg not found. Install with: brew install ffmpeg")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        return ""


def list_avfoundation_devices():
    """List available AVFoundation video and audio devices on macOS.

    Returns:
        dict with 'video' and 'audio' lists of (index, name) tuples.
    """
    output = _run_ffmpeg([
        "ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""
    ])

    devices = {"video": [], "audio": []}
    section = None

    for line in output.splitlines():
        if "AVFoundation video devices:" in line:
            section = "video"
            continue
        elif "AVFoundation audio devices:" in line:
            section = "audio"
            continue

        if section:
            # Match lines like: [AVFoundation ...] [0] FaceTime HD Camera
            m = re.search(r'\[(\d+)\]\s+(.+)', line)
            if m:
                idx = int(m.group(1))
                name = m.group(2).strip()
                devices[section].append((idx, name))

    return devices


def probe_camera_formats(video_index):
    """Probe a camera for supported pixel formats, resolutions, and frame rates.

    Uses ffmpeg to query AVFoundation device capabilities.

    Returns:
        list of dicts with keys: pixel_format, width, height, fps_options
    """
    output = _run_ffmpeg([
        "ffmpeg", "-f", "avfoundation",
        "-video_device_index", str(video_index),
        "-list_formats", "true", "-i", ""
    ])

    formats = []
    for line in output.splitlines():
        # Parse format lines from AVFoundation
        # Example: [avfoundation] 0: yuyv422 1920x1080@[30.000000 60.000000]
        m = re.search(
            r'(\w+)\s+(\d+)x(\d+)@\[([^\]]+)\]', line
        )
        if m:
            pix_fmt = m.group(1)
            w, h = int(m.group(2)), int(m.group(3))
            fps_list = [float(f) for f in m.group(4).split()]
            formats.append({
                "pixel_format": pix_fmt,
                "width": w,
                "height": h,
                "fps_options": fps_list,
            })
            continue

        # Alternative format: just resolution and fps without pixel format bracket
        m2 = re.search(r'(\d+)x(\d+).*?(\d+\.\d+)\s*fps', line)
        if m2:
            w, h = int(m2.group(1)), int(m2.group(2))
            fps = float(m2.group(3))
            formats.append({
                "pixel_format": "unknown",
                "width": w,
                "height": h,
                "fps_options": [fps],
            })

    return formats


def get_unique_resolutions(formats):
    """Extract unique (width, height) pairs from format list, sorted descending."""
    resolutions = sorted(
        {(f["width"], f["height"]) for f in formats},
        key=lambda r: r[0] * r[1],
        reverse=True,
    )
    return resolutions


def get_fps_for_resolution(formats, width, height):
    """Get all available FPS values for a given resolution, sorted ascending."""
    fps_set = set()
    for f in formats:
        if f["width"] == width and f["height"] == height:
            fps_set.update(f["fps_options"])
    return sorted(fps_set)


def get_max_resolution(formats):
    """Return the highest resolution (width, height) available, or None."""
    resolutions = get_unique_resolutions(formats)
    return resolutions[0] if resolutions else None


def get_max_fps(formats, width, height):
    """Return the highest FPS for a given resolution, or None."""
    fps_list = get_fps_for_resolution(formats, width, height)
    return fps_list[-1] if fps_list else None


def get_best_defaults(video_index):
    """Probe a camera and return (width, height, fps) at max resolution and max rate.

    Returns (3840, 2160, 60) as fallback if probing yields nothing.
    """
    formats = probe_camera_formats(video_index)
    res = get_max_resolution(formats)
    if res is None:
        return 3840, 2160, 60
    w, h = res
    fps = get_max_fps(formats, w, h)
    if fps is None:
        fps = 60
    return w, h, fps


def print_devices():
    """Print all available devices to stdout."""
    devices = list_avfoundation_devices()

    print("=== Video Devices ===")
    if not devices["video"]:
        print("  (none found)")
    for idx, name in devices["video"]:
        print(f"  [{idx}] {name}")

    print("\n=== Audio Devices ===")
    if not devices["audio"]:
        print("  (none found)")
    for idx, name in devices["audio"]:
        print(f"  [{idx}] {name}")


def print_camera_formats(video_index):
    """Print supported formats for a camera."""
    formats = probe_camera_formats(video_index)

    if not formats:
        print(f"  No formats reported for video device {video_index}.")
        print("  The camera may still work — try a common resolution like 1920x1080@30.")
        return

    resolutions = get_unique_resolutions(formats)
    print(f"=== Formats for video device {video_index} ===")
    for w, h in resolutions:
        fps_list = get_fps_for_resolution(formats, w, h)
        fps_str = ", ".join(f"{f:.0f}" for f in fps_list)
        print(f"  {w}x{h} @ {fps_str} fps")


if __name__ == "__main__":
    print_devices()
    devices = list_avfoundation_devices()
    for idx, name in devices.get("video", []):
        print()
        print_camera_formats(idx)
