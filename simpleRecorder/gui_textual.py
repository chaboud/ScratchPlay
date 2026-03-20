#!/usr/bin/env python3
"""Textual TUI for simpleRecorder.

Terminal-based UI replicating the tkinter GUI functionality.
Provides selectors for camera, audio, resolution, frame rate,
codec, container, and quality. Record/Preview/Thermal/MultiCam buttons.
Pre-roll buffer, timecode overlay, thermal range lock.
"""

import multiprocessing
import os
import sys
import time

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical
    from textual.timer import Timer
    from textual.widgets import Button, Footer, Header, Input, Label, Select, Static, Switch
    from textual.worker import Worker
except (ImportError, ModuleNotFoundError):
    print("Error: textual is not installed.")
    print("  Fix: pip install textual")
    print("  Or use the CLI instead: python cli.py --help")
    sys.exit(1)

from devices import (
    list_avfoundation_devices,
    probe_camera_formats,
    get_unique_resolutions,
    get_fps_for_resolution,
)
from recorder import RecordingSession, CODEC_MAP


# ---------------------------------------------------------------------------
# Subprocess targets (must be top-level for multiprocessing)
# ---------------------------------------------------------------------------

def _run_preview_process(cam, w, h, fps, codec, container, crf, output_dir,
                         base_name, preroll, audio_device,
                         preview_tc, preview_meters,
                         record_tc, record_meters):
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
# Textual App
# ---------------------------------------------------------------------------

COLORMAP_OPTIONS = [
    "inferno", "jet", "hot", "turbo", "magma", "rainbow", "bone",
    "white_hot", "black_hot",
]

FALLBACK_RESOLUTIONS = ["3840x2160", "1920x1080", "1280x720", "640x480"]
FALLBACK_FPS = ["24", "30", "60"]


