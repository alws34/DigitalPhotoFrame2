from pathlib import Path

from Utilities.sources.immich_cache import ImmichStreamingCache


class FakeImmichSource:
    def download_asset(self, asset_id: str, dest: Path) -> None:
        dest.write_bytes(f"asset:{asset_id}".encode("utf-8"))


def test_download_notifies_after_atomic_write(tmp_path):
    notifications = []
    cache = ImmichStreamingCache(
        source=FakeImmichSource(),
        remote_id="album-1",
        local_path=tmp_path,
        delay_seconds=1,
        on_change=lambda: notifications.append("changed"),
    )

    assert cache._download("asset-1", ".jpg") is True

    assert notifications == ["changed"]
    assert [p.name for p in tmp_path.iterdir()] == ["pf_stream_0000000000.jpg"]


def test_clear_cache_notifies_only_when_files_removed(tmp_path):
    notifications = []
    cache = ImmichStreamingCache(
        source=FakeImmichSource(),
        remote_id="album-1",
        local_path=tmp_path,
        delay_seconds=1,
        on_change=lambda: notifications.append("changed"),
    )

    cache._clear_cache()
    assert notifications == []

    (tmp_path / "pf_stream_0000000000.jpg").write_bytes(b"cached")
    (tmp_path / "pf_stream_0000000001.jpg.tmp").write_bytes(b"partial")

    cache._clear_cache()

    assert notifications == ["changed"]
    assert list(tmp_path.iterdir()) == []
