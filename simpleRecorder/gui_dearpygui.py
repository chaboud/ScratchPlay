#!/usr/bin/env python3
"""Dear PyGui GUI for simpleRecorder.

Provides dropdown selectors for camera, audio, resolution, frame rate,
codec, container, and quality. Record/Preview/Thermal/MultiCam buttons.
Pre-roll buffer, timecode overlay, thermal range lock.
"""

import multiprocessing
import os
import sys
import time

try:
    import dearpygui.dearpygui as dpg
except (ImportError, ModuleNotFoundError):
    print("Error: dearpygui is not installed.")
    print("  Fix: pip install dearpygui")
    sys.exit(1)

from devices import (
    list_avfoundation_devices,
    probe_camera_formats,
    get_unique_resolutions,
    get_fps_for_resolution,
)
from recorder import RecordingSession, CODEC_MAP


# ---------------------------------------------------------------------------
# Process-target helpers (must be at module level for multiprocessing)
# ---------------------------------------------------------------------------

def _run_preview_process(cam, w, h, fps, codec, container, crf, output_dir,
                         base_name, preroll, audio_device,
                         preview_tc, preview_meters, record_tc, record_meters):
    """Run preview in a separate process so OpenCV gets its own main thread."""
    from pipeline import RecordingPipeline
    pipe = RecordingPipeline(
        device_index=cam, width=w, height=h, fps=fps,
        codec=codec, container=container, crf=crf,
        output_dir=output_dir, base_name=base_name,
        pre_roll_seconds=preroll, audio_device=audio_device,
        preview_timecode=preview_tc, preview_meters=preview_meters,
        record_timecode=record_tc, record_meters=record_meters,
    )
    pipe.open()
    try:
        pipe.show_preview()
    finally:
        pipe.close()


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


# ---------------------------------------------------------------------------
# Theme helpers
# ---------------------------------------------------------------------------

