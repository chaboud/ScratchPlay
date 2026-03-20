#!/usr/bin/env python3
"""Command-line interface for simpleRecorder.

Usage modes:
  1. One-shot:    python cli.py record --duration 30
  2. Interactive: python cli.py interactive  (spacebar to start/stop clips)
  3. List:        python cli.py devices
  4. Formats:     python cli.py formats --camera 0
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
    fmt_p.add_argument("--camera", "-c", type=int, default=0, help="Video device index")

    # --- record (one-shot) ---
    rec_p = sub.add_parser("record", help="Record a single clip")
    _add_recording_args(rec_p)
    rec_p.add_argument("--duration", "-t", type=float, default=10, help="Duration in seconds")

    # --- interactive (spacebar toggle) ---
    int_p = sub.add_parser("interactive", help="Spacebar to start/stop recording clips")
    _add_recording_args(int_p)
    int_p.add_argument(
        "--max-length", type=float, default=None,
        help="Max clip length in seconds (auto-stop and start new file)",
    )

    return p.parse_args()


def _add_recording_args(parser):
    """Add common recording arguments to a subparser."""
    parser.add_argument("--camera", "-c", type=int, default=0, help="Video device index")
    parser.add_argument("--audio", "-a", type=int, default=None, help="Audio device index (omit for no audio)")
    parser.add_argument("--width", "-W", type=int, default=None, help="Video width (default: max for camera)")
    parser.add_argument("--height", "-H", type=int, default=None, help="Video height (default: max for camera)")
    parser.add_argument("--fps", "-f", type=int, default=None, help="Frame rate (default: max for resolution)")
    parser.add_argument("--codec", choices=["h264", "avc", "h265", "hevc"], default="h264")
    parser.add_argument("--container", choices=["mov", "mp4"], default="mov")
    parser.add_argument("--crf", type=int, default=None, help="Quality (lower=better, default 20 for h264)")
    parser.add_argument("--output-dir", "-o", default=".", help="Output directory")
    parser.add_argument("--base-name", "-n", default="recording", help="Base filename")


def _resolve_defaults(args):
    """Fill in width/height/fps from camera probe if not explicitly set."""
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
    """One-shot recording."""
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
    """Read a single keypress without blocking, with timeout. Returns None if no key."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            ch = sys.stdin.read(1)
            return ch
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def cmd_interactive(args):
    """Interactive mode: spacebar toggles recording on/off."""
    _resolve_defaults(args)
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
                    # Start new clip
                    path = session.start()
                    recording = True
                    clip_count += 1
                    print(f"\n● REC #{clip_count}: {os.path.basename(path)}")
                else:
                    # Stop current clip
                    path, dur = session.stop()
                    recording = False
                    print(f"\n■ STOP: {os.path.basename(path)} ({dur:.1f}s)")
            elif ch in ("q", "Q", "\x03"):  # q or Ctrl-C
                if recording:
                    path, dur = session.stop()
                    print(f"\n■ STOP: {os.path.basename(path)} ({dur:.1f}s)")
                print(f"\nDone. {clip_count} clip(s) recorded.")
                break

            # Auto-stop if max duration reached
            if recording and args.max_length and session.elapsed >= args.max_length:
                path, dur = session.stop()
                print(f"\n■ AUTO-STOP (max length): {os.path.basename(path)} ({dur:.1f}s)")
                recording = False

            # Status line
            if recording:
                elapsed = session.elapsed
                sys.stdout.write(f"\r  ● {elapsed:.0f}s elapsed...  ")
                sys.stdout.flush()

    except KeyboardInterrupt:
        if recording:
            path, dur = session.stop()
            print(f"\n■ STOP: {os.path.basename(path)} ({dur:.1f}s)")
        print(f"\nInterrupted. {clip_count} clip(s) recorded.")


def main():
    args = parse_args()

    if args.command is None:
        print("Usage: python cli.py {devices|formats|record|interactive}")
        print("Run with -h for help.")
        sys.exit(1)
    elif args.command == "devices":
        print_devices()
    elif args.command == "formats":
        print_camera_formats(args.camera)
    elif args.command == "record":
        cmd_record(args)
    elif args.command == "interactive":
        cmd_interactive(args)


if __name__ == "__main__":
    main()