class RecorderTUI(App):
    """Textual TUI application for simpleRecorder."""

    TITLE = "simpleRecorder"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "toggle_record", "Record"),
    ]

    DEFAULT_CSS = """
    Screen {
        layout: vertical;
        overflow-y: auto;
    }

    #main-container {
        padding: 1 2;
    }

    .section {
        border: solid $accent;
        padding: 1 2;
        margin-bottom: 1;
    }

    .section-title {
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }

    .field-row {
        layout: horizontal;
        height: auto;
        margin-bottom: 1;
    }

    .field-label {
        width: 14;
        content-align-vertical: middle;
        padding-top: 1;
    }

    .field-widget {
        width: 1fr;
    }

    .field-widget-short {
        width: 20;
    }

    .field-widget-tiny {
        width: 10;
    }

    #btn-row {
        layout: horizontal;
        height: auto;
        margin-bottom: 1;
        padding: 1 0;
    }

    #btn-record {
        background: $error;
        color: $text;
        min-width: 16;
        margin-right: 1;
    }

    #btn-record.recording {
        background: $surface;
        color: $text;
    }

    #btn-preview {
        background: #336699;
        color: white;
        min-width: 16;
        margin-right: 1;
    }

    #btn-thermal {
        background: #cc6600;
        color: white;
        min-width: 16;
        margin-right: 1;
    }

    #btn-multi {
        background: #669933;
        color: white;
        min-width: 16;
        margin-right: 1;
    }

    #btn-refresh {
        margin-left: 1;
        min-width: 12;
    }

    .range-row {
        layout: horizontal;
        height: auto;
    }

    .range-input {
        width: 10;
    }

    .range-sep {
        width: 3;
        content-align-vertical: middle;
        padding-top: 1;
    }

    .range-hint {
        padding-top: 1;
        margin-left: 1;
        color: $text-muted;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        padding: 0 2;
    }

    .switch-row {
        layout: horizontal;
        height: auto;
    }

    .switch-label {
        padding-top: 1;
        margin-left: 1;
    }
    """

    def __init__(self):
        super().__init__()
        self._devices = {"video": [], "audio": []}
        self._formats = []
        self._session = None
        self._recording = False
        self._clip_count = 0
        self._rec_timer: Timer | None = None

    # ---- Compose ----

    def compose(self) -> ComposeResult:
        yield Header()

        with Vertical(id="main-container"):

            # --- Devices ---
            with Container(classes="section"):
                yield Label("Devices", classes="section-title")

                with Horizontal(classes="field-row"):
                    yield Label("Camera:", classes="field-label")
                    yield Select(
                        [], id="camera-select", classes="field-widget",
                        prompt="(no cameras)",
                    )
                    yield Button("Refresh", id="btn-refresh", variant="default")

                with Horizontal(classes="field-row"):
                    yield Label("Audio:", classes="field-label")
                    yield Select(
                        [("(none)", None)], id="audio-select",
                        classes="field-widget", value=None,
                    )

            # --- Format ---
            with Container(classes="section"):
                yield Label("Format", classes="section-title")

                with Horizontal(classes="field-row"):
                    yield Label("Resolution:", classes="field-label")
                    yield Select(
                        [], id="res-select", classes="field-widget-short",
                        prompt="(select)",
                    )
                    yield Label("FPS:", classes="field-label")
                    yield Select(
                        [], id="fps-select", classes="field-widget-tiny",
                        prompt="--",
                    )

                with Horizontal(classes="field-row"):
                    yield Label("Codec:", classes="field-label")
                    yield Select(
                        [("h264", "h264"), ("hevc", "hevc")],
                        id="codec-select", classes="field-widget-short",
                        value="h264",
                    )
                    yield Label("Container:", classes="field-label")
                    yield Select(
                        [("mov", "mov"), ("mp4", "mp4")],
                        id="container-select", classes="field-widget-tiny",
                        value="mov",
                    )

                with Horizontal(classes="field-row"):
                    yield Label("CRF:", classes="field-label")
                    yield Input(
                        value="20", id="crf-input",
                        classes="field-widget-tiny", type="integer",
                    )
                    yield Label("Pre-roll (s):", classes="field-label")
                    yield Input(
                        value="5", id="preroll-input",
                        classes="field-widget-tiny", type="integer",
                    )

            # --- Output ---
            with Container(classes="section"):
                yield Label("Output", classes="section-title")

                with Horizontal(classes="field-row"):
                    yield Label("Folder:", classes="field-label")
                    yield Input(
                        value=os.path.expanduser("~/Movies"),
                        id="dir-input", classes="field-widget",
                    )

                with Horizontal(classes="field-row"):
                    yield Label("Base name:", classes="field-label")
                    yield Input(
                        value="recording", id="name-input",
                        classes="field-widget",
                    )

            # --- Overlays ---
            with Container(classes="section"):
                yield Label("Overlays", classes="section-title")

                with Horizontal(classes="switch-row"):
                    yield Switch(value=True, id="preview-tc-switch")
                    yield Label("Preview Timecode", classes="switch-label")

                with Horizontal(classes="switch-row"):
                    yield Switch(value=True, id="preview-meters-switch")
                    yield Label("Preview Meters", classes="switch-label")

                with Horizontal(classes="switch-row"):
                    yield Switch(value=False, id="record-tc-switch")
                    yield Label("Recording Timecode", classes="switch-label")

                with Horizontal(classes="switch-row"):
                    yield Switch(value=False, id="record-meters-switch")
                    yield Label("Recording Meters", classes="switch-label")

            # --- Action Buttons ---
            with Horizontal(id="btn-row"):
                yield Button("● RECORD", id="btn-record", variant="error")
                yield Button("PREVIEW", id="btn-preview", variant="primary")
                yield Button("THERMAL", id="btn-thermal", variant="warning")
                yield Button("MULTI", id="btn-multi", variant="success")

            # --- Thermal Options ---
            with Container(classes="section"):
                yield Label("Thermal Options", classes="section-title")

                with Horizontal(classes="field-row"):
                    yield Label("Backend:", classes="field-label")
                    yield Select(
                        [("infiray", "infiray"), ("waveshare", "waveshare")],
                        id="thermal-mode-select", classes="field-widget-short",
                        value="infiray",
                    )
                    yield Label("Colormap:", classes="field-label")
                    yield Select(
                        [(c, c) for c in COLORMAP_OPTIONS],
                        id="colormap-select", classes="field-widget-short",
                        value="inferno",
                    )

                with Horizontal(classes="field-row"):
                    yield Label("Range lock:", classes="field-label")
                    with Horizontal(classes="range-row"):
                        yield Input(
                            placeholder="min", id="range-min-input",
                            classes="range-input",
                        )
                        yield Static(" - ", classes="range-sep")
                        yield Input(
                            placeholder="max", id="range-max-input",
                            classes="range-input",
                        )
                        yield Static("°C (blank=auto)", classes="range-hint")

        yield Static("Ready", id="status-bar")
        yield Footer()

    # ---- Lifecycle ----

    def on_mount(self) -> None:
        self._do_refresh_devices()

    # ---- Helpers ----

    def _set_status(self, msg: str) -> None:
        self.query_one("#status-bar", Static).update(msg)

    def _get_camera_index(self) -> int | None:
        sel = self.query_one("#camera-select", Select)
        val = sel.value
        if val is Select.BLANK or val is None:
            return None
        return int(val)

    def _get_audio_index(self) -> int | None:
        sel = self.query_one("#audio-select", Select)
        val = sel.value
        if val is Select.BLANK or val is None:
            return None
        return int(val)

    def _get_range_lock(self):
        try:
            lo = float(self.query_one("#range-min-input", Input).value)
            hi = float(self.query_one("#range-max-input", Input).value)
            return (lo, hi)
        except (ValueError, TypeError):
            return None

    # ---- Device handling ----

    def _do_refresh_devices(self) -> None:
        self._set_status("Scanning devices...")
        self.run_worker(self._refresh_devices_worker, thread=True)

    def _refresh_devices_worker(self) -> dict:
        return list_avfoundation_devices()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "_refresh_devices_worker" and event.worker.is_finished:
            result = event.worker.result
            if result is None:
                self._set_status("Device scan failed")
                return
            self._devices = result
            self._populate_device_selects()
            self._set_status("Ready")

        elif event.worker.name == "_probe_formats_worker" and event.worker.is_finished:
            result = event.worker.result
            if result is None:
                self._set_status("Format probe failed")
                return
            self._formats = result
            self._populate_resolution_select()
            self._set_status("Ready")

    def _populate_device_selects(self) -> None:
        cam_sel = self.query_one("#camera-select", Select)
        cam_options = [(f"[{i}] {n}", i) for i, n in self._devices["video"]]
        cam_sel.set_options(cam_options)
        if cam_options:
            cam_sel.value = cam_options[0][1]

        audio_sel = self.query_one("#audio-select", Select)
        audio_options = [("(none)", None)] + [
            (f"[{i}] {n}", i) for i, n in self._devices["audio"]
        ]
        audio_sel.set_options(audio_options)
        audio_sel.value = None

    def _on_camera_changed(self, cam_idx: int) -> None:
        self._set_status(f"Probing camera {cam_idx}...")
        self.run_worker(
            lambda: probe_camera_formats(cam_idx),
            thread=True,
            name="_probe_formats_worker",
        )

    def _populate_resolution_select(self) -> None:
        res_sel = self.query_one("#res-select", Select)
        fps_sel = self.query_one("#fps-select", Select)

        resolutions = get_unique_resolutions(self._formats)

        if resolutions:
            opts = [(f"{w}x{h}", f"{w}x{h}") for w, h in resolutions]
            res_sel.set_options(opts)
            res_sel.value = opts[0][1]
            self._update_fps_for_resolution(opts[0][1])
        else:
            opts = [(r, r) for r in FALLBACK_RESOLUTIONS]
            res_sel.set_options(opts)
            res_sel.value = opts[0][1]
            fps_opts = [(f, f) for f in FALLBACK_FPS]
            fps_sel.set_options(fps_opts)
            fps_sel.value = fps_opts[-1][1]

    def _update_fps_for_resolution(self, res_str: str) -> None:
        fps_sel = self.query_one("#fps-select", Select)
        if "x" not in res_str:
            return
        w, h = (int(x) for x in res_str.split("x"))
        fps_list = get_fps_for_resolution(self._formats, w, h)
        if fps_list:
            fps_strs = [
                str(int(f)) if f == int(f) else str(f) for f in fps_list
            ]
            opts = [(s, s) for s in fps_strs]
            fps_sel.set_options(opts)
            fps_sel.value = opts[-1][1]
        else:
            opts = [(f, f) for f in FALLBACK_FPS]
            fps_sel.set_options(opts)
            fps_sel.value = opts[-1][1]

    # ---- Event handlers ----

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "camera-select":
            val = event.value
            if val is not Select.BLANK and val is not None:
                self._on_camera_changed(int(val))
        elif event.select.id == "res-select":
            val = event.value
            if val is not Select.BLANK and val is not None:
                self._update_fps_for_resolution(str(val))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-refresh":
            self._do_refresh_devices()
        elif btn_id == "btn-record":
            self._toggle_record()
        elif btn_id == "btn-preview":
            self._open_preview()
        elif btn_id == "btn-thermal":
            self._open_thermal()
        elif btn_id == "btn-multi":
            self._open_multicam()

    def action_toggle_record(self) -> None:
        self._toggle_record()

    # ---- Recording ----

    def _toggle_record(self) -> None:
        if not self._recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        cam = self._get_camera_index()
        if cam is None:
            self._set_status("No camera selected!")
            return

        res_sel = self.query_one("#res-select", Select)
        res = res_sel.value
        if res is Select.BLANK or res is None or "x" not in str(res):
            self._set_status("No resolution selected!")
            return

        w, h = (int(x) for x in str(res).split("x"))

        fps_sel = self.query_one("#fps-select", Select)
        fps_val = fps_sel.value
        fps = int(float(fps_val)) if fps_val and fps_val is not Select.BLANK else 30

        crf_input = self.query_one("#crf-input", Input)
        try:
            crf = int(crf_input.value)
        except ValueError:
            crf = None

        codec_sel = self.query_one("#codec-select", Select)
        container_sel = self.query_one("#container-select", Select)
        dir_input = self.query_one("#dir-input", Input)
        name_input = self.query_one("#name-input", Input)

        self._session = RecordingSession(
            video_device=cam,
            audio_device=self._get_audio_index(),
            width=w, height=h, fps=fps,
            codec=str(codec_sel.value),
            container=str(container_sel.value),
            crf=crf,
            output_dir=dir_input.value,
            base_name=name_input.value,
        )

        path = self._session.start()
        self._recording = True
        self._clip_count += 1

        rec_btn = self.query_one("#btn-record", Button)
        rec_btn.label = "■ STOP"
        rec_btn.add_class("recording")

        self._set_status(
            f"Recording #{self._clip_count}: {os.path.basename(path)}"
        )
        self._rec_timer = self.set_interval(0.5, self._update_timer)

    def _stop_recording(self) -> None:
        if self._rec_timer is not None:
            self._rec_timer.stop()
            self._rec_timer = None

        if self._session:
            path, dur = self._session.stop()
            self._set_status(
                f"Saved: {os.path.basename(path)} ({dur:.1f}s)"
            )

        self._recording = False
        self._session = None

        rec_btn = self.query_one("#btn-record", Button)
        rec_btn.label = "● RECORD"
        rec_btn.remove_class("recording")

    def _update_timer(self) -> None:
        if not self._recording or not self._session:
            return
        elapsed = self._session.elapsed
        mins, secs = divmod(int(elapsed), 60)
        hrs, mins = divmod(mins, 60)
        self._set_status(
            f"● REC #{self._clip_count}  {hrs:02d}:{mins:02d}:{secs:02d}"
        )

    # ---- Preview ----

    def _open_preview(self) -> None:
        cam = self._get_camera_index()
        if cam is None:
            self._set_status("No camera selected!")
            return

        res_sel = self.query_one("#res-select", Select)
        res = res_sel.value
        w, h = 1920, 1080
        if res is not Select.BLANK and res is not None and "x" in str(res):
            w, h = (int(x) for x in str(res).split("x"))

        fps_sel = self.query_one("#fps-select", Select)
        fps_val = fps_sel.value
        fps = int(float(fps_val)) if fps_val and fps_val is not Select.BLANK else 30

        preroll_input = self.query_one("#preroll-input", Input)
        try:
            preroll = float(preroll_input.value)
        except ValueError:
            preroll = 5.0

        crf_input = self.query_one("#crf-input", Input)
        try:
            crf = int(crf_input.value)
        except ValueError:
            crf = 20

        codec_sel = self.query_one("#codec-select", Select)
        container_sel = self.query_one("#container-select", Select)
        dir_input = self.query_one("#dir-input", Input)
        name_input = self.query_one("#name-input", Input)

        preview_tc = self.query_one("#preview-tc-switch", Switch).value
        preview_meters = self.query_one("#preview-meters-switch", Switch).value
        record_tc = self.query_one("#record-tc-switch", Switch).value
        record_meters = self.query_one("#record-meters-switch", Switch).value

        self._set_status("Opening preview...")

        p = multiprocessing.Process(
            target=_run_preview_process,
            args=(
                cam, w, h, fps, str(codec_sel.value),
                str(container_sel.value), crf, dir_input.value,
                name_input.value, preroll,
                self._get_audio_index(),
                preview_tc, preview_meters,
                record_tc, record_meters,
            ),
            daemon=True,
        )
        p.start()

    # ---- Thermal ----

    def _open_thermal(self) -> None:
        cam = self._get_camera_index()
        thermal_sel = self.query_one("#thermal-mode-select", Select)
        colormap_sel = self.query_one("#colormap-select", Select)
        dir_input = self.query_one("#dir-input", Input)
        name_input = self.query_one("#name-input", Input)

        mode = str(thermal_sel.value)
        colormap = str(colormap_sel.value)
        range_lock = self._get_range_lock()

        self._set_status(f"Opening thermal preview ({mode})...")

        p = multiprocessing.Process(
            target=_run_thermal_process,
            args=(
                mode, cam if cam is not None else 0, colormap,
                dir_input.value, name_input.value,
                range_lock, self._get_audio_index(),
            ),
            daemon=True,
        )
        p.start()

    # ---- Multi-camera ----

    def _open_multicam(self) -> None:
        if len(self._devices["video"]) < 2:
            self._set_status("Need 2+ cameras for multi-cam mode")
            return

        dir_input = self.query_one("#dir-input", Input)
        name_input = self.query_one("#name-input", Input)

        self._set_status("Opening multi-cam preview...")

        video_devices = list(self._devices["video"])
        p = multiprocessing.Process(
            target=_run_multicam_process,
            args=(dir_input.value, name_input.value, video_devices),
            daemon=True,
        )
        p.start()


def main():
    app = RecorderTUI()
    app.run()


if __name__ == "__main__":
    main()
