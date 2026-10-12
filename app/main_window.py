import os
import tempfile
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from app.styles import APP_STYLE
from components.mark_overlay import MarkOverlay
from components.video_player import VideoPlayer
from services.video_service import (
    QUALITY,
    RESOLUTIONS,
    mark_windows,
    render_preview_frame,
    set_export_settings,
    set_watermark_settings,
)

CORNERS = ["Top-left", "Top-right", "Bottom-left", "Bottom-right"]
TIKTOK = "TikTok moving logo (2 positions)"
SINGLE = "Single fixed region"


def _region(corner, width, height, mx, my, start=0.0, end=1.0):
    x = mx if corner.endswith("left") else 100 - width - mx
    y = my if corner.startswith("Top") else 100 - height - my
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "start": start,
        "end": end,
    }


def _select(combo, value):
    index = combo.findText(value)
    if index >= 0:
        combo.setCurrentIndex(index)


# ----------------------------------------------------------------------
# Watermark dialog (corner-based setup)
# ----------------------------------------------------------------------

class WatermarkDialog(QDialog):
    """Configure corner-based watermark removal."""

    def __init__(self, settings=None, preview_callback=None, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Watermark Remover")
        self.setMinimumWidth(460)

        self.preview_callback = preview_callback
        s = settings or {}

        layout = QVBoxLayout(self)

        info = QLabel(
            "This dialog sets the logo by corner. For exact placement, "
            "close it and use Effects → Mark Watermarks on Preview (M): "
            "click the logo right on the video.\n\n"
            "Use Cover to test positions: a black box that misses the "
            "logo means the box needs adjusting."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        layout.addLayout(form)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(
            ["Cover", "Blur", "Inpaint", "Pixelate", "None"]
        )
        _select(self.mode_combo, s.get("mode", "Cover"))
        form.addRow("Effect:", self.mode_combo)

        self.preset_combo = QComboBox()
        self.preset_combo.addItems([TIKTOK, SINGLE])
        _select(self.preset_combo, s.get("preset", TIKTOK))
        form.addRow("Preset:", self.preset_combo)

        self.corner1_combo = QComboBox()
        self.corner1_combo.addItems(CORNERS)
        _select(self.corner1_combo, s.get("corner", "Top-left"))
        form.addRow("Logo position 1:", self.corner1_combo)

        self.corner2_combo = QComboBox()
        self.corner2_combo.addItems(CORNERS)
        _select(self.corner2_combo, s.get("corner2", "Bottom-right"))
        form.addRow("Logo position 2:", self.corner2_combo)

        self.split_spin = self._make_spin(s.get("split", 50), 5, 95)
        form.addRow("Switches to position 2 at:", self.split_spin)

        self.width_spin = self._make_spin(s.get("width", 30), 1, 100)
        form.addRow("Region width:", self.width_spin)

        self.height_spin = self._make_spin(s.get("height", 8), 1, 100)
        form.addRow("Region height:", self.height_spin)

        self.margin_x_spin = self._make_spin(s.get("margin_x", 2), 0, 49)
        form.addRow("Horizontal margin:", self.margin_x_spin)

        self.margin_y_spin = self._make_spin(s.get("margin_y", 6), 0, 49)
        form.addRow("Vertical margin:", self.margin_y_spin)

        self.trim_spin = QDoubleSpinBox()
        self.trim_spin.setRange(0, 10)
        self.trim_spin.setSingleStep(0.5)
        self.trim_spin.setSuffix(" s")
        self.trim_spin.setValue(float(s.get("trim_end", 0)))
        form.addRow("Trim end card:", self.trim_spin)

        # Create the preview button BEFORE anything calls _update_fields()
        self.preview_button = QPushButton("Preview Frame at Playhead")
        self.preview_button.clicked.connect(self._preview)
        self.preview_button.setVisible(preview_callback is not None)
        layout.addWidget(self.preview_button)

        self.mode_combo.currentTextChanged.connect(self._update_fields)
        self.preset_combo.currentTextChanged.connect(self._update_fields)
        self._update_fields()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _make_spin(value, minimum, maximum):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(int(value))
        spin.setSuffix("%")
        return spin

    def _update_fields(self):
        enabled = self.mode_combo.currentText() != "None"
        tiktok = self.preset_combo.currentText() == TIKTOK

        for control in (
            self.preset_combo,
            self.corner1_combo,
            self.width_spin,
            self.height_spin,
            self.margin_x_spin,
            self.margin_y_spin,
            self.trim_spin,
            self.preview_button,
        ):
            control.setEnabled(enabled)

        self.corner2_combo.setEnabled(enabled and tiktok)
        self.split_spin.setEnabled(enabled and tiktok)

    def _preview(self):
        if self.preview_callback:
            self.preview_callback(self.get_settings())

    def get_settings(self):
        mode = self.mode_combo.currentText()

        if mode == "None":
            return None

        w = self.width_spin.value()
        h = self.height_spin.value()
        mx = self.margin_x_spin.value()
        my = self.margin_y_spin.value()
        c1 = self.corner1_combo.currentText()
        c2 = self.corner2_combo.currentText()
        split = self.split_spin.value() / 100

        if self.preset_combo.currentText() == TIKTOK:
            regions = [
                _region(c1, w, h, mx, my, 0.0, split),
                _region(c2, w, h, mx, my, split, 1.0),
            ]
        else:
            regions = [_region(c1, w, h, mx, my)]

        return {
            "mode": mode,
            "preset": self.preset_combo.currentText(),
            "corner": c1,
            "corner2": c2,
            "split": self.split_spin.value(),
            "width": w,
            "height": h,
            "margin_x": mx,
            "margin_y": my,
            "trim_end": self.trim_spin.value(),
            "regions": regions,
        }


# ----------------------------------------------------------------------
# Export + preview dialogs
# ----------------------------------------------------------------------

class ExportDialog(QDialog):
    """Resolution, quality, frame rate and audio options."""

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Export Settings")
        self.setMinimumWidth(360)

        s = settings or {}

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(list(RESOLUTIONS))
        _select(
            self.resolution_combo,
            s.get("resolution", "720 × 1280 (Portrait)"),
        )
        form.addRow("Resolution:", self.resolution_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(QUALITY))
        _select(self.quality_combo, s.get("quality", "Fast (larger file)"))
        form.addRow("Quality:", self.quality_combo)

        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["24", "30", "60"])
        _select(self.fps_combo, str(s.get("fps", 30)))
        form.addRow("Frame rate:", self.fps_combo)

        self.mute_check = QCheckBox("Remove audio")
        self.mute_check.setChecked(bool(s.get("mute", False)))
        form.addRow("", self.mute_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self):
        w, h = RESOLUTIONS[self.resolution_combo.currentText()]
        preset, crf = QUALITY[self.quality_combo.currentText()]

        return {
            "resolution": self.resolution_combo.currentText(),
            "quality": self.quality_combo.currentText(),
            "width": w,
            "height": h,
            "fps": int(self.fps_combo.currentText()),
            "preset": preset,
            "crf": crf,
            "mute": self.mute_check.isChecked(),
        }


class PreviewDialog(QDialog):
    """Shows one rendered frame with the effect applied."""

    def __init__(self, pixmap, caption, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Effect Preview")

        layout = QVBoxLayout(self)

        image = QLabel()
        image.setAlignment(Qt.AlignCenter)
        image.setPixmap(
            pixmap.scaled(
                520, 760, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )
        layout.addWidget(image)

        text = QLabel(caption)
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignCenter)
        layout.addWidget(text)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)


# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Antony Video Editor")
        self.resize(1400, 850)
        self.setMinimumSize(1000, 650)
        self.setStyleSheet(APP_STYLE)

        self.watermark_settings = None   # from the Watermark Remover dialog
        self.export_settings = None
        self.marks = []                  # clicked on the preview
        self.mark_effect = "Cover"
        set_watermark_settings(None)

        self.video_player = VideoPlayer(self)
        self.setCentralWidget(self.video_player)

        self.mark_overlay = MarkOverlay(
            self.video_player.video_widget,
            self._active_marks,
            self._player_video_size,
            self,
        )
        self.mark_overlay.add_requested.connect(self._add_mark)
        self.mark_overlay.remove_requested.connect(self._remove_mark)
        self.mark_overlay.resize_requested.connect(self._resize_mark)

        self.statusBar().showMessage("Ready")

        self.video_player.status_changed.connect(
            self.statusBar().showMessage
        )
        self.video_player.export_requested.connect(self.export_video)
        self.video_player.add_clip_requested.connect(
            self.add_video_dialog
        )

        self._create_menus()

    def _create_menus(self):
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")

        add_action = QAction("Import Video…", self)
        add_action.setShortcut("Ctrl+O")
        add_action.triggered.connect(
            lambda: self.add_video_dialog(
                self.video_player.model.playhead
            )
        )
        file_menu.addAction(add_action)

        export_action = QAction("Export MP4…", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_video)
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Alt+F4")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = bar.addMenu("&Edit")

        split_action = QAction("Split at Playhead", self)
        split_action.setShortcut("S")
        split_action.triggered.connect(self.video_player.split_here)
        edit_menu.addAction(split_action)

        remove_action = QAction("Remove Selected Clip", self)
        remove_action.setShortcut("Delete")
        remove_action.triggered.connect(self.video_player.remove_selected)
        edit_menu.addAction(remove_action)

        playback_menu = bar.addMenu("&Playback")

        play_action = QAction("Play / Pause", self)
        play_action.setShortcut("Space")
        play_action.triggered.connect(self.video_player.toggle_playback)
        playback_menu.addAction(play_action)

        playback_menu.addAction(
            "Previous Clip", self.video_player.previous_clip
        )
        playback_menu.addAction("Next Clip", self.video_player.next_clip)

        clip_menu = bar.addMenu("&Clip")

        clip_menu.addAction(
            "Add Clip at Playhead",
            lambda: self.add_video_dialog(
                self.video_player.model.playhead
            ),
        )
        clip_menu.addAction(
            "Split at Playhead", self.video_player.split_here
        )
        clip_menu.addAction(
            "Remove Selected", self.video_player.remove_selected
        )

        effects_menu = bar.addMenu("&Effects")

        self.mark_action = QAction("Mark Watermarks on Preview", self)
        self.mark_action.setCheckable(True)
        self.mark_action.setShortcut("M")
        # The overlay is its own window, so the shortcut must work app-wide.
        self.mark_action.setShortcutContext(Qt.ApplicationShortcut)
        self.mark_action.toggled.connect(self.toggle_mark_mode)
        effects_menu.addAction(self.mark_action)

        marks_menu = effects_menu.addMenu("Effect for Marks")
        group = QActionGroup(self)

        for name in ("Cover", "Blur", "Pixelate", "Inpaint"):
            action = QAction(name, self)
            action.setCheckable(True)
            action.setChecked(name == self.mark_effect)
            action.triggered.connect(
                lambda _checked, n=name: self._set_mark_effect(n)
            )
            group.addAction(action)
            marks_menu.addAction(action)

        clear_marks_action = QAction("Clear All Marks", self)
        clear_marks_action.triggered.connect(self.clear_marks)
        effects_menu.addAction(clear_marks_action)

        effects_menu.addSeparator()

        watermark_action = QAction("Watermark Remover (Corners)…", self)
        watermark_action.triggered.connect(self.open_watermark_remover)
        effects_menu.addAction(watermark_action)

        preview_action = QAction("Preview Effect at Playhead", self)
        preview_action.setShortcut("Ctrl+P")
        preview_action.triggered.connect(self.preview_effect)
        effects_menu.addAction(preview_action)

        export_settings_action = QAction("Export Settings…", self)
        export_settings_action.triggered.connect(self.open_export_settings)
        effects_menu.addAction(export_settings_action)

        effects_menu.addSeparator()

        reset_action = QAction("Reset Watermark Effect", self)
        reset_action.triggered.connect(self.reset_watermark_effect)
        effects_menu.addAction(reset_action)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction("About", self.show_about)

    # ------------------------------------------------------------------
    # Marks (clicked on the preview)
    # ------------------------------------------------------------------

    def _player_video_size(self):
        player = self.video_player.player

        if player is None:
            return (0, 0)

        try:
            size = player.video_get_size(0)
            return int(size[0]), int(size[1])
        except Exception:
            return (0, 0)

    def _active_marks(self):
        """Marks that apply at the playhead, as (index, mark) pairs."""
        position = self.video_player.model.playhead

        for group in mark_windows(self.marks):
            a, b = group["t0"], group["t1"]

            if (a is None or position >= a) and (b is None or position < b):
                return group["items"]

        return []

    def _effective_settings(self):
        """Dialog settings + clicked marks, in the form the service wants."""
        if self.watermark_settings:
            settings = dict(self.watermark_settings)
        elif self.marks:
            settings = {
                "mode": self.mark_effect,
                "trim_end": 0,
                "regions": [],
            }
        else:
            return None

        settings["marks"] = list(self.marks)
        return settings

    def _marks_changed(self):
        set_watermark_settings(self._effective_settings())
        self.mark_overlay.refresh()

        count = len(self.marks)

        if count:
            self.statusBar().showMessage(
                f"{count} watermark mark{'s' if count != 1 else ''}. "
                "Export to apply."
            )
        else:
            self.statusBar().showMessage("No watermark marks.")

    def _add_mark(self, x, y, w, h):
        self.marks.append({
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "t": float(self.video_player.model.playhead),
        })
        self._marks_changed()

    def _remove_mark(self, index):
        if 0 <= index < len(self.marks):
            del self.marks[index]
            self._marks_changed()

    def _resize_mark(self, index, factor):
        if not 0 <= index < len(self.marks):
            return

        m = self.marks[index]
        cx = m["x"] + m["w"] / 2
        cy = m["y"] + m["h"] / 2

        m["w"] = max(1.5, min(100.0, m["w"] * factor))
        m["h"] = max(1.0, min(100.0, m["h"] * factor))
        m["x"] = max(0.0, min(100.0 - m["w"], cx - m["w"] / 2))
        m["y"] = max(0.0, min(100.0 - m["h"], cy - m["h"] / 2))

        self._marks_changed()

    def _set_mark_effect(self, name):
        self.mark_effect = name

        if self.marks:
            self._marks_changed()

    def clear_marks(self):
        self.marks = []
        self._marks_changed()

    def toggle_mark_mode(self, enabled):
        if not enabled:
            self.mark_overlay.hide()
            return

        if not self.video_player.model.segments:
            QMessageBox.information(
                self,
                "Nothing to Mark",
                "Import a video first.",
            )
            self.mark_action.setChecked(False)
            return

        if QApplication.activeModalWidget() is not None:
            self.mark_action.setChecked(False)
            return

        if self.video_player._playing:
            self.video_player.toggle_playback()

        self.mark_overlay.show_over_target()
        self.statusBar().showMessage(
            "Marking: click the logo. Scrub and click again if it moves. "
            "Right-click a box to remove it. Press M to finish."
        )

    def _leave_mark_mode(self):
        if self.mark_action.isChecked():
            self.mark_action.setChecked(False)

    # ------------------------------------------------------------------
    # Effects
    # ------------------------------------------------------------------

    def open_watermark_remover(self):
        self._leave_mark_mode()

        dialog = WatermarkDialog(
            settings=self.watermark_settings,
            preview_callback=self.preview_effect,
            parent=self,
        )

        if dialog.exec_() != QDialog.Accepted:
            return

        self.watermark_settings = dialog.get_settings()
        set_watermark_settings(self._effective_settings())

        if self.watermark_settings:
            s = self.watermark_settings
            where = "2 positions" if s.get("preset") == TIKTOK else "1 position"

            self.statusBar().showMessage(
                f"{s['mode']} selected ({where}). Export to apply."
            )
        else:
            self.statusBar().showMessage("Corner effect disabled.")

    def open_export_settings(self):
        self._leave_mark_mode()

        dialog = ExportDialog(self.export_settings, self)

        if dialog.exec_() != QDialog.Accepted:
            return

        self.export_settings = dialog.get_settings()
        set_export_settings(self.export_settings)

        self.statusBar().showMessage(
            f"Export: {self.export_settings['resolution']}, "
            f"{self.export_settings['quality']}."
        )

    def reset_watermark_effect(self):
        self.watermark_settings = None
        self.marks = []
        set_watermark_settings(None)
        self.mark_overlay.refresh()
        self.statusBar().showMessage("Watermark effect reset.")

    def _render_pixmap(self, position, apply_effect):
        """Render one frame at a timeline position and return a QPixmap."""
        model = self.video_player.model

        position = max(
            0.0, min(float(position), max(0.0, model.total_duration - 0.05))
        )

        index, source_time = model.locate(position)

        if index < 0:
            return None

        segment = model.segments[index]

        handle, temp_path = tempfile.mkstemp(suffix=".png")
        os.close(handle)

        try:
            if self.export_settings:
                set_export_settings(self.export_settings)

            render_preview_frame(
                segment,
                source_time,
                temp_path,
                apply_effect=apply_effect,
                timeline_offset=model.segment_start(index),
            )

            pixmap = QPixmap(temp_path)

            if pixmap.isNull():
                raise RuntimeError("The frame could not be loaded.")

            return pixmap

        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def preview_effect(self, settings=None):
        """Render the frame at the playhead with the effect applied.

        `settings` comes from the dialog (unsaved changes); from the menu,
        the applied settings and marks are used instead.
        """
        self._leave_mark_mode()

        model = self.video_player.model

        if not model.segments:
            QMessageBox.information(
                self,
                "Nothing to Preview",
                "Import a video and place the playhead on a frame first.",
            )
            return

        # The menu action passes a bool (checked state), not settings.
        if isinstance(settings, dict):
            settings = dict(settings)
            settings["marks"] = list(self.marks)
        else:
            settings = self._effective_settings()

        if not settings:
            QMessageBox.information(
                self,
                "No Effect Selected",
                "Mark a watermark on the preview (M) or set up the corner "
                "remover first.",
            )
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)

        try:
            set_watermark_settings(settings)
            pixmap = self._render_pixmap(model.playhead, apply_effect=True)

            if pixmap is None:
                return

        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Preview Failed", str(exc))
            return

        finally:
            # Put the applied settings back, whatever the dialog held.
            set_watermark_settings(self._effective_settings())

            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()

        PreviewDialog(
            pixmap,
            f"Frame at {model.playhead:.1f}s with the effect applied. "
            "Move the playhead to check other moments.",
            self,
        ).exec_()

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------

    def add_video_dialog(self, position=None):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Video",
            str(Path.home()),
            (
                "Video files (*.mp4 *.mov *.mkv *.avi *.webm *.m4v);;"
                "All files (*.*)"
            ),
        )

        if not path:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)

        try:
            self.video_player.add_video(path, position)
            self.statusBar().showMessage(f"Imported: {Path(path).name}")
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Could Not Import Video",
                str(exc),
            )
        finally:
            QApplication.restoreOverrideCursor()

    def export_video(self):
        self._leave_mark_mode()

        if not self.video_player.model.segments:
            QMessageBox.information(
                self,
                "Nothing to Export",
                "Import at least one video before exporting.",
            )
            return

        if self._effective_settings() is None:
            answer = QMessageBox.question(
                self,
                "No Watermark Effect",
                "No watermark effect is applied, so the logo will stay "
                "in the export.\n\nExport anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if answer != QMessageBox.Yes:
                return

        output, _ = QFileDialog.getSaveFileName(
            self,
            "Export Video",
            str(Path.home() / "antony_video.mp4"),
            "MP4 video (*.mp4)",
        )

        if not output:
            return

        if not output.lower().endswith(".mp4"):
            output += ".mp4"

        if Path(output).exists():
            answer = QMessageBox.question(
                self,
                "Replace Existing File?",
                "This file already exists. Replace it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if answer != QMessageBox.Yes:
                return

        progress = QProgressDialog(
            "Preparing export…",
            "Cancel",
            0,
            100,
            self,
        )
        progress.setWindowTitle("Exporting MP4")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)
        progress.setValue(0)

        def update_progress(value, message):
            progress.setLabelText(message)
            progress.setValue(value)
            QApplication.processEvents()

        was_playing = self.video_player._playing

        if self.video_player.player is not None:
            self.video_player.player.pause()

        self.video_player._playing = False
        self.video_player.play_button.setText("▶ Play")

        QApplication.setOverrideCursor(Qt.WaitCursor)

        try:
            set_watermark_settings(self._effective_settings())

            if self.export_settings:
                set_export_settings(self.export_settings)

            self.video_player.export_sequence(output, update_progress)

            progress.setValue(100)

            QMessageBox.information(
                self,
                "Export Complete",
                f"Your MP4 was saved to:\n{output}",
            )

            self.statusBar().showMessage(f"Exported: {output}")

        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

        finally:
            progress.close()
            QApplication.restoreOverrideCursor()

            if was_playing:
                self.video_player.toggle_playback()

    def show_about(self):
        QMessageBox.about(
            self,
            "About Antony Video Editor",
            "Antony Video Editor\n\n"
            "A desktop video editor built with Python, PyQt5, VLC, "
            "and FFmpeg.",
        )

    def closeEvent(self, event):
        self.mark_overlay.close()
        self.video_player.stop()
        event.accept()