"""心跳循环退出与暂时失败测试，不调用模型或数据库。"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.multiturn.heartbeat import start_generation_heartbeat


@pytest.mark.asyncio
async def test_heartbeat_retries_transient_storage_error_then_stops_when_turn_ends(monkeypatch) -> None:
    """数据库暂时失败只重试心跳，轮次终态使循环正常退出。"""
    pulse = AsyncMock(side_effect=[RuntimeError("暂时不可用"), True, False])
    monkeypatch.setattr("app.services.multiturn.heartbeat.pulse_generation", pulse)
    heartbeat = start_generation_heartbeat(Mock(), 1, 2, 3, interval=0.001)
    await asyncio.wait_for(heartbeat, 1)
    assert pulse.await_count == 3 and heartbeat.done()


@pytest.mark.asyncio
async def test_cancelled_heartbeat_does_not_leave_background_activity(monkeypatch) -> None:
    """关闭生成器时可取消等待中的心跳，不把取消误作需重试的错误。"""
    entered = asyncio.Event()

    async def pulse(*args, **kwargs):
        """标记已更新一次，随后让循环等待下一次定时更新。"""
        entered.set()
        return True

    monkeypatch.setattr("app.services.multiturn.heartbeat.pulse_generation", pulse)
    heartbeat = start_generation_heartbeat(Mock(), 1, 2, 3)
    await asyncio.wait_for(entered.wait(), 1)
    heartbeat.cancel()
    with pytest.raises(asyncio.CancelledError):
        await heartbeat
    assert heartbeat.cancelled()
