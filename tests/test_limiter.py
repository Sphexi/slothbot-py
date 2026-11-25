import json
import logging
from pathlib import Path

import pytest

from slothbot.counter import PAST_BUCKETS, ChannelKey
from slothbot.limiter import (
    FORBIDDEN_RETRY_SECONDS,
    MAX_SLOWMODE_SECONDS,
    ChannelGone,
    ChannelUnavailable,
    Limiter,
    SlowmodeForbidden,
    compute_slowmode,
)
from slothbot.rate import Rate
from support import CHANNEL, GUILD, OTHER_CHANNEL, OTHER_GUILD, fill

LIMIT = Rate.per_minute(60)
KEY = ChannelKey(GUILD, CHANNEL)
OTHER_KEY = ChannelKey(GUILD, OTHER_CHANNEL)
FOREIGN_KEY = ChannelKey(OTHER_GUILD, CHANNEL)
MESSAGES_PER_BUCKET = 30
SLOWMODE_FOR_ONE_NEWEST_BUCKET = 9


def window(counts_by_index: dict[int, int]) -> list[Rate]:
    """Newest-first ten-second buckets with the given counts set and the rest zero."""
    return [Rate.per(10, counts_by_index.get(i, 0)) for i in range(PAST_BUCKETS)]


def limit_entry(channel: int, count: int, seconds: int) -> dict:
    return {
        "channel": {"channelId": str(channel), "guildId": str(GUILD)},
        "limit": {"count": count, "seconds": seconds},
    }


@pytest.fixture
def limits_path(tmp_path: Path) -> Path:
    return tmp_path / "limits.json"


@pytest.fixture
def limiter(counter, set_slowmode, limits_path, clock) -> Limiter:
    return Limiter(counter, set_slowmode, limits_path, clock)


def test_compute_slowmode_weights_recent_overshoot_and_truncates():
    # Limit is 1 msg/s.
    # i0: 30 msgs/10s -> ratio 3.0 * (30 - 0) / 10 = 9.0
    # i1: 10 msgs/10s equals the limit, so it is excluded.
    # i10: 20 msgs -> ratio 2.0 * (30 - 10) / 10 = 4.0
    # i29: 15 msgs -> ratio 1.5 * (30 - 29) / 10 = 0.15; total 13.15 truncates to 13.
    buckets = window({0: 30, 1: 10, 10: 20, 29: 15})

    assert compute_slowmode(buckets, LIMIT) == 13


def test_compute_slowmode_is_zero_when_no_bucket_exceeds_the_limit():
    assert compute_slowmode(window({0: 10, 5: 3}), LIMIT) == 0


def test_compute_slowmode_is_clamped_to_the_discord_maximum():
    flood = window(dict.fromkeys(range(PAST_BUCKETS), 1000))

    assert compute_slowmode(flood, Rate(1, 60)) == MAX_SLOWMODE_SECONDS


async def test_tick_writes_the_computed_slowmode_for_a_limited_channel(
    limiter, counter, set_slowmode
):
    await limiter.set(KEY, LIMIT)
    fill(counter, CHANNEL, MESSAGES_PER_BUCKET)
    counter.rotate()

    await limiter.tick()

    # One newest bucket of 30 msgs/10s against 1 msg/s: ratio 3.0 * 30 / 10 = 9.
    assert set_slowmode.calls == [(KEY, SLOWMODE_FOR_ONE_NEWEST_BUCKET)]


async def test_tick_skips_the_write_when_the_value_is_unchanged(limiter, counter, set_slowmode):
    await limiter.set(KEY, LIMIT)
    fill(counter, CHANNEL, MESSAGES_PER_BUCKET)
    counter.rotate()

    await limiter.tick()
    await limiter.tick()

    assert len(set_slowmode.calls) == 1


async def test_failed_write_does_not_stop_other_channels(limiter, counter, set_slowmode):
    await limiter.set(KEY, LIMIT)
    await limiter.set(OTHER_KEY, LIMIT)
    fill(counter, CHANNEL, MESSAGES_PER_BUCKET)
    fill(counter, OTHER_CHANNEL, MESSAGES_PER_BUCKET)
    counter.rotate()
    set_slowmode.raises = {CHANNEL: RuntimeError()}

    await limiter.tick()

    assert set_slowmode.calls == [(OTHER_KEY, SLOWMODE_FOR_ONE_NEWEST_BUCKET)]


async def test_failed_write_is_logged_with_the_channel_id(limiter, counter, set_slowmode, caplog):
    await limiter.set(KEY, LIMIT)
    fill(counter, CHANNEL, MESSAGES_PER_BUCKET)
    counter.rotate()
    set_slowmode.raises = {CHANNEL: RuntimeError()}

    with caplog.at_level(logging.WARNING):
        await limiter.tick()

    assert str(CHANNEL) in caplog.text


