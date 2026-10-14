"""Synthetic message-box observations; no worker, backend or inbox is real."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import terminal_service as ts


@pytest.mark.parametrize("failure", [OSError("capture unavailable"), None])
def test_box_observation_unavailable_is_not_confirmed_absence(failure):
    with patch.object(
        ts,
        "get_output",
        side_effect=failure if isinstance(failure, Exception) else None,
        return_value=None,
    ):
        assert ts._message_visible_in_box("w", "Synthetic task") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("direct", [True, False])
async def test_unavailable_box_never_resubmits_or_tears_down(direct):
    provider = MagicMock(supports_direct_status_probe=direct)
    provider.initialize = AsyncMock()
    provider.shell_baseline = None
    provider.get_status.return_value = TerminalStatus.IDLE
    with (
        patch.object(
            ts,
            "get_terminal_metadata",
            return_value={"caller_id": "supervisor", "tmux_session": "s", "tmux_window": "w"},
        ),
        patch.object(ts, "wait_until_status", new=AsyncMock(return_value=False)),
        patch.object(ts, "get_backend"),
        patch.object(ts, "get_output", side_effect=OSError("capture unavailable")) as output,
        patch.object(ts, "send_input") as send,
        patch.object(ts, "send_special_key") as key,
        patch.object(ts, "create_inbox_message") as inbox,
        patch.object(ts, "delete_terminal") as delete,
    ):
        before = set(ts._deferred_init_tasks)
        ts._schedule_deferred_init(provider, "worker", "Synthetic task", None, None)
        await next(iter(ts._deferred_init_tasks - before))
    send.assert_called_once()  # Initial submission only.
    key.assert_not_called()
    delete.assert_not_called()
    output.assert_called_once()
    inbox.assert_called_once()
    receipt = inbox.call_args.kwargs["message"]
    assert "unavailable" in receipt and "unverified" in receipt
    assert "preserved" in receipt
    assert "re-assign" not in receipt and "deleted" not in receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("visible", [True, False])
async def test_confirmed_idle_observed_box_keeps_correct_recovery(visible):
    provider = MagicMock(supports_direct_status_probe=True)
    provider.get_status.side_effect = [TerminalStatus.IDLE, TerminalStatus.PROCESSING]
    with (
        patch.object(
            ts, "get_terminal_metadata", return_value={"tmux_session": "s", "tmux_window": "w"}
        ),
        patch.object(ts, "get_backend"),
        patch.object(ts, "wait_until_status", new=AsyncMock(return_value=False)),
        patch.object(
            ts, "get_output", return_value="Synthetic task" if visible else "empty prompt"
        ),
        patch.object(ts, "send_input") as send,
        patch.object(ts, "send_special_key") as key,
    ):
        result = await ts._confirm_worker_started_or_resubmit(
            "worker", "Synthetic task", None, None, None, provider
        )
    assert result is True
    assert send.call_count == (0 if visible else 1)
    assert key.call_count == (1 if visible else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [TerminalStatus.PROCESSING, TerminalStatus.COMPLETED, TerminalStatus.WAITING_USER_ANSWER],
)
async def test_started_worker_skips_unavailable_box_capture(status):
    provider = MagicMock(supports_direct_status_probe=True)
    provider.get_status.return_value = status
    with (
        patch.object(
            ts, "get_terminal_metadata", return_value={"tmux_session": "s", "tmux_window": "w"}
        ),
        patch.object(ts, "get_backend"),
        patch.object(ts, "wait_until_status", new=AsyncMock(return_value=False)),
        patch.object(ts, "get_output", side_effect=OSError("capture unavailable")) as output,
        patch.object(ts, "send_input") as send,
        patch.object(ts, "send_special_key") as key,
    ):
        result = await ts._confirm_worker_started_or_resubmit(
            "worker", "Synthetic task", None, None, None, provider
        )
    assert result is True
    output.assert_not_called()
    send.assert_not_called()
    key.assert_not_called()
