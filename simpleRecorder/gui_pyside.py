#!/usr/bin/env python3
"""PySide6 GUI for simpleRecorder.

Provides dropdown selectors for camera, audio, resolution, frame rate,
codec, container, and quality. Record/Preview/Thermal/MultiCam buttons.
Audio level meter, pre-roll buffer, timecode overlay, thermal range lock.
"""

import multiprocessing
import os
import sys
import time

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFileDialog, QGroupBox,
        QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
        QSpinBox, QVBoxLayout, QWidget,
    )
except (ImportError, ModuleNotFoundError):
    print("Error: PySide6 is not available in this Python installation.")
    print("  Install it with: pip install PySide6")
    print("  Or use the CLI instead: python cli.py --help")
    sys.exit(1)

from devices import (
    list_avfoundation_devices,
    probe_camera_formats,
    get_unique_resolutions,
    get_fps_for_resolution,
)
from recorder import RecordingSession, CODEC_MAP


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


class RecorderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("simpleRecorder")
        self.setFixedSize(self.sizeHint())

        self.session = None
        self.recording = False
        self.clip_count = 0

        self._devices = {"video": [], "audio": []}
        self._formats = []

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._update_timer)

        self._build_ui()
        self._refresh_devices()

        # Resize to fit contents after building
        self.adjustSize()
        self.setFixedSize(self.size())

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # --- Device Selection ---
        dev_group = QGroupBox("Devices")
        dev_layout = QVBoxLayout(dev_group)

        cam_row = QHBoxLayout()
        cam_row.addWidget(QLabel("Camera:"))
        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(250)
        self.camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        cam_row.addWidget(self.camera_combo, 1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_devices)
        cam_row.addWidget(refresh_btn)
        dev_layout.addLayout(cam_row)

        audio_row = QHBoxLayout()
        audio_row.addWidget(QLabel("Audio:"))
        self.audio_combo = QComboBox()
        self.audio_combo.setMinimumWidth(250)
        audio_row.addWidget(self.audio_combo, 1)
        dev_layout.addLayout(audio_row)

        main_layout.addWidget(dev_group)

        # --- Format Selection ---
        fmt_group = QGroupBox("Format")
        fmt_layout = QVBoxLayout(fmt_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Resolution:"))
        self.res_combo = QComboBox()
        self.res_combo.setMinimumWidth(120)
        self.res_combo.currentIndexChanged.connect(self._on_resolution_changed)
        row1.addWidget(self.res_combo)
        row1.addSpacing(12)
        row1.addWidget(QLabel("FPS:"))
        self.fps_combo = QComboBox()
        self.fps_combo.setMinimumWidth(60)
        row1.addWidget(self.fps_combo)
        row1.addStretch()
        fmt_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Codec:"))
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["h264", "hevc"])
        self.codec_combo.setMinimumWidth(80)
        row2.addWidget(self.codec_combo)
        row2.addSpacing(12)
        row2.addWidget(QLabel("Container:"))
        self.container_combo = QComboBox()
        self.container_combo.addItems(["mov", "mp4"])
        self.container_combo.setMinimumWidth(60)
        row2.addWidget(self.container_combo)
        row2.addStretch()
        fmt_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("CRF:"))
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(20)
        self.crf_spin.setFixedWidth(60)
        row3.addWidget(self.crf_spin)
        row3.addSpacing(12)
        row3.addWidget(QLabel("Pre-roll (s):"))
        self.preroll_spin = QSpinBox()
        self.preroll_spin.setRange(0, 30)
        self.preroll_spin.setValue(5)
        self.preroll_spin.setFixedWidth(60)
        row3.addWidget(self.preroll_spin)
        row3.addStretch()
        fmt_layout.addLayout(row3)

        main_layout.addWidget(fmt_group)

        # --- Output ---
        out_group = QGroupBox("Output")
        out_layout = QVBoxLayout(out_group)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Folder:"))
        self.dir_edit = QLineEdit(os.path.expanduser("~/Movies"))
        dir_row.addWidget(self.dir_edit, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)
        out_layout.addLayout(dir_row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Base name:"))
        self.name_edit = QLineEdit("recording")
        name_row.addWidget(self.name_edit, 1)
        out_layout.addLayout(name_row)

        main_layout.addWidget(out_group)

        # --- Preview Overlays ---
        overlay_group = QGroupBox("Preview Overlays")
        overlay_layout = QHBoxLayout(overlay_group)

        self.preview_tc_check = QCheckBox("Timecode")
        self.preview_tc_check.setChecked(True)
        overlay_layout.addWidget(self.preview_tc_check)
        self.preview_meters_check = QCheckBox("Meters")
        self.preview_meters_check.setChecked(True)
        overlay_layout.addWidget(self.preview_meters_check)
        overlay_layout.addStretch()

        main_layout.addWidget(overlay_group)

        # --- Main Buttons ---
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.record_btn = QPushButton("\u25cf RECORD")
        self.record_btn.setFont(QFont("Helvetica", 14, QFont.Bold))
        self.record_btn.setFixedHeight(50)
        self.record_btn.setMinimumWidth(110)
        self.record_btn.setStyleSheet(
            "QPushButton { color: white; background-color: #cc0000; border: none; border-radius: 4px; }"
            "QPushButton:hover { background-color: #ff3333; }"
        )
        self.record_btn.clicked.connect(self._toggle_record)
        btn_layout.addWidget(self.record_btn)

        preview_btn = QPushButton("PREVIEW")
        preview_btn.setFont(QFont("Helvetica", 12, QFont.Bold))
        preview_btn.setFixedHeight(50)
        preview_btn.setMinimumWidth(110)
        preview_btn.setStyleSheet(
            "QPushButton { color: white; background-color: #336699; border: none; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4488bb; }"
        )
        preview_btn.clicked.connect(self._open_preview)
        btn_layout.addWidget(preview_btn)

        thermal_btn = QPushButton("THERMAL")
        thermal_btn.setFont(QFont("Helvetica", 12, QFont.Bold))
        thermal_btn.setFixedHeight(50)
        thermal_btn.setMinimumWidth(110)
        thermal_btn.setStyleSheet(
            "QPushButton { color: white; background-color: #cc6600; border: none; border-radius: 4px; }"
            "QPushButton:hover { background-color: #ee8833; }"
        )
        thermal_btn.clicked.connect(self._open_thermal)
        btn_layout.addWidget(thermal_btn)

        multi_btn = QPushButton("MULTI")
        multi_btn.setFont(QFont("Helvetica", 12, QFont.Bold))
        multi_btn.setFixedHeight(50)
        multi_btn.setMinimumWidth(90)
        multi_btn.setStyleSheet(
            "QPushButton { color: white; background-color: #669933; border: none; border-radius: 4px; }"
            "QPushButton:hover { background-color: #88bb55; }"
        )
        multi_btn.clicked.connect(self._open_multicam)
        btn_layout.addWidget(multi_btn)

        main_layout.addLayout(btn_layout)

        # --- Thermal Options ---
        therm_group = QGroupBox("Thermal Options")
        therm_layout = QVBoxLayout(therm_group)

        therm_row1 = QHBoxLayout()
        therm_row1.addWidget(QLabel("Backend:"))
        self.thermal_mode_combo = QComboBox()
        self.thermal_mode_combo.addItems(["infiray", "waveshare"])
        self.thermal_mode_combo.setMinimumWidth(100)
        therm_row1.addWidget(self.thermal_mode_combo)
        therm_row1.addSpacing(8)
        therm_row1.addWidget(QLabel("Colormap:"))
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems([
            "inferno", "jet", "hot", "turbo", "magma", "rainbow",
            "bone", "white_hot", "black_hot",
        ])
        self.colormap_combo.setMinimumWidth(100)
        therm_row1.addWidget(self.colormap_combo)
        therm_row1.addStretch()
        therm_layout.addLayout(therm_row1)

        therm_row2 = QHBoxLayout()
        therm_row2.addWidget(QLabel("Range lock:"))
        self.range_min_edit = QLineEdit()
        self.range_min_edit.setFixedWidth(50)
        therm_row2.addWidget(self.range_min_edit)
        therm_row2.addWidget(QLabel(" - "))
        self.range_max_edit = QLineEdit()
        self.range_max_edit.setFixedWidth(50)
        therm_row2.addWidget(self.range_max_edit)
        therm_row2.addWidget(QLabel(" C (blank=auto)"))
        therm_row2.addStretch()
        therm_layout.addLayout(therm_row2)

        main_layout.addWidget(therm_group)

        # --- Status bar ---
        self.status_label = QLabel("Ready")
        main_layout.addWidget(self.status_label)

    # --- Device handling ---

    def _refresh_devices(self):
        self.status_label.setText("Scanning devices...")
        QApplication.processEvents()

        self._devices = list_avfoundation_devices()

        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        cam_items = [f"[{i}] {n}" for i, n in self._devices["video"]]
        if cam_items:
            self.camera_combo.addItems(cam_items)
            self.camera_combo.setCurrentIndex(0)
        else:
            self.camera_combo.addItem("(no cameras found)")
        self.camera_combo.blockSignals(False)

        self.audio_combo.clear()
        audio_items = ["(none)"] + [f"[{i}] {n}" for i, n in self._devices["audio"]]
        self.audio_combo.addItems(audio_items)
        self.audio_combo.setCurrentIndex(0)

        if cam_items:
            self._on_camera_changed()

        self.status_label.setText("Ready")

    def _on_camera_changed(self, index=None):
        idx = self._get_camera_index()
        if idx is None:
            return

        self.status_label.setText(f"Probing camera {idx}...")
        QApplication.processEvents()

        self._formats = probe_camera_formats(idx)
        resolutions = get_unique_resolutions(self._formats)

        self.res_combo.blockSignals(True)
        self.res_combo.clear()
        if resolutions:
            self.res_combo.addItems([f"{w}x{h}" for w, h in resolutions])
            self.res_combo.setCurrentIndex(0)
            self.res_combo.blockSignals(False)
            self._on_resolution_changed()
        else:
            self.res_combo.addItems(["3840x2160", "1920x1080", "1280x720", "640x480"])
            self.res_combo.setCurrentIndex(0)
            self.res_combo.blockSignals(False)
            self.fps_combo.clear()
            self.fps_combo.addItems(["24", "30", "60"])
            self.fps_combo.setCurrentIndex(self.fps_combo.count() - 1)

        self.status_label.setText("Ready")

    def _on_resolution_changed(self, index=None):
        res = self.res_combo.currentText()
        if "x" not in res:
            return
        w, h = (int(x) for x in res.split("x"))
        fps_list = get_fps_for_resolution(self._formats, w, h)
        self.fps_combo.clear()
        if fps_list:
            fps_strs = [str(int(f)) if f == int(f) else str(f) for f in fps_list]
            self.fps_combo.addItems(fps_strs)
            self.fps_combo.setCurrentIndex(self.fps_combo.count() - 1)
        else:
            self.fps_combo.addItems(["24", "30", "60"])
            self.fps_combo.setCurrentIndex(self.fps_combo.count() - 1)

    def _get_camera_index(self):
        val = self.camera_combo.currentText()
        if val.startswith("["):
            try:
                return int(val.split("]")[0][1:])
            except ValueError:
                pass
        return None

    def _get_audio_index(self):
        val = self.audio_combo.currentText()
        if val.startswith("["):
            try:
                return int(val.split("]")[0][1:])
            except ValueError:
                pass
        return None

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder",
                                             self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _get_range_lock(self):
        try:
            lo = float(self.range_min_edit.text())
            hi = float(self.range_max_edit.text())
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
            self.status_label.setText("No camera selected!")
            return

        res = self.res_combo.currentText()
        if "x" not in res:
            self.status_label.setText("No resolution selected!")
            return

        w, h = (int(x) for x in res.split("x"))
        fps_text = self.fps_combo.currentText() or "30"
        fps = int(float(fps_text))
        crf = self.crf_spin.value()

        self.session = RecordingSession(
            video_device=cam,
            audio_device=self._get_audio_index(),
            width=w, height=h, fps=fps,
            codec=self.codec_combo.currentText(),
            container=self.container_combo.currentText(),
            crf=crf,
            output_dir=self.dir_edit.text(),
            base_name=self.name_edit.text(),
        )

        path = self.session.start()
        self.recording = True
        self.clip_count += 1
        self.record_btn.setText("\u25a0 STOP")
        self.record_btn.setStyleSheet(
            "QPushButton { color: white; background-color: #333333; border: none; border-radius: 4px; }"
            "QPushButton:hover { background-color: #555555; }"
        )
        self.status_label.setText(f"Recording #{self.clip_count}: {os.path.basename(path)}")
        self._timer.start()

    def _stop_recording(self):
        self._timer.stop()
        if self.session:
            path, dur = self.session.stop()
            self.status_label.setText(f"Saved: {os.path.basename(path)} ({dur:.1f}s)")
        self.recording = False
        self.session = None
        self.record_btn.setText("\u25cf RECORD")
        self.record_btn.setStyleSheet(
            "QPushButton { color: white; background-color: #cc0000; border: none; border-radius: 4px; }"
            "QPushButton:hover { background-color: #ff3333; }"
        )

    def _update_timer(self):
        if not self.session:
            return
        elapsed = self.session.elapsed
        mins, secs = divmod(int(elapsed), 60)
        hrs, mins = divmod(mins, 60)
        self.status_label.setText(f"\u25cf REC #{self.clip_count}  {hrs:02d}:{mins:02d}:{secs:02d}")

    # --- Preview (unified pipeline) ---

    def _open_preview(self):
        cam = self._get_camera_index()
        if cam is None:
            self.status_label.setText("No camera selected!")
            return

        res = self.res_combo.currentText()
        w, h = 1920, 1080
        if "x" in res:
            w, h = (int(x) for x in res.split("x"))
        fps_text = self.fps_combo.currentText() or "30"
        fps = int(float(fps_text))
        preroll = float(self.preroll_spin.value())
        crf = self.crf_spin.value()

        self.status_label.setText("Opening preview...")
        QApplication.processEvents()

        p = multiprocessing.Process(
            target=_run_preview_process,
            args=(cam, w, h, fps, self.codec_combo.currentText(),
                  self.container_combo.currentText(), crf, self.dir_edit.text(),
                  self.name_edit.text(), preroll,
                  self._get_audio_index(),
                  self.preview_tc_check.isChecked(),
                  self.preview_meters_check.isChecked(),
                  False, False),
            daemon=True,
        )
        p.start()

    # --- Thermal preview ---

    def _open_thermal(self):
        cam = self._get_camera_index()
        mode = self.thermal_mode_combo.currentText()
        colormap = self.colormap_combo.currentText()
        range_lock = self._get_range_lock()

        self.status_label.setText(f"Opening thermal preview ({mode})...")
        QApplication.processEvents()

        p = multiprocessing.Process(
            target=_run_thermal_process,
            args=(mode, cam if cam is not None else 0, colormap,
                  self.dir_edit.text(), self.name_edit.text(),
                  range_lock, self._get_audio_index()),
            daemon=True,
        )
        p.start()

    # --- Multi-camera ---

    def _open_multicam(self):
        if len(self._devices["video"]) < 2:
            self.status_label.setText("Need 2+ cameras for multi-cam mode")
            return

        self.status_label.setText("Opening multi-cam preview...")
        QApplication.processEvents()

        video_devices = list(self._devices["video"])
        p = multiprocessing.Process(
            target=_run_multicam_process,
            args=(self.dir_edit.text(), self.name_edit.text(), video_devices),
            daemon=True,
        )
        p.start()


def main():
    app = QApplication(sys.argv)
    window = RecorderApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
