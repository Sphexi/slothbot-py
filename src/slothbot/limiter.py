"""Derives per-channel slowmode from recent message volume and applies it."""

import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence, Set
from pathlib import Path

from slothbot.counter import PAST_BUCKETS, ChannelKey, MessageCounter
from slothbot.rate import Rate
from slothbot.storage import read_json_list, write_json

log = logging.getLogger(__name__)

MAX_SLOWMODE_SECONDS = 21600
MIN_LIMIT_PER_MINUTE = 6
FORBIDDEN_RETRY_SECONDS = 600.0


class ChannelGone(Exception):
    """The channel no longer exists, in a guild the bot can still see."""


class ChannelUnavailable(Exception):
    """The channel cannot be resolved right now, so its state is unknown."""


class SlowmodeForbidden(Exception):
    """The bot lacks permission to change the channel's slowmode."""


SetSlowmode = Callable[[ChannelKey, int], Awaitable[None]]


def compute_slowmode(buckets: Sequence[Rate], limit: Rate) -> int:
    """Slowmode seconds for newest-first buckets against a limit."""
    # Each over-limit bucket contributes the ratio of its rate to the limit, whatever the
    # margin, weighted from PAST_BUCKETS/10 for the newest bucket down to 1/10 for the oldest.
    total = sum(
        bucket.ratio_to(limit) * (PAST_BUCKETS - index) / 10.0
        for index, bucket in enumerate(buckets)
        if bucket.exceeds(limit)
    )
    # Discord rejects rate_limit_per_user above 21600.
    return min(int(total), MAX_SLOWMODE_SECONDS)


def _is_usable(rate: Rate) -> bool:
    return rate.count > 0 and rate.seconds > 0


def _meets_floor(rate: Rate) -> bool:
    return rate.count * 60 >= MIN_LIMIT_PER_MINUTE * rate.seconds


class Limiter:
    """Holds configured channel limits and keeps channel slowmode in step with traffic."""

    def __init__(
        self,
        counter: MessageCounter,
        set_slowmode: SetSlowmode,
        path: Path,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._counter = counter
        self._set_slowmode = set_slowmode
        self._path = path
        self._clock = clock
        self._limits: dict[ChannelKey, Rate] = {}
        self._written: dict[int, int] = {}
        self._suppressed_until: dict[int, float] = {}
        self._load()

    async def tick(self) -> None:
        """Recompute and apply slowmode for every limited channel."""
        for key, limit in list(self._limits.items()):
            if self._limits.get(key) is not limit:
                continue
            slowmode = compute_slowmode(self._counter.buckets(key), limit)
            await self._apply(key, slowmode)
            if key not in self._limits:
                await self._apply(key, 0)

    async def set(self, key: ChannelKey, rate: Rate) -> None:
        if not _is_usable(rate):
            raise ValueError("rate must have a positive count and window")
        updated = {**self._limits, key: rate}
        self._save(updated)
        self._limits = updated
        self._suppressed_until.pop(key.channel_id, None)

    async def clear(self, key: ChannelKey) -> bool:
        """Forget the channel's limit and reset its slowmode; return whether the reset succeeded."""
        if key in self._limits:
            updated = {k: rate for k, rate in self._limits.items() if k != key}
            self._save(updated)
            self._limits = updated
        self._suppressed_until.pop(key.channel_id, None)
        return await self._apply(key, 0)

    def drop_channel(self, key: ChannelKey) -> None:
        """Forget a deleted channel's limit without touching Discord."""
        self._discard({key})

    def drop_guild(self, guild_id: int) -> None:
        """Forget every limit in a guild the bot has left."""
        self._discard({k for k in self._limits if k.guild_id == guild_id})

    def get(self, key: ChannelKey) -> Rate | None:
        return self._limits.get(key)

    def for_guild(self, guild_id: int) -> dict[int, Rate]:
        return {k.channel_id: rate for k, rate in self._limits.items() if k.guild_id == guild_id}

    async def _apply(self, key: ChannelKey, seconds: int) -> bool:
        """Write the slowmode if it differs from the last write; return whether it is in place."""
        channel_id = key.channel_id
        if self._written.get(channel_id) == seconds:
            return True
        retry_at = self._suppressed_until.get(channel_id)
        if retry_at is not None and self._clock() < retry_at:
            return False
        try:
            await self._set_slowmode(key, seconds)
        except ChannelGone:
            log.info("Channel %s is gone; removing its rate limit", channel_id)
            self._discard({key})
            return True
        except ChannelUnavailable:
            log.info(
                "Channel %s is not reachable right now; leaving its limit in place", channel_id
            )
            return False
        except SlowmodeForbidden:
            self._suppressed_until[channel_id] = self._clock() + FORBIDDEN_RETRY_SECONDS
            log.warning(
                "Missing permission to set slowmode in channel %s; not retrying for %d seconds",
                channel_id,
                FORBIDDEN_RETRY_SECONDS,
            )
            return False
        except Exception:
            log.warning("Could not set slowmode for channel %s", channel_id, exc_info=True)
            return False
        self._written[channel_id] = seconds
        self._suppressed_until.pop(channel_id, None)
        return True

    def _discard(self, doomed: Set[ChannelKey]) -> None:
        """Drop limits and per-channel state; an unsaved removal is repaired by the next save."""
        for key in doomed:
            self._written.pop(key.channel_id, None)
            self._suppressed_until.pop(key.channel_id, None)
        present = doomed & self._limits.keys()
        if not present:
            return
        self._limits = {k: rate for k, rate in self._limits.items() if k not in present}
        try:
            self._save(self._limits)
        except OSError:
            log.warning("Could not save %s after removing limits", self._path, exc_info=True)

    def _save(self, limits: Mapping[ChannelKey, Rate]) -> None:
        payload = [
            {
                "channel": {"channelId": str(k.channel_id), "guildId": str(k.guild_id)},
                "limit": {"count": rate.count, "seconds": rate.seconds},
            }
            for k, rate in sorted(limits.items())
        ]
        write_json(self._path, payload)

    def _load(self) -> None:
        for entry in read_json_list(self._path):
            try:
                key = ChannelKey(
                    int(entry["channel"]["guildId"]), int(entry["channel"]["channelId"])
                )
                rate = Rate(int(entry["limit"]["count"]), int(entry["limit"]["seconds"]))
            except (KeyError, TypeError, ValueError):
                log.warning("Skipping malformed entry in %s", self._path)
                continue
            if not _is_usable(rate):
                continue
            if not _meets_floor(rate):
                log.warning(
                    "Skipping limit below %d per minute for channel %s",
                    MIN_LIMIT_PER_MINUTE,
                    key.channel_id,
                )
                continue
            self._limits[key] = rate
