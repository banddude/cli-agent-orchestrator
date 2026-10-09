"""Tests for terminal log gzip + retention maintenance (banddude/aiva#512)."""

import gzip
import os
import time
from pathlib import Path

import pytest

from cli_agent_orchestrator.services.terminal_log_cleanup import (
    _gzip_file,
    maintain_terminal_logs,
)

_DAY_S = 86400


def _age(path: Path, days: float) -> None:
    """Backdate a file's mtime so threshold branches are deterministic."""
    stamp = time.time() - days * _DAY_S
    os.utime(path, (stamp, stamp))


class TestMaintainTerminalLogs:
    def test_old_log_is_gzipped_losslessly(self, tmp_path):
        log = tmp_path / "abc123.log"
        payload = "terminal output\n" * 100
        log.write_text(payload)
        _age(log, 4)

        counts = maintain_terminal_logs(log_dir=tmp_path, compress_after_days=3)

        assert counts["compressed"] == 1
        assert not log.exists(), "the plain file must be gone after compression"
        gz = tmp_path / "abc123.log.gz"
        assert gz.exists()
        assert gzip.open(gz, "rt").read() == payload, "gzip must be lossless"

    def test_fresh_log_is_untouched(self, tmp_path):
        log = tmp_path / "fresh.log"
        log.write_text("recent output")
        _age(log, 1)

        counts = maintain_terminal_logs(log_dir=tmp_path, compress_after_days=3)

        assert counts == {"compressed": 0, "deleted": 0, "failed": 0, "freed_bytes": 0}
        assert log.exists()

    def test_boundary_age_is_untouched(self, tmp_path):
        """A file exactly at the compress threshold counts as fresh; one
        second past it, it compresses. Driven by the injected clock so the
        boundary comparison is exact rather than racing time.time()."""
        log = tmp_path / "boundary.log"
        log.write_text("x")
        stamp = time.time()
        os.utime(log, (stamp, stamp))

        counts = maintain_terminal_logs(
            log_dir=tmp_path, compress_after_days=3, now=stamp + 3 * _DAY_S
        )
        assert counts["compressed"] == 0
        assert log.exists()

        counts = maintain_terminal_logs(
            log_dir=tmp_path, compress_after_days=3, now=stamp + 3 * _DAY_S + 1
        )
        assert counts["compressed"] == 1
        assert not log.exists()

    def test_archive_past_retention_is_deleted(self, tmp_path):
        gz = tmp_path / "old.log.gz"
        gz.write_bytes(b"archived")
        _age(gz, 31)

        counts = maintain_terminal_logs(log_dir=tmp_path, retention_days=30)

        assert counts["deleted"] == 1
        assert not gz.exists()

    def test_archive_within_retention_is_kept(self, tmp_path):
        gz = tmp_path / "recent.log.gz"
        gz.write_bytes(b"archived")
        _age(gz, 5)

        maintain_terminal_logs(log_dir=tmp_path, retention_days=30)

        assert gz.exists()

    def test_unrelated_files_are_ignored(self, tmp_path):
        other = tmp_path / "notes.txt"
        other.write_text("not a terminal log")
        _age(other, 10)

        counts = maintain_terminal_logs(log_dir=tmp_path)

        assert counts["compressed"] == 0
        assert other.exists()

    def test_failed_compression_leaves_original(self, tmp_path, monkeypatch):
        log = tmp_path / "stuck.log"
        log.write_text("data")
        _age(log, 4)
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_log_cleanup._gzip_file",
            lambda p: False,
        )

        counts = maintain_terminal_logs(log_dir=tmp_path)

        assert counts["failed"] == 1
        assert log.exists(), "a failed compression must leave the original in place"

    def test_missing_dir_is_a_noop(self, tmp_path):
        counts = maintain_terminal_logs(log_dir=tmp_path / "nope")
        assert counts["compressed"] == 0

    def test_gzip_file_atomic_and_lossless(self, tmp_path):
        log = tmp_path / "xyz.log"
        payload = b"binary-ish \x00 output" * 10
        log.write_bytes(payload)

        assert _gzip_file(log) is True
        assert not log.exists()
        assert gzip.open(tmp_path / "xyz.log.gz", "rb").read() == payload


class TestNowInjection:
    """The pass must accept an injected clock so retention logic is testable."""

    def test_now_drives_compress_decision(self, tmp_path):
        log = tmp_path / "clocked.log"
        log.write_text("x")
        fake_now = time.time() + 10 * _DAY_S  # file looks 10 days old "now"

        counts = maintain_terminal_logs(log_dir=tmp_path, compress_after_days=3, now=fake_now)

        assert counts["compressed"] == 1


@pytest.mark.parametrize("filename", ["weird name.log", "nested.log"])
def test_odd_names_round_trip(tmp_path, filename):
    log = tmp_path / filename
    log.write_text("payload")
    _age(log, 4)

    maintain_terminal_logs(log_dir=tmp_path)

    assert gzip.open(tmp_path / (filename + ".gz"), "rt").read() == "payload"
