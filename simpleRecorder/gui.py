#!/usr/bin/env python3
"""Tkinter GUI for simpleRecorder.

Provides dropdown selectors for camera, audio, resolution, frame rate,
codec, container, and quality. Record/Preview/Thermal/MultiCam buttons.
Audio level meter, pre-roll buffer, timecode overlay, thermal range lock.
"""

import multiprocessing
import os
import sys
import time

try:
    import tkinter as tk
    from tkinter import filedialog, ttk
except (ImportError, ModuleNotFoundError):
    print("Error: tkinter is not available in this Python installation.")
    print("  On macOS with Homebrew: brew install python-tk@3.14")
    print("  Or use the CLI instead: python cli.py --help")
    sys.exit(1)

from devices import list_avfoundation_devices, probe_camera_formats, get_unique_resolutions, get_fps_for_resolution
from recorder import RecordingSession, CODEC_MAP


def _run_preview_process(cam, w, h, fps, codec, container, crf, output_dir,
                         base_name, preroll, audio_device, overlay):
    """Run preview in a separate process so OpenCV gets its own main thread."""
    from preview import PreviewWindow
    pw = PreviewWindow(
        mode="regular", device_index=cam, width=w, height=h, fps=fps,
        codec=codec, container=container, crf=crf,
        output_dir=output_dir, base_name=base_name,
        pre_roll_seconds=preroll, audio_device=audio_device, overlay=overlay,
    )
    pw.run()


def _run_thermal_process(mode, device_index, colormap, output_dir, base_name,
                         range_lock, audio_device):
    """Run thermal preview in a separate process."""
    from preview import PreviewWindow
    pw = PreviewWindow(
        mode=mode, device_index=device_index, colormap=colormap,
        output_dir=output_dir, base_name=base_name,
        range_lock=range_lock, audio_device=audio_device,
    )
    pw.run()


def _run_multicam_process(output_dir, base_name, video_devices):
    """Run multicam preview in a separate process."""
    from multicam import MultiCamSession, CameraConfig
    session = MultiCamSession(output_dir=output_dir, base_name=base_name)
    for dev_idx, name in video_devices:
        session.add_camera(CameraConfig(
            device_index=dev_idx, name=name.replace(" ", "_")[:20],
        ))
    session.open_all()
    try:
        session.show_preview()
    finally:
        if session.is_recording:
            session.stop_all()
        session.close_all()


class RecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("simpleRecorder")
        self.root.resizable(False, False)

        self.session = None
        self.recording = False
        self.clip_count = 0
        self.timer_running = False

        self._devices = {"video": [], "audio": []}
        self._formats = []

        self._build_ui()
        self._refresh_devices()

    def _build_ui(self):
        pad = {"padx": 6, "pady": 3}

        # --- Device Selection ---
        dev_frame = ttk.LabelFrame(self.root, text="Devices", padding=8)
        dev_frame.grid(row=0, column=0, sticky="ew", **pad)

        ttk.Label(dev_frame, text="Camera:").grid(row=0, column=0, sticky="w")
        self.camera_var = tk.StringVar()
        self.camera_combo = ttk.Combobox(dev_frame, textvariable=self.camera_var, state="readonly", width=35)
        self.camera_combo.grid(row=0, column=1, sticky="ew", padx=4)
        self.camera_combo.bind("<<ComboboxSelected>>", self._on_camera_changed)

        ttk.Label(dev_frame, text="Audio:").grid(row=1, column=0, sticky="w")
        self.audio_var = tk.StringVar()
        self.audio_combo = ttk.Combobox(dev_frame, textvariable=self.audio_var, state="readonly", width=35)
        self.audio_combo.grid(row=1, column=1, sticky="ew", padx=4)

        ttk.Button(dev_frame, text="Refresh", command=self._refresh_devices).grid(row=0, column=2, padx=4)

        # --- Format Selection ---
        fmt_frame = ttk.LabelFrame(self.root, text="Format", padding=8)
        fmt_frame.grid(row=1, column=0, sticky="ew", **pad)

        ttk.Label(fmt_frame, text="Resolution:").grid(row=0, column=0, sticky="w")
        self.res_var = tk.StringVar()
        self.res_combo = ttk.Combobox(fmt_frame, textvariable=self.res_var, state="readonly", width=15)
        self.res_combo.grid(row=0, column=1, sticky="w", padx=4)
        self.res_combo.bind("<<ComboboxSelected>>", self._on_resolution_changed)

        ttk.Label(fmt_frame, text="FPS:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.fps_var = tk.StringVar()
        self.fps_combo = ttk.Combobox(fmt_frame, textvariable=self.fps_var, state="readonly", width=6)
        self.fps_combo.grid(row=0, column=3, sticky="w", padx=4)

        ttk.Label(fmt_frame, text="Codec:").grid(row=1, column=0, sticky="w")
        self.codec_var = tk.StringVar(value="h264")
        ttk.Combobox(fmt_frame, textvariable=self.codec_var,
                     values=["h264", "hevc"], state="readonly", width=8).grid(row=1, column=1, sticky="w", padx=4)

        ttk.Label(fmt_frame, text="Container:").grid(row=1, column=2, sticky="w", padx=(12, 0))
        self.container_var = tk.StringVar(value="mov")
        ttk.Combobox(fmt_frame, textvariable=self.container_var,
                     values=["mov", "mp4"], state="readonly", width=6).grid(row=1, column=3, sticky="w", padx=4)

        ttk.Label(fmt_frame, text="CRF:").grid(row=2, column=0, sticky="w")
        self.crf_var = tk.StringVar(value="20")
        ttk.Spinbox(fmt_frame, textvariable=self.crf_var, from_=0, to=51, width=5).grid(row=2, column=1, sticky="w", padx=4)

        ttk.Label(fmt_frame, text="Pre-roll (s):").grid(row=2, column=2, sticky="w", padx=(12, 0))
        self.preroll_var = tk.StringVar(value="5")
        ttk.Spinbox(fmt_frame, textvariable=self.preroll_var, from_=0, to=30, width=5).grid(row=2, column=3, sticky="w", padx=4)

        # --- Output ---
        out_frame = ttk.LabelFrame(self.root, text="Output", padding=8)
        out_frame.grid(row=2, column=0, sticky="ew", **pad)

        ttk.Label(out_frame, text="Folder:").grid(row=0, column=0, sticky="w")
        self.dir_var = tk.StringVar(value=os.path.expanduser("~/Movies"))
        ttk.Entry(out_frame, textvariable=self.dir_var, width=30).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(out_frame, text="Browse...", command=self._browse_dir).grid(row=0, column=2, padx=4)

        ttk.Label(out_frame, text="Base name:").grid(row=1, column=0, sticky="w")
        self.name_var = tk.StringVar(value="recording")
        ttk.Entry(out_frame, textvariable=self.name_var, width=30).grid(row=1, column=1, sticky="ew", padx=4)

        # Overlay checkbox
        self.overlay_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(out_frame, text="Burn timecode", variable=self.overlay_var).grid(row=1, column=2, padx=4)

        # --- Main Buttons ---
        btn_frame = ttk.Frame(self.root, padding=8)
        btn_frame.grid(row=3, column=0, sticky="ew", **pad)

        self.record_btn = tk.Button(
            btn_frame, text="● RECORD", font=("Helvetica", 14, "bold"),
            fg="white", bg="#cc0000", activebackground="#ff3333",
            width=10, height=2, command=self._toggle_record,
        )
        self.record_btn.pack(side="left", padx=4)

        tk.Button(
            btn_frame, text="PREVIEW", font=("Helvetica", 12, "bold"),
            fg="white", bg="#336699", activebackground="#4488bb",
            width=10, height=2, command=self._open_preview,
        ).pack(side="left", padx=4)

        tk.Button(
            btn_frame, text="THERMAL", font=("Helvetica", 12, "bold"),
            fg="white", bg="#cc6600", activebackground="#ee8833",
            width=10, height=2, command=self._open_thermal,
        ).pack(side="left", padx=4)

        tk.Button(
            btn_frame, text="MULTI", font=("Helvetica", 12, "bold"),
            fg="white", bg="#669933", activebackground="#88bb55",
            width=8, height=2, command=self._open_multicam,
        ).pack(side="left", padx=4)

        # --- Thermal Options ---
        therm_frame = ttk.LabelFrame(self.root, text="Thermal Options", padding=8)
        therm_frame.grid(row=4, column=0, sticky="ew", **pad)

        ttk.Label(therm_frame, text="Backend:").grid(row=0, column=0, sticky="w")
        self.thermal_mode_var = tk.StringVar(value="infiray")
        ttk.Combobox(therm_frame, textvariable=self.thermal_mode_var,
                     values=["infiray", "waveshare"], state="readonly", width=10).grid(row=0, column=1, sticky="w", padx=4)

        ttk.Label(therm_frame, text="Colormap:").grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.colormap_var = tk.StringVar(value="inferno")
        ttk.Combobox(therm_frame, textvariable=self.colormap_var,
                     values=["inferno", "jet", "hot", "turbo", "magma", "rainbow",
                             "bone", "white_hot", "black_hot"],
                     state="readonly", width=10).grid(row=0, column=3, sticky="w", padx=4)

        ttk.Label(therm_frame, text="Range lock:").grid(row=1, column=0, sticky="w")
        range_f = ttk.Frame(therm_frame)
        range_f.grid(row=1, column=1, columnspan=3, sticky="w", padx=4)
        self.range_min_var = tk.StringVar(value="")
        self.range_max_var = tk.StringVar(value="")
        ttk.Entry(range_f, textvariable=self.range_min_var, width=6).pack(side="left")
        ttk.Label(range_f, text=" - ").pack(side="left")
        ttk.Entry(range_f, textvariable=self.range_max_var, width=6).pack(side="left")
        ttk.Label(range_f, text=" C (blank=auto)").pack(side="left")

        # --- Status ---
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w").grid(
            row=5, column=0, sticky="ew", padx=8, pady=4,
        )

    # --- Device handling ---

    def _refresh_devices(self):
        self.status_var.set("Scanning devices...")
        self.root.update_idletasks()

        self._devices = list_avfoundation_devices()

        cam_items = [f"[{i}] {n}" for i, n in self._devices["video"]]
        self.camera_combo["values"] = cam_items if cam_items else ["(no cameras found)"]
        if cam_items:
            self.camera_combo.current(0)
            self._on_camera_changed()

        audio_items = ["(none)"] + [f"[{i}] {n}" for i, n in self._devices["audio"]]
        self.audio_combo["values"] = audio_items
        self.audio_combo.current(0)

        self.status_var.set("Ready")

    def _on_camera_changed(self, event=None):
        idx = self._get_camera_index()
        if idx is None:
            return

        self.status_var.set(f"Probing camera {idx}...")
        self.root.update_idletasks()

        self._formats = probe_camera_formats(idx)
        resolutions = get_unique_resolutions(self._formats)

        if resolutions:
            self.res_combo["values"] = [f"{w}x{h}" for w, h in resolutions]
            self.res_combo.current(0)
            self._on_resolution_changed()
        else:
            self.res_combo["values"] = ["3840x2160", "1920x1080", "1280x720", "640x480"]
            self.res_combo.current(0)
            self.fps_combo["values"] = ["24", "30", "60"]
            self.fps_combo.current(len(self.fps_combo["values"]) - 1)

        self.status_var.set("Ready")

    def _on_resolution_changed(self, event=None):
        res = self.res_var.get()
        if "x" not in res:
            return
        w, h = (int(x) for x in res.split("x"))
        fps_list = get_fps_for_resolution(self._formats, w, h)
        if fps_list:
            fps_strs = [str(int(f)) if f == int(f) else str(f) for f in fps_list]
            self.fps_combo["values"] = fps_strs
            self.fps_combo.current(len(fps_strs) - 1)
        else:
            self.fps_combo["values"] = ["24", "30", "60"]
            self.fps_combo.current(len(self.fps_combo["values"]) - 1)

    def _get_camera_index(self):
        val = self.camera_var.get()
        if val.startswith("["):
            try:
                return int(val.split("]")[0][1:])
            except ValueError:
                pass
        return None

    def _get_audio_index(self):
        val = self.audio_var.get()
        if val.startswith("["):
            try:
                return int(val.split("]")[0][1:])
            except ValueError:
                pass
        return None

    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get())
        if d:
            self.dir_var.set(d)

    def _get_range_lock(self):
        try:
            lo = float(self.range_min_var.get())
            hi = float(self.range_max_var.get())
            return (lo, hi)
        except (ValueError, TypeError):
            return None

    # --- Recording (ffmpeg direct, no preview) ---

    def _toggle_record(self):
        if not self.recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        cam = self._get_camera_index()
        if cam is None:
            self.status_var.set("No camera selected!")
            return

        res = self.res_var.get()
        if "x" not in res:
            self.status_var.set("No resolution selected!")
            return

        w, h = (int(x) for x in res.split("x"))
        fps = int(float(self.fps_var.get() or "30"))
        try:
            crf = int(self.crf_var.get())
        except ValueError:
            crf = None

        self.session = RecordingSession(
            video_device=cam,
            audio_device=self._get_audio_index(),
            width=w, height=h, fps=fps,
            codec=self.codec_var.get(),
            container=self.container_var.get(),
            crf=crf,
            output_dir=self.dir_var.get(),
            base_name=self.name_var.get(),
        )

        path = self.session.start()
        self.recording = True
        self.clip_count += 1
        self.record_btn.config(text="■ STOP", bg="#333333", activebackground="#555555")
        self.status_var.set(f"Recording #{self.clip_count}: {os.path.basename(path)}")
        self.timer_running = True
        self._update_timer()

    def _stop_recording(self):
        self.timer_running = False
        if self.session:
            path, dur = self.session.stop()
            self.status_var.set(f"Saved: {os.path.basename(path)} ({dur:.1f}s)")
        self.recording = False
        self.session = None
        self.record_btn.config(text="● RECORD", bg="#cc0000", activebackground="#ff3333")

    def _update_timer(self):
        if not self.timer_running or not self.session:
            return
        elapsed = self.session.elapsed
        mins, secs = divmod(int(elapsed), 60)
        hrs, mins = divmod(mins, 60)
        self.status_var.set(f"● REC #{self.clip_count}  {hrs:02d}:{mins:02d}:{secs:02d}")
        self.root.after(500, self._update_timer)

    # --- Preview (unified pipeline) ---

    def _open_preview(self):
        cam = self._get_camera_index()
        if cam is None:
            self.status_var.set("No camera selected!")
            return

        res = self.res_var.get()
        w, h = 1920, 1080
        if "x" in res:
            w, h = (int(x) for x in res.split("x"))
        fps = int(float(self.fps_var.get() or "30"))

        try:
            preroll = float(self.preroll_var.get())
        except ValueError:
            preroll = 5.0

        try:
            crf = int(self.crf_var.get())
        except ValueError:
            crf = 20

        self.status_var.set("Opening preview...")
        self.root.update_idletasks()

        # Use multiprocessing so OpenCV gets its own main thread (required on macOS)
        p = multiprocessing.Process(
            target=_run_preview_process,
            args=(cam, w, h, fps, self.codec_var.get(),
                  self.container_var.get(), crf, self.dir_var.get(),
                  self.name_var.get(), preroll,
                  self._get_audio_index(), self.overlay_var.get()),
            daemon=True,
        )
        p.start()

    # --- Thermal preview ---

    def _open_thermal(self):
        cam = self._get_camera_index()
        mode = self.thermal_mode_var.get()
        colormap = self.colormap_var.get()
        range_lock = self._get_range_lock()

        self.status_var.set(f"Opening thermal preview ({mode})...")
        self.root.update_idletasks()

        p = multiprocessing.Process(
            target=_run_thermal_process,
            args=(mode, cam if cam is not None else 0, colormap,
                  self.dir_var.get(), self.name_var.get(),
                  range_lock, self._get_audio_index()),
            daemon=True,
        )
        p.start()

    # --- Multi-camera ---

    def _open_multicam(self):
        if len(self._devices["video"]) < 2:
            self.status_var.set("Need 2+ cameras for multi-cam mode")
            return

        self.status_var.set("Opening multi-cam preview...")
        self.root.update_idletasks()

        video_devices = list(self._devices["video"])
        p = multiprocessing.Process(
            target=_run_multicam_process,
            args=(self.dir_var.get(), self.name_var.get(), video_devices),
            daemon=True,
        )
        p.start()


def main():
    root = tk.Tk()
    app = RecorderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
