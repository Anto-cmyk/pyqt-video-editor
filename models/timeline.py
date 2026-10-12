
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TimelineSegment:
    path: Path
    source_start: float
    source_end: float

    @property
    def duration(self):
        return max(0.0, self.source_end - self.source_start)

    @property
    def name(self):
        return self.path.name


class TimelineModel:
    def __init__(self):
        self.segments = []
        self.playhead = 0.0
        self.selected_index = -1

    @property
    def total_duration(self):
        return sum(s.duration for s in self.segments)

    def clear(self):
        self.segments.clear()
        self.playhead = 0.0
        self.selected_index = -1

    def locate(self, position):
        if not self.segments:
            return -1, 0.0

        position = max(0.0, min(float(position), self.total_duration))
        elapsed = 0.0

        for index, segment in enumerate(self.segments):
            end = elapsed + segment.duration

            if position < end or index == len(self.segments) - 1:
                local = min(segment.duration, max(0.0, position - elapsed))
                return index, segment.source_start + local

            elapsed = end

        return -1, 0.0

    def segment_start(self, index):
        return sum(s.duration for s in self.segments[:index])

    def add_video(self, path, source_start=0.0, source_end=None, position=None):
        from services.ffmpeg_service import get_video_info

        path = Path(path).resolve()

        if not path.is_file():
            raise FileNotFoundError(f"Video not found: {path}")

        if source_end is None:
            source_end = get_video_info(path)["duration_seconds"]

        source_start = float(source_start)
        source_end = float(source_end)

        if source_start < 0 or source_end <= source_start:
            raise ValueError("The clip end must be after its start.")

        new_segment = TimelineSegment(path, source_start, source_end)

        if position is None or not self.segments:
            self.segments.append(new_segment)
            index = len(self.segments) - 1
        else:
            position = max(0.0, min(float(position), self.total_duration))
            elapsed = 0.0
            index = len(self.segments)

            for i, segment in enumerate(self.segments):
                end = elapsed + segment.duration

                if position <= elapsed:
                    index = i
                    break

                if elapsed < position < end:
                    source_split = segment.source_start + position - elapsed
                    left = TimelineSegment(
                        segment.path, segment.source_start, source_split
                    )
                    right = TimelineSegment(
                        segment.path, source_split, segment.source_end
                    )

                    self.segments[i:i + 1] = [left, new_segment, right]
                    index = i + 1
                    break

                elapsed = end
            else:
                index = len(self.segments)

            if index == len(self.segments):
                self.segments.append(new_segment)
            elif self.segments[index] is not new_segment:
                self.segments.insert(index, new_segment)

        self.selected_index = index
        self.playhead = self.segment_start(index)
        return new_segment

    def split_at(self, position):
        index, source_time = self.locate(position)

        if index < 0:
            return False

        segment = self.segments[index]

        if (
            source_time <= segment.source_start + 0.05
            or source_time >= segment.source_end - 0.05
        ):
            return False

        left = TimelineSegment(segment.path, segment.source_start, source_time)
        right = TimelineSegment(segment.path, source_time, segment.source_end)
        self.segments[index:index + 1] = [left, right]
        self.selected_index = index + 1
        self.playhead = float(position)
        return True

    def remove_selected(self):
        i = self.selected_index

        if not 0 <= i < len(self.segments):
            return False

        start = self.segment_start(i)
        self.segments.pop(i)
        self.playhead = min(start, self.total_duration)

        if not self.segments:
            self.selected_index = -1
        else:
            self.selected_index = min(i, len(self.segments) - 1)

        return True
