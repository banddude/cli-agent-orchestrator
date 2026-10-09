import gzip
import logging
import logging.handlers
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from cli_agent_orchestrator.constants import (
    LOG_DIR,
    SERVER_LOG_BACKUP_COUNT,
    SERVER_LOG_MAX_BYTES,
)
from cli_agent_orchestrator.services.config_service import ConfigService

# Query parameters that carry bearer credentials. `access_token` is the AG-UI
# SSE pattern (browser EventSource cannot set an Authorization header);
# `ticket` is reserved for the planned short-lived-ticket handshake.
_CREDENTIAL_PARAMS = ("access_token", "ticket")
REDACTED = "[REDACTED]"
_CREDENTIAL_RE = re.compile(
    r"\b(" + "|".join(_CREDENTIAL_PARAMS) + r")=([^&\s\"']+)",
)


class RedactQueryTokenFilter(logging.Filter):
    """Scrub credential-bearing query parameters from log records.

    Attached to ``uvicorn.access`` so ``GET /agui/v1/stream?access_token=<JWT>``
    lines never persist the token (uvicorn logs the raw path+query, and a JWT in
    an access log is replayable until ``exp``). Mutates the record in place and
    always returns True — this filter redacts, it never drops.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and _CREDENTIAL_RE.search(record.msg):
            record.msg = _CREDENTIAL_RE.sub(rf"\1={REDACTED}", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _CREDENTIAL_RE.sub(rf"\1={REDACTED}", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


def install_access_log_redaction() -> None:
    """Attach the credential filter to uvicorn's access logger (idempotent)."""
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactQueryTokenFilter) for f in access_logger.filters):
        access_logger.addFilter(RedactQueryTokenFilter())


class GzipRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Daily + size-capped rotation with gzipped, pruned archives.

    The plain per-start ``FileHandler`` this replaces let a long-lived server
    grow a single ``cao_<timestamp>.log`` past 2 GB (banddude/aiva#512).

    - Daily rollover (``when="midnight"``) AND rollover whenever the active
      file reaches ``maxBytes``, so a chatty day cannot outgrow the cap.
    - Every rotated file is compressed to ``<name>.<date>.gz`` (lossless;
      ``gunzip`` restores it).
    - Size-triggered rollovers on the same day pick the first free
      ``<name>.<date>.<n>.gz`` instead of colliding on one dated name. This
      is also why ``doRollover`` is implemented here rather than inherited:
      the stdlib ``TimedRotatingFileHandler.doRollover`` early-returns when
      the dated archive already exists, which would let the active file grow
      past ``maxBytes`` for the rest of the day after the first rollover.
    - Archives beyond ``backupCount`` are pruned, oldest first.

    The active file keeps its plain ``cao_<timestamp>.log`` name, so existing
    tooling that globs ``cao_*.log`` still finds it.
    """

    def __init__(
        self,
        filename: str,
        when: str = "midnight",
        backupCount: int = SERVER_LOG_BACKUP_COUNT,
        maxBytes: int = SERVER_LOG_MAX_BYTES,
        **kwargs,
    ) -> None:
        super().__init__(filename, when=when, backupCount=backupCount, **kwargs)
        self.maxBytes = maxBytes

    def _size_exceeded(self) -> bool:
        if self.maxBytes <= 0 or not os.path.exists(self.baseFilename):
            return False
        return os.stat(self.baseFilename).st_size >= self.maxBytes

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if super().shouldRollover(record):
            return True
        return self._size_exceeded()

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            # typeshed declares StreamHandler.stream non-Optional even though
            # the stdlib itself sets it to None between rotations.
            self.stream = None  # type: ignore[assignment]
        if os.path.exists(self.baseFilename):
            date_suffix = time.strftime(self.suffix, time.localtime())
            dest = self.baseFilename + "." + date_suffix + ".gz"
            if os.path.exists(dest):
                stem, ext = os.path.splitext(dest)
                n = 1
                while os.path.exists(f"{stem}.{n}{ext}"):
                    n += 1
                dest = f"{stem}.{n}{ext}"
            with open(self.baseFilename, "rb") as f_in, gzip.open(dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out, length=1024 * 1024)
            os.remove(self.baseFilename)
        if not self.delay:
            self.stream = self._open()
        self.rolloverAt = self.computeRollover(int(time.time()))
        self._prune_archives()

    def _prune_archives(self) -> None:
        backup_count = self.backupCount
        if backup_count <= 0:
            return
        base = os.path.basename(self.baseFilename)
        rotated = sorted(
            (p for p in Path(self.dirname).glob(base + ".*") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        for path in rotated[:-backup_count] if len(rotated) > backup_count else []:
            try:
                os.remove(path)
            except OSError:
                pass  # best-effort retention; a locked file rotates out later

    @property
    def dirname(self) -> str:
        return os.path.dirname(self.baseFilename) or "."


def setup_logging() -> None:
    """Setup logging configuration."""
    log_level = str(ConfigService.get("logging.level", default="INFO")).upper()

    # Ensure log directory exists
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = LOG_DIR / f"cao_{timestamp}.log"

    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # File handler: daily + size rotation with gzipped archives (aiva#512) so a
    # long-lived server cannot grow one cao_*.log past the size cap.
    file_handler = GzipRotatingFileHandler(str(log_file))

    # Stream handler: WARNING+ always goes to stderr so operationally-relevant
    # events surface on the console (and in a subprocess's captured stdout/stderr,
    # which the e2e harness asserts on) rather than being buried in the log file.
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter(fmt))

    logging.basicConfig(
        level=log_level,
        format=fmt,
        handlers=[file_handler, stderr_handler],
    )

    print(f"Server logs: {log_file}")
    print("For debug logs: export CAO_LOG_LEVEL=DEBUG && cao-server")
    logging.info(f"Logging to: {log_file}")
