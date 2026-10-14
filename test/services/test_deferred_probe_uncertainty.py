"""Unavailable startup observations must never authorize retry or teardown.

All pane, provider, metadata, input and notification boundaries are synthetic.
No CLI, browser, live worker, server or external inbox is used.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import terminal_service as ts


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["capture", "provider"])
@pytest.mark.parametrize("final_probe", [False, True])
async def test_unavailable_probe_stops_cached_idle_recovery(failure, final_probe):
    provider = MagicMock(supports_direct_status_probe=True)
    idle_probes = 3 if final_probe else 0
    provider.get_status.side_effect = (
        [TerminalStatus.IDLE] * idle_probes + [RuntimeError("synthetic parser failure")]
        if failure == "provider"
        else [TerminalStatus.IDLE] * (idle_probes + 1)
    )
    with (
        patch.object(ts, "wait_until_status", new=AsyncMock(return_value=False)),
        patch.object(
            ts, "get_terminal_metadata", return_value={"tmux_session": "s", "tmux_window": "w"}
        ),
        patch.object(ts, "get_backend") as backend,
        patch.object(ts, "_message_visible_in_box", return_value=False),
        patch.object(ts, "send_input") as send,
        patch.object(ts, "send_special_key") as key,
    ):
        backend.return_value.get_history.side_effect = (
            ["synthetic idle pane"] * idle_probes + [OSError("synthetic capture failure")]
            if failure == "capture"
            else ["synthetic idle pane"] * (idle_probes + 1)
        )
        result = await ts._confirm_worker_started_or_resubmit(
            "worker", "Synthetic task", None, None, None, provider=provider
        )
    assert result is None, "an unavailable observation is neither started nor confirmed idle"
    assert send.call_count == idle_probes
    key.assert_not_called()
    assert backend.return_value.get_history.call_count == idle_probes + 1


@pytest.mark.parametrize("status", [TerminalStatus.UNKNOWN, TerminalStatus.ERROR])
def test_non_idle_unstarted_observation_is_unavailable(status):
    provider = MagicMock()
    provider.get_status.return_value = status
    with (
        patch.object(
            ts, "get_terminal_metadata", return_value={"tmux_session": "s", "tmux_window": "w"}
        ),
        patch.object(ts, "get_backend"),
    ):
        assert ts._worker_is_started_direct("worker", provider) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("in_box", [False, True])
async def test_confirmed_idle_still_recovers_then_observes_started(in_box):
    provider = MagicMock(supports_direct_status_probe=True)
    provider.get_status.side_effect = [TerminalStatus.IDLE, TerminalStatus.PROCESSING]
    with (
        patch.object(ts, "wait_until_status", new=AsyncMock(return_value=False)),
        patch.object(
            ts, "get_terminal_metadata", return_value={"tmux_session": "s", "tmux_window": "w"}
        ),
        patch.object(ts, "get_backend"),
        patch.object(ts, "_message_visible_in_box", return_value=in_box),
        patch.object(ts, "send_input") as send,
        patch.object(ts, "send_special_key") as key,
    ):
        assert (
            await ts._confirm_worker_started_or_resubmit(
                "worker", "Synthetic task", None, None, None, provider=provider
            )
            is True
        )
    assert send.call_count == (0 if in_box else 1)
    assert key.call_count == (1 if in_box else 0)


@pytest.mark.asyncio
async def test_confirmed_idle_exhaustion_still_reports_not_started():
    provider = MagicMock(supports_direct_status_probe=True)
    provider.get_status.return_value = TerminalStatus.IDLE
    with (
        patch.object(ts, "wait_until_status", new=AsyncMock(return_value=False)),
        patch.object(
            ts, "get_terminal_metadata", return_value={"tmux_session": "s", "tmux_window": "w"}
        ),
        patch.object(ts, "get_backend"),
        patch.object(ts, "_message_visible_in_box", return_value=False),
        patch.object(ts, "send_input") as send,
    ):
        assert (
            await ts._confirm_worker_started_or_resubmit(
                "worker", "Synthetic task", None, None, None, provider=provider
            )
            is False
        )
    assert send.call_count == 3
    assert provider.get_status.call_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "started",
    [TerminalStatus.PROCESSING, TerminalStatus.COMPLETED, TerminalStatus.WAITING_USER_ANSWER],
)
async def test_producing_or_started_worker_never_resubmits(started):
    provider = MagicMock(supports_direct_status_probe=True)
    provider.get_status.return_value = started
    with (
        patch.object(ts, "wait_until_status", new=AsyncMock(return_value=False)),
        patch.object(
            ts, "get_terminal_metadata", return_value={"tmux_session": "s", "tmux_window": "w"}
        ),
        patch.object(ts, "get_backend"),
        patch.object(ts, "send_input") as send,
        patch.object(ts, "send_special_key") as key,
    ):
        assert (
            await ts._confirm_worker_started_or_resubmit(
                "worker", "Synthetic task", None, None, None, provider=provider
            )
            is True
        )
    send.assert_not_called()
    key.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_receipt_preserves_worker_without_claiming_started():
    provider = MagicMock(supports_direct_status_probe=True)
    provider.initialize = AsyncMock()
    provider.shell_baseline = None
    with (
        patch.object(
            ts,
            "get_terminal_metadata",
            return_value={"caller_id": None, "tmux_session": "s", "tmux_window": "w"},
        ),
        patch.object(ts, "send_input") as send,
        patch.object(ts, "wait_until_status", new=AsyncMock(return_value=False)),
        patch.object(ts, "get_backend") as backend,
        patch.object(ts, "_notify_caller_of_deferred_failure") as notify,
        patch.object(ts, "delete_terminal") as delete,
    ):
        backend.return_value.get_history.side_effect = OSError("synthetic capture failure")
        before = set(ts._deferred_init_tasks)
        ts._schedule_deferred_init(provider, "worker", "Synthetic task", None, None)
        (task,) = ts._deferred_init_tasks - before
        await task
    send.assert_called_once()  # Initial submission only, no automatic retry.
    notify.assert_called_once()
    assert notify.call_args.kwargs["delete_worker"] is False
    assert "unavailable" in notify.call_args.args[1]
    assert "started" not in notify.call_args.args[1]
    delete.assert_not_called()
