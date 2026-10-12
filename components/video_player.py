
import os
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from models.timeline import TimelineModel


def format_time(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours:02}:{minutes:02}:{secs:02}"
    return f"{minutes:02}:{secs:02}"


class VideoSurface(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setMinimumSize(400, 260)
        self.setStyleSheet(
            "background:#08090c; border:1px solid #343741;"
            "border-radius:6px;"
        )
        self.setToolTip("Click the preview to play or pause.")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ClipStrip(QWidget):
    seek_requested = pyqtSignal(float)
    context_requested = pyqtSignal(float, object)
    selection_changed = pyqtSignal(int)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.setMinimumHeight(64)
        self.setMaximumHeight(84)
        self.setToolTip("Click to seek. Right-click for editing options.")

    def position_from_x(self, x):
        if self.width() <= 0 or self.model.total_duration <= 0:
            return 0.0

        fraction = max(0.0, min(1.0, x / self.width()))
        return fraction * self.model.total_duration

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#17191f"))

        if not self.model.segments or self.model.total_duration <= 0:
            painter.setPen(QColor("#aab0bc"))
            painter.drawText(
                self.rect(), Qt.AlignCenter, "Add clips to begin"
            )
            painter.end()
            return

        colors = [
            "#526bb5",
            "#27866f",
            "#a46d3f",
            "#7656a8",
            "#397b9a",
        ]

        total = self.model.total_duration
        elapsed = 0.0

        for index, segment in enumerate(self.model.segments):
            left = round(elapsed / total * self.width())
            elapsed += segment.duration
            right = round(elapsed / total * self.width())
            width = max(1, right - left - 3)

            painter.setBrush(QColor(colors[index % len(colors)]))

            if index == self.model.selected_index:
                painter.setPen(QPen(QColor("#ffe082"), 2))
            else:
                painter.setPen(Qt.NoPen)

            painter.drawRoundedRect(
                left + 1, 5, width, self.height() - 17, 4, 4
            )

            if width > 35:
                painter.setPen(QColor("#ffffff"))
                label = segment.name
                max_chars = max(4, width // 8)

                if len(label) > max_chars:
                    label = label[:max_chars - 1] + "…"

                painter.drawText(
                    left + 6,
                    5,
                    width - 10,
                    self.height() - 17,
                    Qt.AlignVCenter | Qt.AlignLeft,
                    label,
                )

        playhead_x = round(
            self.model.playhead / total * self.width()
        )

        painter.setPen(QPen(QColor("#ff5252"), 2))
        painter.drawLine(playhead_x, 0, playhead_x, self.height() - 5)

        painter.setBrush(QColor("#ff5252"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(playhead_x - 4, 0, 8, 8)
        painter.end()

    def mousePressEvent(self, event):
        position = self.position_from_x(event.x())

        if event.button() == Qt.RightButton:
            self.context_requested.emit(
                position, self.mapToGlobal(event.pos())
            )
        elif event.button() == Qt.LeftButton:
            index, _ = self.model.locate(position)
            self.selection_changed.emit(index)
            self.seek_requested.emit(position)


class VideoPlayer(QWidget):
    status_changed = pyqtSignal(str)
    export_requested = pyqtSignal()
    add_clip_requested = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.model = TimelineModel()
        self.player = None
        self.vlc_instance = None
        self._vlc_directory = None
        self._active_index = -1
        self._active_path = None
        self._playing = False
        self._slider_dragging = False
        self._pending_seek = None
        self._media_paths = set()

        self._build_ui()
        self._initialize_vlc()

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Left-hand imported media library
        self.media_panel = QWidget()
        self.media_panel.setFixedWidth(235)
        self.media_panel.setObjectName("mediaPanel")

        media_layout = QVBoxLayout(self.media_panel)
        media_layout.setContentsMargins(10, 10, 10, 10)
        media_layout.setSpacing(9)

        media_title = QLabel("MEDIA LIBRARY")
        media_title.setStyleSheet(
            "font-size:14px; font-weight:bold; color:#e6e8ef;"
        )
        media_layout.addWidget(media_title)

        media_description = QLabel(
            "Imported videos appear here. Double-click a video to add "
            "another copy to the sequence."
        )
        media_description.setWordWrap(True)
        media_description.setStyleSheet("color:#9299a6; font-size:11px;")
        media_layout.addWidget(media_description)

        self.import_button = QPushButton("+ Import Video")
        self.import_button.clicked.connect(self._request_import)
        media_layout.addWidget(self.import_button)

        self.media_list = QListWidget()
        self.media_list.setObjectName("mediaList")
        self.media_list.setToolTip(
            "Select an imported video. Double-click to add it to the sequence."
        )
        self.media_list.itemDoubleClicked.connect(
            self._add_selected_media_to_sequence
        )
        media_layout.addWidget(self.media_list, 1)

        self.add_selected_button = QPushButton("Add Selected to Sequence")
        self.add_selected_button.clicked.connect(
            self._add_selected_media_to_sequence
        )
        media_layout.addWidget(self.add_selected_button)

        root.addWidget(self.media_panel)

        # Main preview and editing workspace
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(9)
        root.addWidget(workspace, 1)

        heading = QHBoxLayout()

        title = QLabel("VIDEO PREVIEW")
        title.setStyleSheet("font-size:15px; font-weight:bold;")
        heading.addWidget(title)
        heading.addStretch()

        self.add_button = QPushButton("+ Add Clip")
        self.add_button.clicked.connect(self._request_import)
        heading.addWidget(self.add_button)
        workspace_layout.addLayout(heading)

        self.video_widget = VideoSurface()
        self.video_widget.clicked.connect(self.toggle_playback)
        workspace_layout.addWidget(self.video_widget, 1)

        self.now_playing = QLabel("Use File → Add Video to start.")
        self.now_playing.setWordWrap(True)
        workspace_layout.addWidget(self.now_playing)

        seek_row = QHBoxLayout()

        self.current_time = QLabel("00:00")
        self.total_time = QLabel("00:00")

        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 10000)
        self.seek_slider.setTracking(True)
        self.seek_slider.sliderPressed.connect(self._slider_pressed)
        self.seek_slider.sliderMoved.connect(self._slider_moved)
        self.seek_slider.sliderReleased.connect(self._slider_released)

        seek_row.addWidget(self.current_time)
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.total_time)
        workspace_layout.addLayout(seek_row)

        controls = QHBoxLayout()

        self.previous_button = QPushButton("⏮ Previous")
        self.play_button = QPushButton("▶ Play")
        self.next_button = QPushButton("Next ⏭")
        self.split_button = QPushButton("✂ Split Here")
        self.remove_button = QPushButton("Remove Clip")
        self.export_button = QPushButton("Export MP4")

        self.previous_button.clicked.connect(self.previous_clip)
        self.play_button.clicked.connect(self.toggle_playback)
        self.next_button.clicked.connect(self.next_clip)
        self.split_button.clicked.connect(self.split_here)
        self.remove_button.clicked.connect(self.remove_selected)
        self.export_button.clicked.connect(self.export_requested.emit)

        for button in (
            self.previous_button,
            self.play_button,
            self.next_button,
            self.split_button,
            self.remove_button,
        ):
            controls.addWidget(button)

        controls.addStretch()
        controls.addWidget(self.export_button)
        workspace_layout.addLayout(controls)

        strip_heading = QHBoxLayout()
        strip_heading.addWidget(QLabel("CLIP SEQUENCE"))
        strip_heading.addStretch()
        strip_heading.addWidget(QLabel("Right-click for options"))
        workspace_layout.addLayout(strip_heading)

        self.strip = ClipStrip(self.model)
        self.strip.seek_requested.connect(self.seek_to)
        self.strip.context_requested.connect(self._show_context_menu)
        self.strip.selection_changed.connect(self._select_index)
        workspace_layout.addWidget(self.strip)

        self.sequence_status = QLabel("0 clips · 00:00 total")
        self.sequence_status.setStyleSheet("color:#9299a6;")
        workspace_layout.addWidget(self.sequence_status)

    def _request_import(self):
        self.add_clip_requested.emit(float(self.model.playhead))

    def _initialize_vlc(self):
        try:
            candidates = [
                Path(r"C:\Program Files\VideoLAN\VLC"),
                Path(r"C:\Program Files (x86)\VideoLAN\VLC"),
            ]

            for folder in candidates:
                if (folder / "libvlc.dll").exists():
                    self._vlc_directory = os.add_dll_directory(str(folder))
                    os.environ["VLC_PLUGIN_PATH"] = str(folder / "plugins")
                    break

            import vlc

            self.vlc_instance = vlc.Instance(
                "--no-video-title-show",
                "--avcodec-hw=none",
                "--no-metadata-network-access",
            )

            if self.vlc_instance is None:
                raise RuntimeError("VLC could not create an instance.")

            self.player = self.vlc_instance.media_player_new()
            self.player.set_hwnd(int(self.video_widget.winId()))

        except Exception as exc:
            self.player = None
            QMessageBox.critical(
                self,
                "VLC Playback Error",
                "Could not initialize VLC playback.\n\n"
                f"{exc}\n\n"
                "Check that 64-bit VLC is installed and python-vlc "
                "is installed in this virtual environment.",
            )

    def _add_to_media_library(self, path):
        path = Path(path).resolve()
        key = str(path).lower()

        if key in self._media_paths:
            for index in range(self.media_list.count()):
                item = self.media_list.item(index)
                if str(item.data(Qt.UserRole)).lower() == key:
                    self.media_list.setCurrentItem(item)
                    break
            return

        item = QListWidgetItem(path.name)
        item.setData(Qt.UserRole, str(path))
        item.setToolTip(str(path))
        self.media_list.addItem(item)
        self.media_list.setCurrentItem(item)
        self._media_paths.add(key)

    def _add_selected_media_to_sequence(self, item=None):
        if item is None:
            item = self.media_list.currentItem()

        if item is None:
            self.status_changed.emit("Select a video in the Media Library first.")
            return

        path = item.data(Qt.UserRole)

        try:
            self.add_video(path, self.model.playhead)
        except Exception as exc:
            QMessageBox.critical(self, "Could Not Add Clip", str(exc))

    def add_video(self, path, position=None):
        from services.ffmpeg_service import get_video_info

        path = Path(path).resolve()
        info = get_video_info(path)

        self._add_to_media_library(path)

        self.model.add_video(
            path,
            0.0,
            info["duration_seconds"],
            position=position,
        )

        self._refresh()
        self.seek_to(self.model.playhead)
        self.status_changed.emit(f"Added {path.name}")

    def _refresh(self):
        self.strip.update()

        self.current_time.setText(format_time(self.model.playhead))
        self.total_time.setText(format_time(self.model.total_duration))

        self.sequence_status.setText(
            f"{len(self.model.segments)} clips · "
            f"{format_time(self.model.total_duration)} total"
        )

        if 0 <= self.model.selected_index < len(self.model.segments):
            segment = self.model.segments[self.model.selected_index]
            self.now_playing.setText(
                f"Selected: {segment.name} "
                f"({format_time(segment.duration)})"
            )
        elif not self.model.segments:
            self.now_playing.setText("Import a video to begin.")

    def _select_index(self, index):
        if 0 <= index < len(self.model.segments):
            self.model.selected_index = index
            self._refresh()

    def _load_clip(self, index, source_time=None, autoplay=False):
        if self.player is None:
            self.status_changed.emit("VLC playback is unavailable.")
            return

        if not 0 <= index < len(self.model.segments):
            return

        segment = self.model.segments[index]

        self._active_index = index
        self._active_path = segment.path
        self._pending_seek = (
            segment.source_start if source_time is None else source_time
        )

        media = self.vlc_instance.media_new(str(segment.path))
        self.player.set_media(media)
        self.player.play()

        self._playing = autoplay
        self.play_button.setText("⏸ Pause" if autoplay else "▶ Play")

        QTimer.singleShot(500, self._finish_pending_seek)

    def _finish_pending_seek(self):
        if self.player is None or self._pending_seek is None:
            return

        if self._active_index < 0:
            return

        seek_time = self._pending_seek
        self._pending_seek = None

        self.player.set_time(max(0, int(seek_time * 1000)))

        if self._playing:
            self.player.play()
        else:
            self.player.pause()

    def seek_to(self, position):
        if not self.model.segments:
            return

        position = max(
            0.0, min(float(position), self.model.total_duration)
        )

        index, source_time = self.model.locate(position)

        if index < 0:
            return

        was_playing = self._playing
        self.model.playhead = position
        self.model.selected_index = index

        segment = self.model.segments[index]

        if (
            index != self._active_index
            or self._active_path != segment.path
        ):
            self._load_clip(index, source_time, was_playing)
        elif self.player is not None:
            self.player.set_time(int(source_time * 1000))

            if was_playing:
                self.player.play()
            else:
                self.player.pause()

        self._refresh()

    def toggle_playback(self):
        if not self.model.segments:
            self.status_changed.emit("Add a video before playing.")
            return

        if self.player is None:
            self.status_changed.emit("VLC playback is unavailable.")
            return

        if self._playing:
            self.player.pause()
            self._playing = False
            self.play_button.setText("▶ Play")
        else:
            index, source_time = self.model.locate(self.model.playhead)

            if index < 0:
                return

            segment = self.model.segments[index]

            if (
                index != self._active_index
                or self._active_path != segment.path
            ):
                self._load_clip(index, source_time, True)
            else:
                self.player.play()

            self._playing = True
            self.play_button.setText("⏸ Pause")

    def _tick(self):
        if (
            self._playing
            and self.player is not None
            and self._active_index >= 0
            and self._pending_seek is None
        ):
            source_ms = self.player.get_time()

            if source_ms >= 0 and self._active_index < len(self.model.segments):
                segment = self.model.segments[self._active_index]
                source_time = source_ms / 1000.0

                self.model.playhead = (
                    self.model.segment_start(self._active_index)
                    + max(0.0, source_time - segment.source_start)
                )

                if source_time >= segment.source_end - 0.10:
                    next_index = self._active_index + 1

                    if next_index < len(self.model.segments):
                        self.model.playhead = self.model.segment_start(
                            next_index
                        )
                        next_segment = self.model.segments[next_index]
                        self._load_clip(
                            next_index,
                            next_segment.source_start,
                            True,
                        )
                    else:
                        self.model.playhead = self.model.total_duration
                        self.player.pause()
                        self._playing = False
                        self.play_button.setText("▶ Play")

        if not self._slider_dragging:
            total = self.model.total_duration
            value = round(self.model.playhead / total * 10000) if total else 0

            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(value)
            self.seek_slider.blockSignals(False)

        self._refresh()

    def _slider_pressed(self):
        self._slider_dragging = True

    def _slider_moved(self, value):
        total = self.model.total_duration
        position = total * value / 10000 if total else 0.0
        self.current_time.setText(format_time(position))

    def _slider_released(self):
        total = self.model.total_duration
        position = total * self.seek_slider.value() / 10000
        self._slider_dragging = False
        self.seek_to(position)

    def previous_clip(self):
        if not self.model.segments:
            return

        index = max(0, self.model.selected_index - 1)
        self.seek_to(self.model.segment_start(index))

    def next_clip(self):
        if not self.model.segments:
            return

        index = min(
            len(self.model.segments) - 1,
            max(0, self.model.selected_index + 1),
        )
        self.seek_to(self.model.segment_start(index))

    def split_here(self):
        if self.model.split_at(self.model.playhead):
            self._refresh()
            self.status_changed.emit("Clip split at the current position.")
        else:
            self.status_changed.emit(
                "Move the playhead inside a clip before splitting."
            )

    def remove_selected(self):
        if not self.model.remove_selected():
            self.status_changed.emit("Select a clip to remove.")
            return

        self._active_index = -1
        self._active_path = None

        if self.player is not None:
            self.player.stop()

        self._playing = False
        self.play_button.setText("▶ Play")
        self._refresh()

        if self.model.segments:
            self.seek_to(self.model.playhead)
        else:
            self.now_playing.setText("Import a video to begin.")

        self.status_changed.emit("Clip removed.")

    def _show_context_menu(self, position, global_pos=None):
        menu = QMenu(self)

        add_action = menu.addAction("Add Video Clip…")
        menu.addSeparator()
        split_action = menu.addAction("Split at This Position")
        remove_action = menu.addAction("Remove Selected Clip")
        menu.addSeparator()
        play_action = menu.addAction("Play / Pause")

        if global_pos is None:
            global_pos = self.mapToGlobal(self.rect().center())

        chosen = menu.exec_(global_pos)

        if chosen == add_action:
            self.add_clip_requested.emit(float(position))
        elif chosen == split_action:
            self.seek_to(position)
            self.split_here()
        elif chosen == remove_action:
            self.remove_selected()
        elif chosen == play_action:
            self.toggle_playback()

    def contextMenuEvent(self, event):
        self._show_context_menu(
            self.model.playhead, event.globalPos()
        )

    def export_sequence(self, output_path, progress_callback=None):
        from services.video_service import export_timeline

        return export_timeline(
            self.model.segments,
            output_path,
            progress_callback,
        )

    def stop(self):
        self.timer.stop()

        if self.player is not None:
            self.player.stop()