async def test_failed_write_is_not_cached_so_the_next_tick_retries(limiter, counter, set_slowmode):
    await limiter.set(KEY, LIMIT)
    fill(counter, CHANNEL, MESSAGES_PER_BUCKET)
    counter.rotate()
    set_slowmode.raises = {CHANNEL: RuntimeError()}
    await limiter.tick()
    set_slowmode.raises = {}

    await limiter.tick()

    assert set_slowmode.calls == [(KEY, SLOWMODE_FOR_ONE_NEWEST_BUCKET)]


async def test_clear_writes_zero_immediately_and_forgets_the_limit(limiter, set_slowmode):
    await limiter.set(KEY, LIMIT)

    await limiter.clear(KEY)

    assert set_slowmode.calls == [(KEY, 0)]
    assert limiter.get(KEY) is None


async def test_cleared_channel_is_no_longer_ticked(limiter, counter, set_slowmode):
    await limiter.set(KEY, LIMIT)
    fill(counter, CHANNEL, MESSAGES_PER_BUCKET)
    counter.rotate()
    await limiter.clear(KEY)
    set_slowmode.calls.clear()

    await limiter.tick()

    assert set_slowmode.calls == []


async def test_for_guild_returns_only_that_guilds_channels(limiter):
    await limiter.set(KEY, LIMIT)
    await limiter.set(ChannelKey(OTHER_GUILD, OTHER_CHANNEL), LIMIT)

    assert limiter.for_guild(GUILD) == {CHANNEL: LIMIT}


@pytest.mark.parametrize("rate", [Rate(0, 60), Rate(5, 0)], ids=["zero-count", "zero-seconds"])
async def test_set_rejects_a_rate_that_cannot_be_divided(limiter, rate):
    with pytest.raises(ValueError, match="positive"):
        await limiter.set(KEY, rate)

    assert limiter.get(KEY) is None


async def test_limits_persist_in_the_on_disk_shape_and_reload(
    limiter, counter, set_slowmode, limits_path
):
    await limiter.set(KEY, LIMIT)

    reloaded = Limiter(counter, set_slowmode, limits_path)

    assert json.loads(limits_path.read_text()) == [limit_entry(CHANNEL, 60, 60)]
    assert reloaded.get(KEY) == LIMIT


def test_load_skips_unusable_limits_and_keeps_valid_ones(counter, set_slowmode, limits_path):
    entries = [limit_entry(1, 0, 60), limit_entry(2, 5, 0), limit_entry(CHANNEL, 60, 60)]
    limits_path.write_text(json.dumps(entries))

    loaded = Limiter(counter, set_slowmode, limits_path)

    assert loaded.for_guild(GUILD) == {CHANNEL: LIMIT}


def test_limiter_from_missing_file_has_no_limits(limiter):
    assert limiter.for_guild(GUILD) == {}


@pytest.mark.parametrize(
    ("count", "seconds", "expected"),
    [(5, 60, None), (6, 60, Rate(6, 60)), (1, 10, Rate(1, 10)), (1, 11, None)],
    ids=["below-floor", "at-floor", "at-floor-short-window", "below-floor-long-window"],
)
def test_load_skips_limits_below_the_floor_and_keeps_valid_entries(
    counter, set_slowmode, limits_path, count, seconds, expected
):
    candidate = 7
    entries = [limit_entry(candidate, count, seconds), limit_entry(CHANNEL, 60, 60)]
    limits_path.write_text(json.dumps(entries))

    loaded = Limiter(counter, set_slowmode, limits_path)

    assert loaded.get(ChannelKey(GUILD, candidate)) == expected
    assert loaded.get(KEY) == LIMIT


@pytest.mark.parametrize("victim", [OTHER_KEY, KEY], ids=["later-channel", "same-channel"])
async def test_tick_leaves_slowmode_off_for_a_limit_cleared_while_the_tick_awaits(
    limiter, counter, set_slowmode, victim
):
    await limiter.set(KEY, LIMIT)
    await limiter.set(OTHER_KEY, LIMIT)
    fill(counter, CHANNEL, MESSAGES_PER_BUCKET)
    fill(counter, OTHER_CHANNEL, MESSAGES_PER_BUCKET)
    counter.rotate()
    # The tick's first write is for CHANNEL; the clear lands while that write is awaited.
    set_slowmode.during[CHANNEL] = lambda: limiter.clear(victim)

    await limiter.tick()

    assert set_slowmode.current[victim.channel_id] == 0
    assert limiter.get(victim) is None


async def _limited_channel_with_traffic(limiter, counter) -> None:
    await limiter.set(KEY, LIMIT)
    fill(counter, CHANNEL, MESSAGES_PER_BUCKET)
    counter.rotate()


async def test_channel_gone_drops_the_limit_and_persists_the_removal(
    limiter, counter, set_slowmode, limits_path
):
    await _limited_channel_with_traffic(limiter, counter)
    set_slowmode.raises = {CHANNEL: ChannelGone()}

    await limiter.tick()

    assert limiter.get(KEY) is None
    assert json.loads(limits_path.read_text()) == []


