"""Google Photos source — OAuth2 + Photos Library API v1."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import requests

from .base import Album, ImageSource, SyncResult

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mov"}
PHOTOS_API = "https://photoslibrary.googleapis.com/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPES = "https://www.googleapis.com/auth/photoslibrary.readonly"


class GooglePhotosSource(ImageSource):
    source_type = "google_photos"

    def __init__(self) -> None:
        self._client_id: str = ""
        self._client_secret: str = ""
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    @classmethod
    def get_auth_url(cls, client_id: str, redirect_uri: str) -> str:
        """Return the Google OAuth2 authorization URL to redirect the user to."""
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "access_type": "offline",
            "prompt": "consent",
        }
        query = "&".join(
            f"{k}={requests.utils.quote(str(v))}" for k, v in params.items()
        )
        return f"{AUTH_URL}?{query}"

    @classmethod
    def exchange_code(
        cls, client_id: str, client_secret: str, code: str, redirect_uri: str
    ) -> dict:
        """Exchange authorization code for tokens. Returns token dict."""
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def authenticate(self, credentials: dict) -> bool:
        """Load credentials dict (from DB decrypt). Returns True if tokens present."""
        self._client_id = credentials.get("client_id", "")
        self._client_secret = credentials.get("client_secret", "")
        self._access_token = credentials.get("access_token", "")
        self._refresh_token = credentials.get("refresh_token", "")
        self._token_expiry = credentials.get("token_expiry", 0.0)
        return bool(self._refresh_token or self._access_token)

    @property
    def is_authenticated(self) -> bool:
        return bool(self._refresh_token or self._access_token)

    def get_credentials(self) -> dict:
        """Return current credentials dict for re-encryption and storage."""
        return {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "token_expiry": self._token_expiry,
        }

    def _ensure_valid_token(self) -> None:
        if time.time() < self._token_expiry - 60:
            return
        if not self._refresh_token:
            raise RuntimeError("No refresh token — re-auth required")
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)

    def _headers(self) -> dict:
        self._ensure_valid_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    # ------------------------------------------------------------------
    # Albums
    # ------------------------------------------------------------------

    def list_albums(self) -> list[Album]:
        albums: list[Album] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"pageSize": 50}
            if page_token:
                params["pageToken"] = page_token
            resp = requests.get(
                f"{PHOTOS_API}/albums",
                headers=self._headers(),
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for a in data.get("albums", []):
                albums.append(
                    Album(
                        remote_id=a["id"],
                        name=a.get("title", a["id"]),
                        cover_url=a.get("coverPhotoBaseUrl"),
                        media_count=int(a.get("mediaItemsCount", 0)),
                    )
                )
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return albums

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync_album(
        self, remote_id: str, local_path: Path, existing_files: set[str]
    ) -> SyncResult:
        local_path.mkdir(parents=True, exist_ok=True)
        result = SyncResult()
        remote_ids: set[str] = set()

        page_token: str | None = None
        while True:
            body: dict[str, Any] = {"albumId": remote_id, "pageSize": 100}
            if page_token:
                body["pageToken"] = page_token
            resp = requests.post(
                f"{PHOTOS_API}/mediaItems:search",
                headers=self._headers(),
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("mediaItems", []):
                item_id = item["id"]
                mime = item.get("mimeType", "image/jpeg")
                ext = _mime_to_ext(mime)
                if ext not in SUPPORTED_EXTENSIONS:
                    continue
                filename = f"{item_id}{ext}"
                remote_ids.add(filename)

                if filename not in existing_files:
                    download_url = item["baseUrl"] + "=d"
                    try:
                        _download_file(
                            download_url, local_path / filename, self._headers()
                        )
                        result.added += 1
                        existing_files.add(filename)
                    except Exception as e:
                        result.errors.append(f"Failed to download {filename}: {e}")
                        logging.warning("[GooglePhotos] %s", result.errors[-1])

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        # Remove files no longer in remote album
        for fname in list(existing_files):
            if fname not in remote_ids:
                try:
                    (local_path / fname).unlink(missing_ok=True)
                    result.removed += 1
                    existing_files.discard(fname)
                except Exception as e:
                    result.errors.append(f"Failed to remove {fname}: {e}")

        return result


def _mime_to_ext(mime: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
    }
    return mapping.get(mime, ".jpg")


def _download_file(url: str, dest: Path, headers: dict) -> None:
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
