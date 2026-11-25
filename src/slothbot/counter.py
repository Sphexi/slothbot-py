"""Per-channel message counting in rotating fixed-width buckets."""

import logging
from collections import Counter
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import NamedTuple

from slothbot.rate import Rate
from slothbot.storage import RoleSet, read_json_list, write_json

log = logging.getLogger(__name__)

BUCKET_SECONDS = 10
PAST_BUCKETS = 30


class ChannelKey(NamedTuple):
    guild_id: int
    channel_id: int


class MessageCounter:
    """Counts qualifying messages per channel and keeps a short history of buckets."""

    def __init__(self, path: Path, ignored_roles: RoleSet) -> None:
        self._path = path
        self._ignored_roles = ignored_roles
        self._counting_bots: dict[int, bool] = {}
        self._current: Counter[ChannelKey] = Counter()
        self._past: list[dict[ChannelKey, int]] = []
        self._load()

    def should_count(
        self, guild_id: int | None, is_bot: bool, author_role_ids: Collection[int]
    ) -> bool:
        if guild_id is None:
            return False
        if is_bot and not self.is_counting_bots(guild_id):
            return False
        return self._ignored_roles.get(guild_id).isdisjoint(author_role_ids)

    def record(
        self,
        guild_id: int | None,
        channel_id: int,
        is_bot: bool,
        author_role_ids: Collection[int],
    ) -> bool:
        """Count a message if it qualifies; return whether it was counted."""
        if guild_id is None or not self.should_count(guild_id, is_bot, author_role_ids):
            return False
        self._current[ChannelKey(guild_id, channel_id)] += 1
        return True

    def rotate(self) -> None:
        """Close the in-flight bucket and push it onto the history."""
        snapshot = dict(self._current)
        self._current = Counter()
        self._past = [snapshot, *self._past][:PAST_BUCKETS]

    def buckets(self, key: ChannelKey) -> list[Rate]:
        """Newest-first rates for the channel, always PAST_BUCKETS long."""
        counts = [bucket.get(key, 0) for bucket in self._past]
        counts += [0] * (PAST_BUCKETS - len(counts))
        return [Rate.per(BUCKET_SECONDS, count) for count in counts]

    def total_counts(self, guild_id: int) -> dict[int, int]:
        """Retained message counts per channel id for one guild."""
        totals: Counter[int] = Counter()
        for bucket in self._past:
            for key, count in bucket.items():
                if key.guild_id == guild_id:
                    totals[key.channel_id] += count
        return dict(totals)

    def clear(self, key: ChannelKey) -> None:
        """Drop the channel from the in-flight bucket and every retained bucket."""
        self._current.pop(key, None)
        for bucket in self._past:
            bucket.pop(key, None)

    def clear_guild(self, guild_id: int) -> None:
        """Drop every channel of the guild from the in-flight and retained buckets."""
        for counts in (self._current, *self._past):
            for key in [k for k in counts if k.guild_id == guild_id]:
                del counts[key]

    def is_counting_bots(self, guild_id: int) -> bool:
        return self._counting_bots.get(guild_id, False)

    def set_counting_bots(self, guild_id: int, value: bool) -> None:
        updated = {**self._counting_bots, guild_id: value}
        self._save(updated)
        self._counting_bots = updated

    def _save(self, counting_bots: Mapping[int, bool]) -> None:
        payload = [
            {"guild_id": str(guild_id), "count_bot_msgs": value}
            for guild_id, value in sorted(counting_bots.items())
        ]
        write_json(self._path, payload)

    def _load(self) -> None:
        for entry in read_json_list(self._path):
            try:
                guild_id = int(entry["guild_id"])
                value = entry["count_bot_msgs"]
            except (KeyError, TypeError, ValueError):
                log.warning("Skipping malformed entry in %s", self._path)
                continue
            if isinstance(value, bool):
                self._counting_bots[guild_id] = value
