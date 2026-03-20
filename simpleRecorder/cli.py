#!/usr/bin/env python3
"""Command-line interface for simpleRecorder.

Usage modes:
  python cli.py devices               List cameras and audio devices
  python cli.py formats -c 0          Show camera formats
  python cli.py record -t 30          One-shot recording
  python cli.py interactive           Spacebar start/stop clips
  python cli.py preview -c 0          Live preview with record (R key)
  python cli.py thermal -m infiray    Thermal camera preview
  python cli.py thermal-scan          Detect thermal cameras
  python cli.py multicam              Multi-camera simultaneous recording
"""

import argparse
import sys
import os
import select
import termios
import tty
import time

from devices import (
    list_avfoundation_devices, print_devices, print_camera_formats,
    get_best_defaults,
)
from recorder import RecordingSession, one_shot_record


def parse_args():
    p = argparse.ArgumentParser(
        prog="simpleRecorder",
        description="Record video from macOS cameras via ffmpeg/AVFoundation.",
    )
    sub = p.add_subparsers(dest="command", help="Command to run")

    # --- devices ---
    sub.add_parser("devices", help="List available video and audio devices")

    # --- formats ---
    fmt_p = sub.add_parser("formats", help="Show supported formats for a camera")
    fmt_p.add_argument("--camera", "-c", type=int, default=0)

    # --- record (one-shot via ffmpeg) ---
    rec_p = sub.add_parser("record", help="Record a single clip (ffmpeg direct)")
    _add_recording_args(rec_p)
    rec_p.add_argument("--duration", "-t", type=float, default=10)

    # --- interactive (spacebar toggle, ffmpeg direct) ---
    int_p = sub.add_parser("interactive", help="Spacebar to start/stop recording clips")
    _add_recording_args(int_p)
    int_p.add_argument("--max-length", type=float, default=None,
                       help="Max clip length in seconds")
    int_p.add_argument("--preview", action="store_true",
                       help="Open live preview window (use R key to record)")
    int_p.add_argument("--pre-roll", type=float, default=5.0,
                       help="Pre-roll buffer seconds (default 5, with --preview)")
    _add_overlay_args(int_p)

    # --- preview (unified pipeline: preview + record) ---
    prev_p = sub.add_parser("preview", help="Live preview window with recording")
    _add_recording_args(prev_p)
    prev_p.add_argument("--pre-roll", type=float, default=5.0,
                        help="Pre-roll buffer seconds (default 5)")
    _add_overlay_args(prev_p)

    # --- thermal ---
    therm_p = sub.add_parser("thermal", help="Thermal camera preview + record")
    therm_p.add_argument("--mode", "-m", choices=["infiray", "waveshare"], default="infiray")
    therm_p.add_argument("--camera", "-c", type=int, default=0)
    therm_p.add_argument("--audio", "-a", type=int, default=None)
    therm_p.add_argument("--colormap", default="inferno",
                         help="inferno, jet, hot, bone, turbo, magma, rainbow, ironbow, white_hot, black_hot")
    therm_p.add_argument("--waveshare-fps", type=int, default=15)
    therm_p.add_argument("--range-min", type=float, default=None,
                         help="Lock colormap min temperature (C)")
    therm_p.add_argument("--range-max", type=float, default=None,
                         help="Lock colormap max temperature (C)")
    therm_p.add_argument("--output-dir", "-o", default=".")
    therm_p.add_argument("--base-name", "-n", default="thermal")

    # --- thermal-scan ---
    sub.add_parser("thermal-scan", help="Auto-detect connected thermal cameras")

    # --- multicam ---
    mc_p = sub.add_parser("multicam", help="Multi-camera simultaneous recording")
    mc_p.add_argument("--cameras", type=str, required=True,
                      help="Comma-separated device indices (e.g. 0,1,2)")
    mc_p.add_argument("--names", type=str, default=None,
                      help="Comma-separated names (e.g. front,side,top)")
    mc_p.add_argument("--output-dir", "-o", default=".")
    mc_p.add_argument("--base-name", "-n", default="multicam")

    return p.parse_args()


def _add_recording_args(parser):
    """Add common recording arguments to a subparser."""
    parser.add_argument("--camera", "-c", type=int, default=0)
    parser.add_argument("--audio", "-a", type=int, default=None,
                        help="Audio device index (omit for no audio)")
    parser.add_argument("--width", "-W", type=int, default=None,
                        help="Video width (default: max)")
    parser.add_argument("--height", "-H", type=int, default=None,
                        help="Video height (default: max)")
    parser.add_argument("--fps", "-f", type=int, default=None,
                        help="Frame rate (default: max)")
    parser.add_argument("--codec", choices=["h264", "avc", "h265", "hevc"], default="h264")
    parser.add_argument("--container", choices=["mov", "mp4"], default="mov")
    parser.add_argument("--crf", type=int, default=None,
                        help="Quality (lower=better, default 20)")
    parser.add_argument("--output-dir", "-o", default=".")
    parser.add_argument("--base-name", "-n", default="recording")
    parser.add_argument("--target", "-T", default=None,
                        help="Target output path (e.g. /tmp/myvideo.mov). "
                             "Overrides --output-dir, --base-name, --container")


