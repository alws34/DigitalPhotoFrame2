"""Local folder source — discovers subfolders of Images/local_images/ as albums."""
from __future__ import annotations

from pathlib import Path

from .base import Album, ImageSource, SyncResult

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov"}


class LocalFolderSource(ImageSource):
    source_type = "local"

    def __init__(self, local_images_root: Path) -> None:
        self._root = Path(local_images_root)

    def authenticate(self, credentials: dict) -> bool:
        return True  # no auth for local

    @property
    def is_authenticated(self) -> bool:
        return True

    def list_albums(self) -> list[Album]:
        albums: list[Album] = []
        if not self._root.exists():
            return albums
        for entry in sorted(self._root.iterdir()):
            if entry.is_dir():
                count = sum(
                    1
                    for f in entry.iterdir()
                    if f.suffix.lower() in SUPPORTED_EXTENSIONS
                )
                albums.append(
                    Album(remote_id=entry.name, name=entry.name, media_count=count)
                )
        return albums

    def sync_album(
        self, remote_id: str, local_path: Path, existing_files: set[str]
    ) -> SyncResult:
        # Local source: files are already on disk, nothing to sync
        return SyncResult()
