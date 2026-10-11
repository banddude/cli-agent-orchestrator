"""Tests for logging utility."""

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.utils.logging import setup_logging


class TestSetupLogging:
    """Tests for setup_logging function."""

    @patch("cli_agent_orchestrator.utils.logging.LOG_DIR")
    @patch("cli_agent_orchestrator.utils.logging.logging.basicConfig")
    def test_setup_logging_default_level(self, mock_basic_config, mock_log_dir):
        """Test setup_logging with default INFO level."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_log_dir.__truediv__ = lambda self, x: Path(tmpdir) / x
            mock_log_dir.mkdir = MagicMock()

            with patch.dict("os.environ", {}, clear=True):
                with patch("builtins.print"):
                    setup_logging()

            mock_basic_config.assert_called_once()
            call_kwargs = mock_basic_config.call_args[1]
            assert call_kwargs["level"] == "INFO"

    @patch("cli_agent_orchestrator.utils.logging.LOG_DIR")
    @patch("cli_agent_orchestrator.utils.logging.logging.basicConfig")
    def test_setup_logging_debug_level(self, mock_basic_config, mock_log_dir):
        """Test setup_logging with DEBUG level from env var."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_log_dir.__truediv__ = lambda self, x: Path(tmpdir) / x
            mock_log_dir.mkdir = MagicMock()

            with patch.dict("os.environ", {"CAO_LOG_LEVEL": "DEBUG"}):
                with patch("builtins.print"):
                    setup_logging()

            mock_basic_config.assert_called_once()
            call_kwargs = mock_basic_config.call_args[1]
            assert call_kwargs["level"] == "DEBUG"

    @patch("cli_agent_orchestrator.utils.logging.LOG_DIR")
    @patch("cli_agent_orchestrator.utils.logging.logging.basicConfig")
    def test_setup_logging_warning_level(self, mock_basic_config, mock_log_dir):
        """Test setup_logging with WARNING level."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_log_dir.__truediv__ = lambda self, x: Path(tmpdir) / x
            mock_log_dir.mkdir = MagicMock()

            with patch.dict("os.environ", {"CAO_LOG_LEVEL": "warning"}):
                with patch("builtins.print"):
                    setup_logging()

            mock_basic_config.assert_called_once()
            call_kwargs = mock_basic_config.call_args[1]
            assert call_kwargs["level"] == "WARNING"

    @patch("cli_agent_orchestrator.utils.logging.LOG_DIR")
    @patch("cli_agent_orchestrator.utils.logging.logging.basicConfig")
    def test_setup_logging_creates_log_directory(self, mock_basic_config, mock_log_dir):
        """Test setup_logging creates log directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_log_dir.__truediv__ = lambda self, x: Path(tmpdir) / x
            mock_log_dir.mkdir = MagicMock()

            with patch("builtins.print"):
                setup_logging()

            mock_log_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @patch("cli_agent_orchestrator.utils.logging.LOG_DIR")
    @patch("cli_agent_orchestrator.utils.logging.logging.basicConfig")
    def test_setup_logging_prints_info(self, mock_basic_config, mock_log_dir):
        """Test setup_logging prints log file location."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_log_dir.__truediv__ = lambda self, x: Path(tmpdir) / x
            mock_log_dir.mkdir = MagicMock()

            with patch("builtins.print") as mock_print:
                setup_logging()

            # Should print log file location
            assert mock_print.call_count == 2
            calls = [str(call) for call in mock_print.call_args_list]
            assert any("Server logs" in str(call) for call in calls)

    @patch("cli_agent_orchestrator.utils.logging.LOG_DIR")
    @patch("cli_agent_orchestrator.utils.logging.logging.basicConfig")
    @patch("cli_agent_orchestrator.utils.logging.logging.info")
    def test_setup_logging_logs_info(self, mock_log_info, mock_basic_config, mock_log_dir):
        """Test setup_logging logs info message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_log_dir.__truediv__ = lambda self, x: Path(tmpdir) / x
            mock_log_dir.mkdir = MagicMock()

            with patch("builtins.print"):
                setup_logging()

            mock_log_info.assert_called_once()
            assert "Logging to" in str(mock_log_info.call_args)

    @patch("cli_agent_orchestrator.utils.logging.LOG_DIR")
    @patch("cli_agent_orchestrator.utils.logging.logging.basicConfig")
    def test_setup_logging_format(self, mock_basic_config, mock_log_dir):
        """Test setup_logging uses correct log format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_log_dir.__truediv__ = lambda self, x: Path(tmpdir) / x
            mock_log_dir.mkdir = MagicMock()

            with patch("builtins.print"):
                setup_logging()

            call_kwargs = mock_basic_config.call_args[1]
            assert "format" in call_kwargs
            assert "%(asctime)s" in call_kwargs["format"]
            assert "%(name)s" in call_kwargs["format"]
            assert "%(levelname)s" in call_kwargs["format"]


class TestGzipRotatingFileHandler:
    """aiva#512: the per-start cao_*.log file grew past 2 GB on a long-lived
    server. The handler must roll on size as well as daily, gzip every rotated
    file, never clobber a same-day archive, and prune beyond backupCount."""

    def _handler(self, log_dir, max_bytes, backup_count=14, when="midnight"):
        from cli_agent_orchestrator.utils.logging import GzipRotatingFileHandler

        log_file = Path(log_dir) / "cao_2026-10-09_00-00-00.log"
        return GzipRotatingFileHandler(
            str(log_file),
            when=when,
            backupCount=backup_count,
            maxBytes=max_bytes,
            delay=True,
        )

    def test_size_rollover_gzips_archive(self, tmp_path):
        """Writing past maxBytes rotates, compresses the old content, and the
        active file starts fresh."""
        import gzip as gz

        handler = self._handler(tmp_path, max_bytes=500)
        logger = logging.getLogger("rotate-size")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            for i in range(50):
                logger.info("x" * 100 + str(i))
            handler.flush()
            archives = list(Path(tmp_path).glob("cao_2026-10-09_00-00-00.log.*.gz"))
            assert archives, "a size-triggered rollover must produce a .gz archive"
            with gz.open(archives[0], "rt") as f:
                archived = f.read()
            assert "x" * 100 in archived, "the archive must hold the pre-rollover lines"
            active = Path(tmp_path) / "cao_2026-10-09_00-00-00.log"
            assert active.exists()
            # The crossing record is written before the size check sees it, so
            # the restarted file may hold at most one post-rollover record.
            assert (
                active.stat().st_size <= 500 + 150
            ), f"the active file must restart near the cap, got {active.stat().st_size}"
        finally:
            logger.removeHandler(handler)
            handler.close()

    def test_same_day_rollovers_do_not_clobber(self, tmp_path):
        """Two rollovers on the same day pick distinct archive names."""
        handler = self._handler(tmp_path, max_bytes=300)
        logger = logging.getLogger("rotate-collide")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            for i in range(200):
                logger.info("y" * 100 + str(i))
            archives = list(Path(tmp_path).glob("cao_2026-10-09_00-00-00.log.*.gz"))
            assert len(archives) >= 2, (
                "two size rollovers on one day must both survive, "
                f"got {sorted(p.name for p in archives)}"
            )
        finally:
            logger.removeHandler(handler)
            handler.close()

    def test_backup_count_prunes_oldest_archives(self, tmp_path):
        """Rotating more times than backupCount keeps only the newest N."""
        handler = self._handler(tmp_path, max_bytes=200, backup_count=2)
        logger = logging.getLogger("rotate-prune")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            for i in range(400):
                logger.info("z" * 100 + str(i))
            archives = list(Path(tmp_path).glob("cao_2026-10-09_00-00-00.log.*"))
            assert len(archives) <= 2, f"expected <=2 archives, got {len(archives)}"
        finally:
            logger.removeHandler(handler)
            handler.close()

    def test_no_rollover_below_cap(self, tmp_path):
        """A file under maxBytes is not rotated on write."""
        handler = self._handler(tmp_path, max_bytes=1024 * 1024)
        logger = logging.getLogger("rotate-idle")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            logger.info("small line")
            handler.flush()
            archives = list(Path(tmp_path).glob("cao_2026-10-09_00-00-00.log.*"))
            assert archives == []
        finally:
            logger.removeHandler(handler)
            handler.close()


class TestSetupLoggingRotation:
    """setup_logging must install the rotating file handler (aiva#512)."""

    @patch("cli_agent_orchestrator.utils.logging.LOG_DIR")
    @patch("cli_agent_orchestrator.utils.logging.logging.basicConfig")
    def test_setup_logging_uses_rotating_handler(self, mock_basic_config, mock_log_dir):
        from cli_agent_orchestrator.utils.logging import GzipRotatingFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_log_dir.__truediv__ = lambda self, x: Path(tmpdir) / x
            mock_log_dir.mkdir = MagicMock()

            with patch("builtins.print"):
                setup_logging()

            call_kwargs = mock_basic_config.call_args[1]
            handlers = call_kwargs["handlers"]
            assert any(isinstance(h, GzipRotatingFileHandler) for h in handlers)


class TestRotationSafety:
    def test_failed_compression_preserves_original_and_leaves_no_archive(self, tmp_path):
        from cli_agent_orchestrator.utils.logging import GzipRotatingFileHandler

        source = tmp_path / "cao_test.log"
        source.write_text("original bytes\n")
        handler = GzipRotatingFileHandler(str(source), delay=True)
        try:
            with patch(
                "cli_agent_orchestrator.utils.logging.shutil.copyfileobj",
                side_effect=OSError("disk full"),
            ):
                with pytest.raises(OSError, match="disk full"):
                    handler.doRollover()
            assert source.read_text() == "original bytes\n"
            assert sorted(p.name for p in tmp_path.iterdir()) == ["cao_test.log"]
        finally:
            handler.close()

    def test_racing_archive_collision_preserves_both_sources(self, tmp_path):
        import os

        from cli_agent_orchestrator.utils.logging import GzipRotatingFileHandler

        source = tmp_path / "cao_test.log"
        source.write_text("original bytes\n")
        handler = GzipRotatingFileHandler(str(source), delay=True)
        real_link = os.link

        def collision(temporary, destination):
            Path(destination).write_bytes(b"other archive")
            real_link(temporary, destination)

        try:
            with patch("cli_agent_orchestrator.utils.logging.os.link", side_effect=collision):
                with pytest.raises(FileExistsError):
                    handler.doRollover()
            assert source.read_text() == "original bytes\n"
            assert next(tmp_path.glob("*.gz")).read_bytes() == b"other archive"
            assert not list(tmp_path.glob("*.tmp"))
        finally:
            handler.close()

    def test_retention_ignores_unrelated_files_and_symlinks(self, tmp_path):
        from cli_agent_orchestrator.utils.logging import GzipRotatingFileHandler

        handler = GzipRotatingFileHandler(str(tmp_path / "cao_test.log"), backupCount=1, delay=True)
        preserved = [
            "cao_test.log.backup",
            "cao_test.log.unrelated.gz",
            "cao_test.log.2026-10-01.gz.tmp",
        ]
        for name in preserved:
            (tmp_path / name).write_text("keep")
        target = tmp_path / "target"
        target.write_text("keep target")
        link = tmp_path / "cao_test.log.2026-09-01.gz"
        link.symlink_to(target)
        for day in ["01", "02", "03"]:
            (tmp_path / f"cao_test.log.2026-10-{day}.gz").write_text("owned archive")
        try:
            handler._prune_archives()
            assert all((tmp_path / name).read_text() == "keep" for name in preserved)
            assert link.is_symlink()
            assert target.read_text() == "keep target"
            assert len(list(tmp_path.glob("cao_test.log.2026-10-??.gz"))) == 1
        finally:
            handler.close()

    def test_rotation_leaves_old_terminal_writer_and_range_bytes_intact(self, tmp_path):
        import os

        from cli_agent_orchestrator.utils.logging import GzipRotatingFileHandler

        terminal = tmp_path / "terminal.log"
        terminal.write_text("before\n")
        os.utime(terminal, (1, 1))
        server = tmp_path / "cao_test.log"
        server.write_text("server log\n")
        handler = GzipRotatingFileHandler(str(server), delay=True)
        try:
            with terminal.open("a") as writer:
                handler.doRollover()
                writer.write("after\n")
                writer.flush()
                with terminal.open("rb") as reader:
                    assert reader.read() == b"before\nafter\n"
            assert not (tmp_path / "terminal.log.gz").exists()
        finally:
            handler.close()
