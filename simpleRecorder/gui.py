#!/usr/bin/env python3
"""Tkinter GUI for simpleRecorder.

Provides dropdown selectors for camera, audio, resolution, frame rate,
codec, container, and quality. Record button toggles recording on/off.
Preview button opens a live view window. Thermal mode support.
"""

import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from devices import list_avfoundation_devices, probe_camera_formats, get_unique_resolutions, get_fps_for_resolution
from recorder import RecordingSession, CODEC_MAP, CONTAINER_EXT, DEFAULT_CRF


class RecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("simpleRecorder")
        self.root.resizable(False, False)

        self.session = None
        self.recording = False
        self.clip_count = 0
        self.timer_running = False

        # Cached device/format data
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

        btn_col = ttk.Frame(dev_frame)
        btn_col.grid(row=0, column=2, rowspan=2, padx=4)
        ttk.Button(btn_col, text="Refresh", command=self._refresh_devices).pack(pady=1)

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
        self.codec_combo = ttk.Combobox(
            fmt_frame, textvariable=self.codec_var,
            values=["h264", "hevc"], state="readonly", width=8,
        )
        self.codec_combo.grid(row=1, column=1, sticky="w", padx=4)

        ttk.Label(fmt_frame, text="Container:").grid(row=1, column=2, sticky="w", padx=(12, 0))
        self.container_var = tk.StringVar(value="mov")
        self.container_combo = ttk.Combobox(
            fmt_frame, textvariable=self.container_var,
            values=["mov", "mp4"], state="readonly", width=6,
        )
        self.container_combo.grid(row=1, column=3, sticky="w", padx=4)

        ttk.Label(fmt_frame, text="CRF (quality):").grid(row=2, column=0, sticky="w")
        self.crf_var = tk.StringVar(value="20")
        self.crf_spin = ttk.Spinbox(fmt_frame, textvariable=self.crf_var, from_=0, to=51, width=5)
        self.crf_spin.grid(row=2, column=1, sticky="w", padx=4)

        # --- Output ---
        out_frame = ttk.LabelFrame(self.root, text="Output", padding=8)
        out_frame.grid(row=2, column=0, sticky="ew", **pad)

        ttk.Label(out_frame, text="Folder:").grid(row=0, column=0, sticky="w")
        self.dir_var = tk.StringVar(value=os.path.expanduser("~/Movies"))
        self.dir_entry = ttk.Entry(out_frame, textvariable=self.dir_var, width=30)
        self.dir_entry.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(out_frame, text="Browse...", command=self._browse_dir).grid(row=0, column=2, padx=4)

        ttk.Label(out_frame, text="Base name:").grid(row=1, column=0, sticky="w")
        self.name_var = tk.StringVar(value="recording")
        ttk.Entry(out_frame, textvariable=self.name_var, width=30).grid(row=1, column=1, sticky="ew", padx=4)

        # --- Buttons ---
        btn_frame = ttk.Frame(self.root, padding=8)
        btn_frame.grid(row=3, column=0, sticky="ew", **pad)

        self.record_btn = tk.Button(
            btn_frame, text="● RECORD", font=("Helvetica", 16, "bold"),
            fg="white", bg="#cc0000", activebackground="#ff3333",
            width=14, height=2, command=self._toggle_record,
        )
        self.record_btn.pack(side="left", padx=(0, 8))

        self.preview_btn = tk.Button(
            btn_frame, text="PREVIEW", font=("Helvetica", 14, "bold"),
            fg="white", bg="#336699", activebackground="#4488bb",
            width=10, height=2, command=self._open_preview,
        )
        self.preview_btn.pack(side="left", padx=(0, 8))

        self.thermal_btn = tk.Button(
            btn_frame, text="THERMAL", font=("Helvetica", 14, "bold"),
            fg="white", bg="#cc6600", activebackground="#ee8833",
            width=10, height=2, command=self._open_thermal,
        )
        self.thermal_btn.pack(side="left")

        # --- Thermal Options (collapsed by default) ---
        therm_frame = ttk.LabelFrame(self.root, text="Thermal Options", padding=8)
        therm_frame.grid(row=4, column=0, sticky="ew", **pad)

        ttk.Label(therm_frame, text="Backend:").grid(row=0, column=0, sticky="w")
        self.thermal_mode_var = tk.StringVar(value="infiray")
        ttk.Combobox(
            therm_frame, textvariable=self.thermal_mode_var,
            values=["infiray", "waveshare"], state="readonly", width=12,
        ).grid(row=0, column=1, sticky="w", padx=4)

        ttk.Label(therm_frame, text="Colormap:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.colormap_var = tk.StringVar(value="inferno")
        ttk.Combobox(
            therm_frame, textvariable=self.colormap_var,
            values=["inferno", "jet", "hot", "turbo", "magma", "rainbow",
                    "bone", "white_hot", "black_hot"],
            state="readonly", width=12,
        ).grid(row=0, column=3, sticky="w", padx=4)

        # --- Status ---
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w").grid(
            row=5, column=0, sticky="ew", padx=8, pady=4,
        )

    def _refresh_devices(self):
        """Re-enumerate cameras and audio devices."""
        self.status_var.set("Scanning devices...")
        self.root.update_idletasks()

        self._devices = list_avfoundation_devices()

        # Populate camera dropdown
        cam_items = [f"[{i}] {n}" for i, n in self._devices["video"]]
        self.camera_combo["values"] = cam_items if cam_items else ["(no cameras found)"]
        if cam_items:
            self.camera_combo.current(0)
            self._on_camera_changed()

        # Populate audio dropdown
        audio_items = ["(none)"] + [f"[{i}] {n}" for i, n in self._devices["audio"]]
        self.audio_combo["values"] = audio_items
        self.audio_combo.current(0)

        self.status_var.set("Ready")

    def _on_camera_changed(self, event=None):
        """Probe formats when camera selection changes."""
        idx = self._get_camera_index()
        if idx is None:
            return

        self.status_var.set(f"Probing camera {idx}...")
        self.root.update_idletasks()

        self._formats = probe_camera_formats(idx)

        resolutions = get_unique_resolutions(self._formats)
        if resolutions:
            res_items = [f"{w}x{h}" for w, h in resolutions]
            self.res_combo["values"] = res_items
            self.res_combo.current(0)  # max resolution (sorted descending)
            self._on_resolution_changed()
        else:
            # No formats reported — offer common defaults, max first
            defaults = ["3840x2160", "1920x1080", "1280x720", "640x480"]
            self.res_combo["values"] = defaults
            self.res_combo.current(0)  # default to 4K
            self.fps_combo["values"] = ["24", "30", "60"]
            self.fps_combo.current(len(self.fps_combo["values"]) - 1)  # max FPS

        self.status_var.set("Ready")

    def _on_resolution_changed(self, event=None):
        """Update FPS options for selected resolution."""
        res = self.res_var.get()
        if "x" not in res:
            return
        w, h = (int(x) for x in res.split("x"))
        fps_list = get_fps_for_resolution(self._formats, w, h)
        if fps_list:
            fps_strs = [str(int(f)) if f == int(f) else str(f) for f in fps_list]
            self.fps_combo["values"] = fps_strs
            self.fps_combo.current(len(fps_strs) - 1)  # max FPS
        else:
            self.fps_combo["values"] = ["24", "30", "60"]
            self.fps_combo.current(len(self.fps_combo["values"]) - 1)  # max FPS

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
            width=w,
            height=h,
            fps=fps,
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

        # Start timer update
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
        time_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"
        self.status_var.set(f"● REC #{self.clip_count}  {time_str}")
        self.root.after(500, self._update_timer)

    def _open_preview(self):
        """Launch a live preview window for the selected regular camera."""
        cam = self._get_camera_index()
        if cam is None:
            self.status_var.set("No camera selected!")
            return

        res = self.res_var.get()
        w, h = 1920, 1080
        if "x" in res:
            w, h = (int(x) for x in res.split("x"))
        fps = int(float(self.fps_var.get() or "30"))

        self.status_var.set("Opening preview...")
        self.root.update_idletasks()

        def _run():
            from preview import PreviewWindow
            pw = PreviewWindow(
                mode="regular",
                device_index=cam,
                width=w,
                height=h,
                fps=fps,
            )
            pw.run()
            # Update status back on main thread
            self.root.after(0, lambda: self.status_var.set("Preview closed"))

        threading.Thread(target=_run, daemon=True).start()

    def _open_thermal(self):
        """Launch a thermal camera preview window."""
        cam = self._get_camera_index()
        mode = self.thermal_mode_var.get()
        colormap = self.colormap_var.get()

        self.status_var.set(f"Opening thermal preview ({mode})...")
        self.root.update_idletasks()

        def _run():
            from preview import PreviewWindow
            pw = PreviewWindow(
                mode=mode,
                device_index=cam if cam is not None else 0,
                colormap=colormap,
            )
            pw.run()
            self.root.after(0, lambda: self.status_var.set("Thermal preview closed"))

        threading.Thread(target=_run, daemon=True).start()


def main():
    root = tk.Tk()
    app = RecorderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
