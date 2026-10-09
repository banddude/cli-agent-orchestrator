"""Gzip + retention maintenance for per-terminal log files (banddude/aiva#512).

LogWriter appends terminal output to ``TERMINAL_LOG_DIR/<terminal_id>.log``
forever; nothing reads them back in normal operation, and on a long-lived
server the directory passed 1 GB. This module periodically:

- gzips ``*.log`` files whose last modification is at least
  ``TERMINAL_LOG_COMPRESS_AFTER_DAYS`` days old (lossless; ``gunzip``
  restores the original bytes),
- deletes ``*.log.gz`` archives older than ``TERMINAL_LOG_RETENTION_DAYS``.

A file fresh enough to be written by LogWriter (mtime inside the window)
is never touched, so an actively-streaming terminal cannot be archived
mid-flight. The gzip write lands via a temp file + atomic rename, so a
concurrent reader never sees a partial archive.
"""

import asyncio
import gzip
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional

from cli_agent_orchestrator.constants import (
    TERMINAL_LOG_COMPRESS_AFTER_DAYS,
    TERMINAL_LOG_DIR,
    TERMINAL_LOG_MAINTENANCE_INTERVAL_S,
    TERMINAL_LOG_RETENTION_DAYS,
)

logger = logging.getLogger(__name__)

_DAY_S = 86400


def maintain_terminal_logs(
    log_dir: Path = TERMINAL_LOG_DIR,
    compress_after_days: int = TERMINAL_LOG_COMPRESS_AFTER_DAYS,
    retention_days: int = TERMINAL_LOG_RETENTION_DAYS,
    now: Optional[float] = None,
) -> Dict[str, int]:
    """One maintenance pass over ``log_dir``.

    Returns counts of compressed files, deleted archives, and bytes freed.
    Never raises for an individual file: a file that fails to compress
    (e.g. raced with a concurrent replace) is skipped and retried on the
    next pass.
    """
    counts: Dict[str, int] = {"compressed": 0, "deleted": 0, "failed": 0, "freed_bytes": 0}
    if not log_dir.is_dir():
        return counts

    current_time = time.time() if now is None else now
    compress_cutoff = current_time - compress_after_days * _DAY_S
    retention_cutoff = current_time - retention_days * _DAY_S

    for path in sorted(log_dir.iterdir()):
        try:
            if not path.is_file():
                continue
            if path.suffix == ".gz":
                if path.stat().st_mtime < retention_cutoff:
                    size = path.stat().st_size
                    os.remove(path)
                    counts["deleted"] += 1
                    counts["freed_bytes"] += size
                continue
            if path.suffix != ".log":
                continue
            if path.stat().st_mtime >= compress_cutoff:
                continue  # fresh or actively written; leave it alone
            plain_size = path.stat().st_size
            if _gzip_file(path):
                counts["compressed"] += 1
                # Freed = plain size minus what the archive still costs.
                gz_size = Path(str(path) + ".gz").stat().st_size
                counts["freed_bytes"] += max(plain_size - gz_size, 0)
            else:
                counts["failed"] += 1
        except FileNotFoundError:
            # Raced with a concurrent pass or a terminal cleanup; skip.
            continue
        except OSError as e:
            logger.warning("Terminal log maintenance skipped %s: %s", path, e)
            counts["failed"] += 1
    return counts


def _gzip_file(path: Path) -> bool:
    """Compress ``path`` to ``path.gz`` atomically, then remove the plain file.

    Returns False (leaving the original untouched) if compression fails at
    any point, so the next pass retries instead of losing data. The freed
    bytes are the difference between the plain file and the archive; the
    original size is reported by the caller before the plain file goes away.
    """
    gz_path = Path(str(path) + ".gz")
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".gz.tmp")
    try:
        with os.fdopen(fd, "wb") as tmp_f, open(path, "rb") as f_in:
            with gzip.GzipFile(fileobj=tmp_f, mode="wb") as gz_f:
                shutil.copyfileobj(f_in, gz_f, length=1024 * 1024)
            tmp_f.flush()
            os.fsync(tmp_f.fileno())
        os.replace(tmp_name, gz_path)
    except OSError:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        return False
    os.remove(path)
    return True


async def run_periodic() -> None:
    """Run one maintenance pass at startup, then every maintenance interval."""
    logger.info(
        "Terminal log maintenance started (compress after %dd, retain %dd)",
        TERMINAL_LOG_COMPRESS_AFTER_DAYS,
        TERMINAL_LOG_RETENTION_DAYS,
    )
    while True:
        try:
            counts = await asyncio.to_thread(maintain_terminal_logs)
            if counts["compressed"] or counts["deleted"] or counts["failed"]:
                logger.info(
                    "Terminal log maintenance: %d compressed, %d deleted, "
                    "%d failed, %.1f MiB freed",
                    counts["compressed"],
                    counts["deleted"],
                    counts["failed"],
                    counts["freed_bytes"] / (1024 * 1024),
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Terminal log maintenance pass failed: %s", e)
        await asyncio.sleep(TERMINAL_LOG_MAINTENANCE_INTERVAL_S)
