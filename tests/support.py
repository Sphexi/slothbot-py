"""Shared constants and fakes for the test suite."""

import asyncio
from collections.abc import Awaitable, Callable

from slothbot.counter import ChannelKey, MessageCounter

GUILD = 1
OTHER_GUILD = 2
CHANNEL = 100
OTHER_CHANNEL = 200
IGNORED_ROLE = 10
OTHER_ROLE = 20


class FakeSlowmode:
    """Injected slowmode setter that records writes and can raise or interleave on demand."""

    def __init__(self) -> None:
        self.calls: list[tuple[ChannelKey, int]] = []
        self.attempts: list[tuple[ChannelKey, int]] = []
        self.current: dict[int, int] = {}
        self.raises: dict[int, Exception] = {}
        self.during: dict[int, Callable[[], Awaitable[None]]] = {}

    async def __call__(self, key: ChannelKey, seconds: int) -> None:
        self.attempts.append((key, seconds))
        error = self.raises.get(key.channel_id)
        if error is not None:
            raise error
        self.calls.append((key, seconds))
        self.current[key.channel_id] = seconds
        # The write has landed; yield so other tasks run before the caller resumes.
        await asyncio.sleep(0)
        hook = self.during.pop(key.channel_id, None)
        if hook is not None:
            await hook()


class FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def fill(counter: MessageCounter, channel_id: int, count: int, guild_id: int = GUILD) -> None:
    """Record `count` human messages in the channel."""
    for _ in range(count):
        counter.record(guild_id, channel_id, is_bot=False, author_role_ids=())