def _make_button_theme(r, g, b):
    """Create a Dear PyGui theme for a coloured button."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b), category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,
                                (min(r + 40, 255), min(g + 40, 255), min(b + 40, 255)),
                                category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,
                                (min(r + 70, 255), min(g + 70, 255), min(b + 70, 255)),
                                category=dpg.mvThemeCat_Core)
            dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 255, 255), category=dpg.mvThemeCat_Core)
    return theme


# ---------------------------------------------------------------------------
# Application class
# ---------------------------------------------------------------------------

class RecorderApp:
    def __init__(self):
        self.session = None
        self.recording = False
        self.clip_count = 0
        self.timer_running = False
        self._record_start = 0.0

        self._devices = {"video": [], "audio": []}
        self._formats = []

        # Theme references (created after dpg context)
        self._theme_record = None
        self._theme_stop = None
        self._theme_preview = None
        self._theme_thermal = None
        self._theme_multi = None

        # Widget tags
        self.camera_combo = None
        self.audio_combo = None
        self.res_combo = None
        self.fps_combo = None
        self.codec_combo = None
        self.container_combo = None
        self.crf_input = None
        self.preroll_input = None
        self.dir_input = None
        self.name_input = None
        self.preview_tc_check = None
        self.preview_meters_check = None
        self.record_btn = None
        self.thermal_mode_combo = None
        self.colormap_combo = None
        self.range_min_input = None
        self.range_max_input = None
        self.status_text = None

    # ---- build ----

    def setup(self):
        """Build the Dear PyGui UI. Must be called after dpg.create_context()."""
        self._theme_record = _make_button_theme(204, 0, 0)
        self._theme_stop = _make_button_theme(51, 51, 51)
        self._theme_preview = _make_button_theme(51, 102, 153)
        self._theme_thermal = _make_button_theme(204, 102, 0)
        self._theme_multi = _make_button_theme(102, 153, 51)

        with dpg.window(tag="primary", label="simpleRecorder"):
            # --- Devices ---
            with dpg.collapsing_header(label="Devices", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("Camera:")
                    self.camera_combo = dpg.add_combo(
                        [], width=300, callback=self._on_camera_changed,
                    )
                    dpg.add_button(label="Refresh", callback=self._refresh_devices)

                with dpg.group(horizontal=True):
                    dpg.add_text("Audio: ")
                    self.audio_combo = dpg.add_combo([], width=300)

            dpg.add_spacer(height=4)

            # --- Format ---
            with dpg.collapsing_header(label="Format", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("Resolution:")
                    self.res_combo = dpg.add_combo(
                        [], width=140, callback=self._on_resolution_changed,
                    )
                    dpg.add_text("  FPS:")
                    self.fps_combo = dpg.add_combo([], width=80)

                with dpg.group(horizontal=True):
                    dpg.add_text("Codec:")
                    self.codec_combo = dpg.add_combo(
                        ["h264", "hevc"], default_value="h264", width=100,
                    )
                    dpg.add_text("  Container:")
                    self.container_combo = dpg.add_combo(
                        ["mov", "mp4"], default_value="mov", width=80,
                    )

                with dpg.group(horizontal=True):
                    dpg.add_text("CRF:")
                    self.crf_input = dpg.add_input_int(
                        default_value=20, min_value=0, max_value=51,
                        min_clamped=True, max_clamped=True, width=80,
                    )
                    dpg.add_text("  Pre-roll (s):")
                    self.preroll_input = dpg.add_input_int(
                        default_value=5, min_value=0, max_value=30,
                        min_clamped=True, max_clamped=True, width=80,
                    )

            dpg.add_spacer(height=4)

            # --- Output ---
            with dpg.collapsing_header(label="Output", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("Folder:")
                    self.dir_input = dpg.add_input_text(
                        default_value=os.path.expanduser("~/Movies"), width=280,
                    )
                    dpg.add_button(label="Browse...", callback=self._browse_dir)

                with dpg.group(horizontal=True):
                    dpg.add_text("Base name:")
                    self.name_input = dpg.add_input_text(
                        default_value="recording", width=200,
                    )

            dpg.add_spacer(height=4)

            # --- Preview Overlays ---
            with dpg.collapsing_header(label="Preview Overlays", default_open=True):
                with dpg.group(horizontal=True):
                    self.preview_tc_check = dpg.add_checkbox(
                        label="Timecode", default_value=True,
                    )
                    self.preview_meters_check = dpg.add_checkbox(
                        label="Meters", default_value=True,
                    )

            # Folder dialog (hidden until Browse is clicked)
            with dpg.file_dialog(
                directory_selector=True,
                show=False,
                callback=self._folder_selected,
                tag="folder_dialog",
                width=500,
                height=400,
            ):
                pass

            dpg.add_spacer(height=8)

            # --- Main Buttons ---
            with dpg.group(horizontal=True):
                self.record_btn = dpg.add_button(
                    label="  RECORD  ", callback=self._toggle_record,
                    height=50, width=130,
                )
                dpg.bind_item_theme(self.record_btn, self._theme_record)

                preview_btn = dpg.add_button(
                    label="  PREVIEW  ", callback=self._open_preview,
                    height=50, width=130,
                )
                dpg.bind_item_theme(preview_btn, self._theme_preview)

                thermal_btn = dpg.add_button(
                    label="  THERMAL  ", callback=self._open_thermal,
                    height=50, width=130,
                )
                dpg.bind_item_theme(thermal_btn, self._theme_thermal)

                multi_btn = dpg.add_button(
                    label="  MULTI  ", callback=self._open_multicam,
                    height=50, width=110,
                )
                dpg.bind_item_theme(multi_btn, self._theme_multi)

            dpg.add_spacer(height=4)

            # --- Thermal Options ---
            with dpg.collapsing_header(label="Thermal Options", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("Backend:")
                    self.thermal_mode_combo = dpg.add_combo(
                        ["infiray", "waveshare"], default_value="infiray", width=120,
                    )
                    dpg.add_text("  Colormap:")
                    self.colormap_combo = dpg.add_combo(
                        ["inferno", "jet", "hot", "turbo", "magma",
                         "rainbow", "bone", "white_hot", "black_hot"],
                        default_value="inferno", width=120,
                    )

                with dpg.group(horizontal=True):
                    dpg.add_text("Range lock:")
                    self.range_min_input = dpg.add_input_text(
                        default_value="", width=60, hint="min",
                    )
                    dpg.add_text(" - ")
                    self.range_max_input = dpg.add_input_text(
                        default_value="", width=60, hint="max",
                    )
                    dpg.add_text(" C (blank=auto)")

            dpg.add_spacer(height=4)
            dpg.add_separator()

            # --- Status ---
            self.status_text = dpg.add_text("Ready")

        # Kick off initial device scan
        self._refresh_devices()

    # ---- helpers ----

    def _set_status(self, msg):
        dpg.set_value(self.status_text, msg)

    def _get_camera_index(self):
        val = dpg.get_value(self.camera_combo)
        if val and val.startswith("["):
            try:
                return int(val.split("]")[0][1:])
            except ValueError:
                pass
        return None

    def _get_audio_index(self):
        val = dpg.get_value(self.audio_combo)
        if val and val.startswith("["):
            try:
                return int(val.split("]")[0][1:])
            except ValueError:
                pass
        return None

    def _get_range_lock(self):
        try:
            lo = float(dpg.get_value(self.range_min_input))
            hi = float(dpg.get_value(self.range_max_input))
            return (lo, hi)
        except (ValueError, TypeError):
            return None

    # ---- devices ----

    def _refresh_devices(self, sender=None, app_data=None):
        self._set_status("Scanning devices...")
        self._devices = list_avfoundation_devices()

        cam_items = [f"[{i}] {n}" for i, n in self._devices["video"]]
        if not cam_items:
            cam_items = ["(no cameras found)"]
        dpg.configure_item(self.camera_combo, items=cam_items)
        dpg.set_value(self.camera_combo, cam_items[0])
        if self._devices["video"]:
            self._on_camera_changed()

        audio_items = ["(none)"] + [f"[{i}] {n}" for i, n in self._devices["audio"]]
        dpg.configure_item(self.audio_combo, items=audio_items)
        dpg.set_value(self.audio_combo, audio_items[0])

        self._set_status("Ready")

    def _on_camera_changed(self, sender=None, app_data=None):
        idx = self._get_camera_index()
        if idx is None:
            return

        self._set_status(f"Probing camera {idx}...")
        self._formats = probe_camera_formats(idx)
        resolutions = get_unique_resolutions(self._formats)

        if resolutions:
            res_strs = [f"{w}x{h}" for w, h in resolutions]
            dpg.configure_item(self.res_combo, items=res_strs)
            dpg.set_value(self.res_combo, res_strs[0])
            self._on_resolution_changed()
        else:
            fallback = ["1920x1080", "1280x720", "640x480"]
            dpg.configure_item(self.res_combo, items=fallback)
            dpg.set_value(self.res_combo, fallback[0])
            fps_fallback = ["24", "30"]
            dpg.configure_item(self.fps_combo, items=fps_fallback)
            dpg.set_value(self.fps_combo, fps_fallback[-1])

        self._set_status("Ready")

    def _on_resolution_changed(self, sender=None, app_data=None):
        res = dpg.get_value(self.res_combo)
        if not res or "x" not in res:
            return
        w, h = (int(x) for x in res.split("x"))
        fps_list = get_fps_for_resolution(self._formats, w, h)
        if fps_list:
            fps_strs = [str(int(f)) if f == int(f) else str(f) for f in fps_list]
        else:
            fps_strs = ["24", "30"]
        dpg.configure_item(self.fps_combo, items=fps_strs)
        dpg.set_value(self.fps_combo, fps_strs[-1])

    # ---- folder browser ----

    def _browse_dir(self, sender=None, app_data=None):
        dpg.show_item("folder_dialog")

    def _folder_selected(self, sender, app_data):
        if app_data and "file_path_name" in app_data:
            dpg.set_value(self.dir_input, app_data["file_path_name"])

    # ---- recording ----

    def _toggle_record(self, sender=None, app_data=None):
        if not self.recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        cam = self._get_camera_index()
        if cam is None:
            self._set_status("No camera selected!")
            return

        res = dpg.get_value(self.res_combo)
        if not res or "x" not in res:
            self._set_status("No resolution selected!")
            return

        w, h = (int(x) for x in res.split("x"))
        fps_str = dpg.get_value(self.fps_combo) or "30"
        fps = int(float(fps_str))

        crf = dpg.get_value(self.crf_input)

        self.session = RecordingSession(
            video_device=cam,
            audio_device=self._get_audio_index(),
            width=w, height=h, fps=fps,
            codec=dpg.get_value(self.codec_combo),
            container=dpg.get_value(self.container_combo),
            crf=crf,
            output_dir=dpg.get_value(self.dir_input),
            base_name=dpg.get_value(self.name_input),
        )

        try:
            path = self.session.start()
        except RuntimeError as e:
            self.session = None
            self._set_status(f"Record failed: {e}")
            return

        self.recording = True
        self.clip_count += 1
        self._record_start = time.time()

        dpg.set_item_label(self.record_btn, "  STOP  ")
        dpg.bind_item_theme(self.record_btn, self._theme_stop)
        self._set_status(f"Recording #{self.clip_count}: {os.path.basename(path)}")
        self.timer_running = True

    def _stop_recording(self):
        self.timer_running = False
        if self.session:
            path, dur = self.session.stop()
            self._set_status(f"Saved: {os.path.basename(path)} ({dur:.1f}s)")
        self.recording = False
        self.session = None
        dpg.set_item_label(self.record_btn, "  RECORD  ")
        dpg.bind_item_theme(self.record_btn, self._theme_record)

    def _update_timer(self):
        """Called each frame; updates the status line while recording."""
        if not self.timer_running or not self.session:
            return
        elapsed = self.session.elapsed
        mins, secs = divmod(int(elapsed), 60)
        hrs, mins = divmod(mins, 60)
        self._set_status(f"REC #{self.clip_count}  {hrs:02d}:{mins:02d}:{secs:02d}")

    # ---- preview ----

    def _open_preview(self, sender=None, app_data=None):
        cam = self._get_camera_index()
        if cam is None:
            self._set_status("No camera selected!")
            return

        res = dpg.get_value(self.res_combo)
        w, h = 1920, 1080
        if res and "x" in res:
            w, h = (int(x) for x in res.split("x"))
        fps_str = dpg.get_value(self.fps_combo) or "30"
        fps = int(float(fps_str))

        preroll = dpg.get_value(self.preroll_input)
        crf = dpg.get_value(self.crf_input)
        preview_tc = dpg.get_value(self.preview_tc_check)
        preview_meters = dpg.get_value(self.preview_meters_check)

        self._set_status("Opening preview...")

        p = multiprocessing.Process(
            target=_run_preview_process,
            args=(cam, w, h, fps, dpg.get_value(self.codec_combo),
                  dpg.get_value(self.container_combo), crf,
                  dpg.get_value(self.dir_input),
                  dpg.get_value(self.name_input), preroll,
                  self._get_audio_index(),
                  preview_tc, preview_meters, False, False),
            daemon=True,
        )
        p.start()

    # ---- thermal ----

    def _open_thermal(self, sender=None, app_data=None):
        cam = self._get_camera_index()
        mode = dpg.get_value(self.thermal_mode_combo)
        colormap = dpg.get_value(self.colormap_combo)
        range_lock = self._get_range_lock()

        self._set_status(f"Opening thermal preview ({mode})...")

        p = multiprocessing.Process(
            target=_run_thermal_process,
            args=(mode, cam if cam is not None else 0, colormap,
                  dpg.get_value(self.dir_input),
                  dpg.get_value(self.name_input),
                  range_lock, self._get_audio_index()),
            daemon=True,
        )
        p.start()

    # ---- multicam ----

    def _open_multicam(self, sender=None, app_data=None):
        if len(self._devices["video"]) < 2:
            self._set_status("Need 2+ cameras for multi-cam mode")
            return

        self._set_status("Opening multi-cam preview...")

        video_devices = list(self._devices["video"])
        p = multiprocessing.Process(
            target=_run_multicam_process,
            args=(dpg.get_value(self.dir_input),
                  dpg.get_value(self.name_input), video_devices),
            daemon=True,
        )
        p.start()


def main():
    dpg.create_context()

    app = RecorderApp()
    app.setup()

    dpg.create_viewport(title="simpleRecorder", width=620, height=580)
    dpg.setup_dearpygui()
    dpg.show_viewport()

    dpg.set_primary_window("primary", True)

    # Main render loop with timer update
    while dpg.is_dearpygui_running():
        app._update_timer()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    main()