def _add_overlay_args(parser):
    """Add overlay/burn arguments to preview-capable subparsers."""
    parser.add_argument("--no-overlay", action="store_true",
                        help="Disable timecode overlay on preview (legacy shorthand)")
    parser.add_argument("--preview-timecode", action="store_true", default=True,
                        help="Show timecode on preview (default: on)")
    parser.add_argument("--no-preview-timecode", dest="preview_timecode", action="store_false",
                        help="Hide timecode on preview")
    parser.add_argument("--preview-meters", action="store_true", default=True,
                        help="Show audio meters on preview (default: on)")
    parser.add_argument("--no-preview-meters", dest="preview_meters", action="store_false",
                        help="Hide audio meters on preview")
    parser.add_argument("--record-timecode", action="store_true", default=False,
                        help="Burn timecode into recorded video")
    parser.add_argument("--record-meters", action="store_true", default=False,
                        help="Burn audio meters into recorded video")


def _resolve_target(args):
    """If --target is set, derive output-dir, base-name, and container from it."""
    if not hasattr(args, 'target') or args.target is None:
        return
    target = args.target
    args.output_dir = os.path.dirname(target) or "."
    base = os.path.basename(target)
    stem, ext = os.path.splitext(base)
    args.base_name = stem
    if ext.lower() in (".mp4",):
        args.container = "mp4"
    elif ext.lower() in (".mov",):
        args.container = "mov"


def _resolve_defaults(args):
    """Fill in width/height/fps from camera probe if not explicitly set."""
    _resolve_target(args)
    if args.width is None or args.height is None or args.fps is None:
        print(f"Probing camera {args.camera} for best defaults...")
        w, h, fps = get_best_defaults(args.camera)
        if args.width is None:
            args.width = w
        if args.height is None:
            args.height = h
        if args.fps is None:
            args.fps = fps
        print(f"  Using {args.width}x{args.height}@{args.fps}")


def cmd_record(args):
    _resolve_defaults(args)
    one_shot_record(
        video_device=args.camera,
        audio_device=args.audio,
        width=args.width,
        height=args.height,
        fps=args.fps,
        codec=args.codec,
        container=args.container,
        crf=args.crf,
        output_dir=args.output_dir,
        base_name=args.base_name,
        duration=args.duration,
    )


def _getch_nonblocking(timeout=0.1):
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            return sys.stdin.read(1)
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def cmd_interactive(args):
    _resolve_defaults(args)

    if args.preview:
        _interactive_preview(args)
        return

    session = RecordingSession(
        video_device=args.camera,
        audio_device=args.audio,
        width=args.width,
        height=args.height,
        fps=args.fps,
        codec=args.codec,
        container=args.container,
        crf=args.crf,
        output_dir=args.output_dir,
        base_name=args.base_name,
        max_duration=args.max_length,
    )

    clip_count = 0
    print("=== simpleRecorder Interactive Mode ===")
    print(f"Camera: {args.camera} | {args.width}x{args.height}@{args.fps}")
    print(f"Codec: {args.codec} | Container: {args.container}")
    print(f"Output: {args.output_dir}/{args.base_name}_*.{args.container}")
    if args.audio is not None:
        print(f"Audio device: {args.audio}")
    print()
    print("SPACE = start/stop recording | Q = quit")
    print("-" * 40)

    recording = False
    try:
        while True:
            ch = _getch_nonblocking(0.2)

            if ch == " ":
                if not recording:
                    path = session.start()
                    recording = True
                    clip_count += 1
                    print(f"\n● REC #{clip_count}: {os.path.basename(path)}")
                else:
                    path, dur = session.stop()
                    recording = False
                    print(f"\n■ STOP: {os.path.basename(path)} ({dur:.1f}s)")
            elif ch in ("q", "Q", "\x03"):
                if recording:
                    path, dur = session.stop()
                    print(f"\n■ STOP: {os.path.basename(path)} ({dur:.1f}s)")
                print(f"\nDone. {clip_count} clip(s) recorded.")
                break

            if recording and args.max_length and session.elapsed >= args.max_length:
                path, dur = session.stop()
                print(f"\n■ AUTO-STOP (max length): {os.path.basename(path)} ({dur:.1f}s)")
                recording = False

            if recording:
                elapsed = session.elapsed
                sys.stdout.write(f"\r  ● {elapsed:.0f}s elapsed...  ")
                sys.stdout.flush()

    except KeyboardInterrupt:
        if recording:
            path, dur = session.stop()
            print(f"\n■ STOP: {os.path.basename(path)} ({dur:.1f}s)")
        print(f"\nInterrupted. {clip_count} clip(s) recorded.")