async def test_channel_unavailable_keeps_the_limit_and_retries_on_the_next_tick(
    limiter, counter, set_slowmode
):
    await _limited_channel_with_traffic(limiter, counter)
    set_slowmode.raises = {CHANNEL: ChannelUnavailable()}
    await limiter.tick()
    set_slowmode.raises = {}

    await limiter.tick()

    assert limiter.get(KEY) == LIMIT
    assert set_slowmode.calls == [(KEY, SLOWMODE_FOR_ONE_NEWEST_BUCKET)]


async def test_forbidden_suppresses_further_attempts_within_the_retry_window(
    limiter, counter, set_slowmode, clock
):
    await _limited_channel_with_traffic(limiter, counter)
    set_slowmode.raises = {CHANNEL: SlowmodeForbidden()}
    await limiter.tick()
    clock.now += FORBIDDEN_RETRY_SECONDS - 1

    await limiter.tick()

    assert len(set_slowmode.attempts) == 1
    assert limiter.get(KEY) == LIMIT


async def test_forbidden_is_retried_once_the_retry_window_has_passed(
    limiter, counter, set_slowmode, clock
):
    await _limited_channel_with_traffic(limiter, counter)
    set_slowmode.raises = {CHANNEL: SlowmodeForbidden()}
    await limiter.tick()
    set_slowmode.raises = {}
    clock.now += FORBIDDEN_RETRY_SECONDS

    await limiter.tick()

    assert set_slowmode.calls == [(KEY, SLOWMODE_FOR_ONE_NEWEST_BUCKET)]


async def test_set_lifts_an_active_suppression(limiter, counter, set_slowmode):
    await _limited_channel_with_traffic(limiter, counter)
    set_slowmode.raises = {CHANNEL: SlowmodeForbidden()}
    await limiter.tick()
    set_slowmode.raises = {}
    await limiter.set(KEY, LIMIT)

    await limiter.tick()

    assert set_slowmode.calls == [(KEY, SLOWMODE_FOR_ONE_NEWEST_BUCKET)]


async def test_clear_lifts_an_active_suppression(limiter, counter, set_slowmode):
    await _limited_channel_with_traffic(limiter, counter)
    set_slowmode.raises = {CHANNEL: SlowmodeForbidden()}
    await limiter.tick()
    set_slowmode.raises = {}

    await limiter.clear(KEY)

    assert set_slowmode.calls == [(KEY, 0)]


@pytest.mark.parametrize(
    ("raises", "expected"),
    [({}, True), ({CHANNEL: RuntimeError()}, False), ({CHANNEL: SlowmodeForbidden()}, False)],
    ids=["written", "unexpected-failure", "forbidden"],
)
async def test_clear_reports_whether_the_slowmode_reset_succeeded(
    limiter, set_slowmode, raises, expected
):
    await limiter.set(KEY, LIMIT)
    set_slowmode.raises = raises

    reset = await limiter.clear(KEY)

    assert reset is expected
    assert limiter.get(KEY) is None


async def test_drop_channel_forgets_only_that_channels_limit(
    limiter, counter, set_slowmode, limits_path
):
    await limiter.set(KEY, LIMIT)
    await limiter.set(OTHER_KEY, LIMIT)
    await limiter.set(FOREIGN_KEY, LIMIT)

    limiter.drop_channel(KEY)

    reloaded = Limiter(counter, set_slowmode, limits_path)
    assert limiter.get(KEY) is None
    assert limiter.get(OTHER_KEY) == LIMIT
    assert limiter.get(FOREIGN_KEY) == LIMIT
    assert reloaded.get(KEY) is None
    assert reloaded.get(OTHER_KEY) == LIMIT
    assert set_slowmode.calls == []


async def test_drop_guild_forgets_only_that_guilds_limits(
    limiter, counter, set_slowmode, limits_path
):
    await limiter.set(KEY, LIMIT)
    await limiter.set(OTHER_KEY, LIMIT)
    await limiter.set(FOREIGN_KEY, LIMIT)

    limiter.drop_guild(GUILD)

    reloaded = Limiter(counter, set_slowmode, limits_path)
    assert limiter.for_guild(GUILD) == {}
    assert limiter.for_guild(OTHER_GUILD) == {CHANNEL: LIMIT}
    assert reloaded.for_guild(OTHER_GUILD) == {CHANNEL: LIMIT}
    assert set_slowmode.calls == []


async def test_failed_save_leaves_the_limit_out_of_memory(counter, set_slowmode, unwritable_path):
    limiter = Limiter(counter, set_slowmode, unwritable_path)

    with pytest.raises(OSError):
        await limiter.set(KEY, LIMIT)

    assert limiter.get(KEY) is None
