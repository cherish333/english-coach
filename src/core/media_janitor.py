"""Media-store housekeeping.

Why this exists
---------------
`data/media` had no lifecycle management at all:

* yt-dlp writes ``<stem>.f<id>.mp4.part`` while downloading and leaves it behind
  when a download is interrupted or times out. Nothing removed those, and a real
  5.31 MB ``yt_...f398.mp4.part`` had been sitting in the user's media directory
  since an aborted import weeks earlier.
* ``document_library.delete_custom_document`` cleans up after a *document*, but a
  download that fails **after** yt-dlp succeeded and **before** the document is
  registered leaves a full-size media file with no owner. Because imports also
  re-download rather than reuse (the canonical file is renamed on registration),
  a few retries can silently cost hundreds of megabytes.

The janitor is deliberately conservative: it only removes artefacts that are
clearly incomplete, or complete files that no catalog entry references **and**
that are older than a grace period, so an import that is in flight is never
disturbed.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

logger = logging.getLogger("english_coach.media_janitor")

# Incomplete-download markers (yt-dlp / ffmpeg conventions).
INCOMPLETE_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp", ".crdownload")
INCOMPLETE_INFIXES = (".f137.", ".f138.", ".f140.", ".f251.", ".f399.")

# A partial file is junk after an hour; a complete but unreferenced file may
# belong to an import that is still being registered, so give it days.
DEFAULT_PARTIAL_GRACE_SECONDS = 3600
DEFAULT_ORPHAN_GRACE_SECONDS = 7 * 24 * 3600


def is_incomplete_media_name(name: str) -> bool:
    """True when a filename is a download artefact rather than finished media."""
    lowered = name.lower()
    if any(lowered.endswith(suffix) for suffix in INCOMPLETE_SUFFIXES):
        return True
    return any(infix in lowered for infix in INCOMPLETE_INFIXES)


def referenced_media_names(media_filenames: Iterable[Optional[str]]) -> Set[str]:
    """Normalize the set of media filenames that documents currently own."""
    return {Path(name).name for name in media_filenames if name}


def sweep_media_dir(
    media_dir: Path,
    referenced: Iterable[Optional[str]],
    *,
    partial_grace_seconds: int = DEFAULT_PARTIAL_GRACE_SECONDS,
    orphan_grace_seconds: int = DEFAULT_ORPHAN_GRACE_SECONDS,
    now: Optional[float] = None,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Remove download artefacts and unreferenced media from ``media_dir``.

    Returns a report with what was removed and how many bytes were reclaimed.
    Never raises for an individual file; a locked or vanished file is skipped.
    """
    report: Dict[str, object] = {
        "media_dir": str(media_dir),
        "partials_removed": [],
        "orphans_removed": [],
        "bytes_reclaimed": 0,
        "skipped": [],
        "dry_run": dry_run,
    }
    if not media_dir.is_dir():
        return report

    current = time.time() if now is None else now
    owned = referenced_media_names(referenced)

    for entry in sorted(media_dir.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue

        age = max(0.0, current - stat.st_mtime)
        is_partial = is_incomplete_media_name(entry.name)

        if is_partial:
            if age < partial_grace_seconds:
                report["skipped"].append({"name": entry.name, "reason": "recent_partial"})
                continue
            bucket = "partials_removed"
        elif entry.name in owned:
            continue
        else:
            # Unreferenced complete file: could be an import still in progress.
            if age < orphan_grace_seconds:
                report["skipped"].append({"name": entry.name, "reason": "recent_orphan"})
                continue
            bucket = "orphans_removed"

        if dry_run:
            report[bucket].append(entry.name)  # type: ignore[union-attr]
            report["bytes_reclaimed"] = int(report["bytes_reclaimed"]) + stat.st_size
            continue

        try:
            entry.unlink()
        except OSError as exc:
            logger.warning("Could not remove %s: %s", entry, exc)
            report["skipped"].append({"name": entry.name, "reason": f"unlink_failed: {exc}"})
            continue

        report[bucket].append(entry.name)  # type: ignore[union-attr]
        report["bytes_reclaimed"] = int(report["bytes_reclaimed"]) + stat.st_size

    return report


def media_dir_usage(media_dir: Path) -> Dict[str, int]:
    """Total bytes and file count for the media store."""
    total = 0
    count = 0
    if not media_dir.is_dir():
        return {"bytes": 0, "files": 0}
    for entry in media_dir.iterdir():
        try:
            if entry.is_file():
                total += entry.stat().st_size
                count += 1
        except OSError:
            continue
    return {"bytes": total, "files": count}
