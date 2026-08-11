"""Base classes for image/video source providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Album:
    remote_id: str
    name: str
    cover_url: str | None = None
    media_count: int = 0


@dataclass
class SyncResult:
    added: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)


class ImageSource(ABC):
    """Abstract base for photo/video source providers."""

    source_type: str  # set as class attribute in subclasses

    @abstractmethod
    def authenticate(self, credentials: dict) -> bool:
        """Validate and load credentials. Return True if successful."""
        ...

    @abstractmethod
    def list_albums(self) -> list[Album]:
        """Return all albums available from this source."""
        ...

    @abstractmethod
    def sync_album(
        self,
        remote_id: str,
        local_path: Path,
        existing_files: set[str],
    ) -> SyncResult:
        """
        Sync a remote album to local_path.
        existing_files: filenames (not paths) already present locally.
        Returns counts of added/removed files and any error messages.
        """
        ...

    @property
    def is_authenticated(self) -> bool:
        """Override to report live auth state."""
        return False
