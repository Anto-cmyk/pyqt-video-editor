
"""Data model representing a video clip in the editing project."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class VideoClip:
    """Immutable information about an imported video clip."""

    path: Path

    @property
    def name(self) -> str:
        """Return the video's filename."""
        return self.path.name

    @property
    def extension(self) -> str:
        """Return the file extension in lowercase."""
        return self.path.suffix.lower()