def _resolve_overlay_flags(args):
    """Resolve overlay flags, respecting legacy --no-overlay."""
    preview_tc = args.preview_timecode and not args.no_overlay
    preview_m = args.preview_meters
    record_tc = args.record_timecode
    record_m = args.record_meters
    return preview_tc, preview_m, record_tc, record_m


def _interactive_preview(args):
    """Interactive mode with live preview window via RecordingPipeline."""
    from pipeline import RecordingPipeline

    p_tc, p_m, r_tc, r_m = _resolve_overlay_flags(args)
    pipe = RecordingPipeline(
        device_index=args.camera,
        width=args.width,
        height=args.height,
        fps=args.fps,
        codec=args.codec,
        container=args.container,
        crf=args.crf if args.crf else 20,
        output_dir=args.output_dir,
        base_name=args.base_name,
        pre_roll_seconds=args.pre_roll,
        audio_device=args.audio,
        preview_timecode=p_tc,
        preview_meters=p_m,
        record_timecode=r_tc,
        record_meters=r_m,
    )
    pipe.open()
    try:
        pipe.show_preview()
    finally:
        pipe.close()


def cmd_preview(args):
    """Live preview with unified pipeline (preview-while-record + pre-roll)."""
    from preview import PreviewWindow

    _resolve_defaults(args)
    from pipeline import RecordingPipeline

    p_tc, p_m, r_tc, r_m = _resolve_overlay_flags(args)
    pipe = RecordingPipeline(
        device_index=args.camera,
        width=args.width,
        height=args.height,
        fps=args.fps,
        codec=args.codec,
        container=args.container,
        crf=args.crf if args.crf else 20,
        output_dir=args.output_dir,
        base_name=args.base_name,
        pre_roll_seconds=args.pre_roll,
        audio_device=args.audio,
        preview_timecode=p_tc,
        preview_meters=p_m,
        record_timecode=r_tc,
        record_meters=r_m,
    )
    pipe.open()
    try:
        pipe.show_preview()
    finally:
        pipe.close()


def cmd_thermal(args):
    """Thermal camera preview with radiometric recording."""
    from preview import PreviewWindow

    range_lock = None
    if args.range_min is not None and args.range_max is not None:
        range_lock = (args.range_min, args.range_max)

    pw = PreviewWindow(
        mode=args.mode,
        device_index=args.camera,
        colormap=args.colormap,
        waveshare_fps=args.waveshare_fps,
        output_dir=args.output_dir,
        base_name=args.base_name,
        range_lock=range_lock,
        audio_device=args.audio,
    )
    pw.run()


def cmd_thermal_scan():
    from thermal import detect_thermal_cameras

    print("Scanning for thermal cameras...")
    cameras = detect_thermal_cameras()
    if not cameras:
        print("  No thermal cameras detected.")
        print("  (Make sure camera is plugged in and not claimed by another app)")
    else:
        for cam in cameras:
            idx = cam["device_index"]
            idx_str = f"device {idx}" if idx is not None else "serial"
            print(f"  [{cam['type']}] {cam['name']} ({idx_str})")


def cmd_multicam(args):
    """Multi-camera simultaneous recording with composite preview."""
    from multicam import MultiCamSession, CameraConfig

    indices = [int(x.strip()) for x in args.cameras.split(",")]
    names = None
    if args.names:
        names = [n.strip() for n in args.names.split(",")]

    session = MultiCamSession(output_dir=args.output_dir, base_name=args.base_name)

    for i, idx in enumerate(indices):
        name = names[i] if names and i < len(names) else f"cam{idx}"
        session.add_camera(CameraConfig(device_index=idx, name=name))

    print(f"Opening {len(indices)} camera(s)...")
    session.open_all()

    try:
        session.show_preview()
    finally:
        if session.is_recording:
            session.stop_all()
        session.close_all()


def main():
    args = parse_args()

    commands = {
        "devices": lambda: print_devices(),
        "formats": lambda: print_camera_formats(args.camera),
        "record": lambda: cmd_record(args),
        "interactive": lambda: cmd_interactive(args),
        "preview": lambda: cmd_preview(args),
        "thermal": lambda: cmd_thermal(args),
        "thermal-scan": lambda: cmd_thermal_scan(),
        "multicam": lambda: cmd_multicam(args),
    }

    if args.command is None:
        print("Usage: python cli.py {" + "|".join(commands.keys()) + "}")
        print("Run with -h for help.")
        sys.exit(1)

    commands[args.command]()


if __name__ == "__main__":
    main()
