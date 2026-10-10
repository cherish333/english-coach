"""Tests for the media-store housekeeping.

`data/media` had no lifecycle management: an interrupted yt-dlp download leaves a
`<stem>.f<id>.mp4.part` behind, and a download that completes after yt-dlp
succeeds but before the document is registered becomes an unowned full-size file.
A real 5.31 MB `.part` had been sitting in the user's media directory for weeks.

The safety requirements matter more than the cleanup: an in-flight import must
never be disturbed, and media a document owns must never be deleted.
"""

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.media_janitor import (
    DEFAULT_ORPHAN_GRACE_SECONDS,
    DEFAULT_PARTIAL_GRACE_SECONDS,
    is_incomplete_media_name,
    media_dir_usage,
    referenced_media_names,
    sweep_media_dir,
)
from src.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "yt_abc.f398.mp4.part",
    "yt_abc.mp4.part",
    "yt_abc.mp4.ytdl",
    "clip.temp",
    "clip.tmp",
    "download.crdownload",
    "video.f137.mp4",
])
def test_incomplete_names_are_recognised(name):
    assert is_incomplete_media_name(name) is True


@pytest.mark.parametrize("name", [
    "yt_abc.mp4", "custom_yt_abc.mp4", "article.m4a", "lesson.webm", "voice.mp3",
])
def test_finished_media_is_not_flagged(name):
    assert is_incomplete_media_name(name) is False


def test_referenced_names_are_normalised():
    names = referenced_media_names(["a/b.mp4", "c.mp4", None, ""])
    assert names == {"b.mp4", "c.mp4"}


# ---------------------------------------------------------------------------
# Cleanup behaviour
# ---------------------------------------------------------------------------

def _make(path: Path, size: int = 64, age_seconds: float = 0) -> Path:
    path.write_bytes(b"x" * size)
    if age_seconds:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


def test_old_partial_is_removed(tmp_path):
    stale = _make(tmp_path / "yt_x.f398.mp4.part", age_seconds=DEFAULT_PARTIAL_GRACE_SECONDS + 60)
    report = sweep_media_dir(tmp_path, [])
    assert "yt_x.f398.mp4.part" in report["partials_removed"]
    assert not stale.exists()
    assert report["bytes_reclaimed"] == 64


def test_fresh_partial_is_kept(tmp_path):
    """An in-flight download must not be deleted out from under yt-dlp."""
    fresh = _make(tmp_path / "yt_y.f137.mp4.part")
    report = sweep_media_dir(tmp_path, [])
    assert fresh.exists(), "a fresh partial download was deleted"
    assert any(s["name"] == fresh.name and s["reason"] == "recent_partial" for s in report["skipped"])
    assert report["bytes_reclaimed"] == 0


def test_referenced_media_is_never_removed(tmp_path):
    """Even an ancient file stays if a document owns it."""
    owned = _make(tmp_path / "custom_yt_lesson.mp4", age_seconds=365 * 24 * 3600)
    report = sweep_media_dir(tmp_path, ["custom_yt_lesson.mp4"])
    assert owned.exists()
    assert report["orphans_removed"] == []


def test_old_unreferenced_media_is_removed(tmp_path):
    orphan = _make(tmp_path / "yt_abandoned.mp4", age_seconds=DEFAULT_ORPHAN_GRACE_SECONDS + 60)
    report = sweep_media_dir(tmp_path, [])
    assert "yt_abandoned.mp4" in report["orphans_removed"]
    assert not orphan.exists()


def test_recent_unreferenced_media_is_kept(tmp_path):
    """It may belong to an import that has not been registered yet."""
    recent = _make(tmp_path / "yt_just_downloaded.mp4")
    report = sweep_media_dir(tmp_path, [])
    assert recent.exists()
    assert any(s["name"] == recent.name and s["reason"] == "recent_orphan" for s in report["skipped"])


def test_dry_run_changes_nothing(tmp_path):
    _make(tmp_path / "yt_x.f398.mp4.part", age_seconds=DEFAULT_PARTIAL_GRACE_SECONDS + 60)
    report = sweep_media_dir(tmp_path, [], dry_run=True)
    assert report["partials_removed"], "dry run found nothing to report"
    assert (tmp_path / "yt_x.f398.mp4.part").exists(), "dry run deleted a file"
    assert report["dry_run"] is True
    assert report["bytes_reclaimed"] == 64


def test_dotfiles_and_directories_are_untouched(tmp_path):
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    report = sweep_media_dir(tmp_path, [])
    assert (tmp_path / ".gitkeep").exists()
    assert (tmp_path / "subdir").is_dir()
    assert report["orphans_removed"] == []


def test_missing_directory_is_not_an_error(tmp_path):
    report = sweep_media_dir(tmp_path / "nope", [])
    assert report["bytes_reclaimed"] == 0
    assert report["partials_removed"] == []


def test_usage_report(tmp_path):
    _make(tmp_path / "a.mp4", size=100)
    _make(tmp_path / "b.mp4", size=50)
    usage = media_dir_usage(tmp_path)
    assert usage == {"bytes": 150, "files": 2}
    assert media_dir_usage(tmp_path / "missing") == {"bytes": 0, "files": 0}


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------

def test_media_usage_endpoint():
    resp = client.get("/api/media/usage")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"bytes", "files", "human"}
    assert isinstance(body["bytes"], int)


def test_media_sweep_dry_run_endpoint():
    resp = client.post("/api/media/sweep?dry_run=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert set(body) >= {"partials_removed", "orphans_removed", "bytes_reclaimed", "reclaimed_human"}


def test_periodic_sweep_is_registered():
    """A one-shot startup sweep cannot reclaim anything during a long session."""
    from src.config import PROJECT_ROOT

    server_py = (PROJECT_ROOT / "src" / "server.py").read_text(encoding="utf-8")
    assert "MEDIA_SWEEP_INTERVAL_SECONDS" in server_py
    assert "_periodic_media_sweep" in server_py
    assert "sweep_task.cancel()" in server_py, "the sweep task must be cancelled on shutdown"


def test_media_sweep_only_targets_the_media_dir():
    """Guard against the sweep ever being pointed at a broader path."""
    from src.config import PROJECT_ROOT

    server_py = (PROJECT_ROOT / "src" / "server.py").read_text(encoding="utf-8")
    assert "sweep_media_dir, MEDIA_DIR" in server_py
    assert PROJECT_ROOT  # sanity: project root resolved
