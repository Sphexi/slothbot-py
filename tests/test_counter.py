import json

import pytest

from slothbot.counter import PAST_BUCKETS, ChannelKey, MessageCounter
from slothbot.rate import Rate
from support import (
    CHANNEL,
    GUILD,
    IGNORED_ROLE,
    OTHER_CHANNEL,
    OTHER_GUILD,
    OTHER_ROLE,
    fill,
)

KEY = ChannelKey(GUILD, CHANNEL)
OTHER_KEY = ChannelKey(GUILD, OTHER_CHANNEL)


@pytest.mark.parametrize(
    ("guild_id", "is_bot", "counting_bots", "role_ids", "expected"),
    [
        (None, False, False, (), False),
        (GUILD, False, False, (), True),
        (GUILD, False, False, (IGNORED_ROLE,), False),
        (GUILD, False, False, (IGNORED_ROLE, OTHER_ROLE), False),
        (GUILD, False, False, (OTHER_ROLE,), True),
        (GUILD, True, False, (), False),
        (GUILD, True, True, (), True),
        (GUILD, True, True, (IGNORED_ROLE,), False),
        (OTHER_GUILD, False, False, (IGNORED_ROLE,), True),
    ],
    ids=[
        "no-guild",
        "human",
        "ignored-role",
        "ignored-and-other-role",
        "other-role-only",
        "bot-flag-off",
        "bot-flag-on",
        "bot-flag-on-ignored-role",
        "guild-without-ignored-roles",
    ],
)
def test_should_count_applies_guild_bot_and_role_rules(
    counter, guild_id, is_bot, counting_bots, role_ids, expected
):
    counter.set_counting_bots(GUILD, counting_bots)

    result = counter.should_count(guild_id, is_bot, role_ids)

    assert result is expected


def test_record_reports_whether_the_message_was_counted(counter):
    counted = counter.record(GUILD, CHANNEL, is_bot=False, author_role_ids=())
    skipped = counter.record(GUILD, CHANNEL, is_bot=True, author_role_ids=())

    assert (counted, skipped) == (True, False)


def test_buckets_are_zero_padded_to_the_full_history_length(counter):
    fill(counter, CHANNEL, 3)
    counter.rotate()

    buckets = counter.buckets(KEY)

    assert len(buckets) == PAST_BUCKETS
    assert buckets[0] == Rate.per(10, 3)
    assert set(buckets[1:]) == {Rate.per(10, 0)}


def test_buckets_list_newest_first(counter):
    fill(counter, CHANNEL, 1)
    counter.rotate()
    fill(counter, CHANNEL, 2)
    counter.rotate()

    counts = [bucket.count for bucket in counter.buckets(KEY)[:3]]

    assert counts == [2, 1, 0]


def test_rotation_discards_buckets_beyond_the_retention_cap(counter):
    fill(counter, CHANNEL, 1)
    counter.rotate()

    for _ in range(PAST_BUCKETS):
        counter.rotate()

    assert counter.total_counts(GUILD) == {}
    assert len(counter.buckets(KEY)) == PAST_BUCKETS


def test_total_counts_sums_each_channel_across_buckets(counter):
    fill(counter, CHANNEL, 2)
    fill(counter, OTHER_CHANNEL, 1)
    counter.rotate()
    fill(counter, CHANNEL, 3)
    counter.rotate()

    assert counter.total_counts(GUILD) == {CHANNEL: 5, OTHER_CHANNEL: 1}


def test_clear_drops_only_the_named_channel_from_every_bucket(counter):
    fill(counter, CHANNEL, 2)
    fill(counter, OTHER_CHANNEL, 4)
    counter.rotate()
    fill(counter, CHANNEL, 2)
    counter.rotate()

    counter.clear(KEY)

    assert counter.total_counts(GUILD) == {OTHER_CHANNEL: 4}


def test_bot_counting_flag_defaults_off_and_persists_in_the_on_disk_shape(tmp_path, ignored_roles):
    path = tmp_path / "counter.json"
    first = MessageCounter(path, ignored_roles)
    default = first.is_counting_bots(GUILD)

    first.set_counting_bots(GUILD, True)
    reloaded = MessageCounter(path, ignored_roles)

    assert default is False
    assert json.loads(path.read_text()) == [{"guild_id": str(GUILD), "count_bot_msgs": True}]
    assert reloaded.is_counting_bots(GUILD) is True


def test_total_counts_keeps_guilds_separate_including_a_shared_channel_id(counter):
    fill(counter, CHANNEL, 2, GUILD)
    fill(counter, CHANNEL, 3, OTHER_GUILD)
    fill(counter, OTHER_CHANNEL, 4, OTHER_GUILD)
    counter.rotate()

    totals = (counter.total_counts(GUILD), counter.total_counts(OTHER_GUILD))

    assert totals == ({CHANNEL: 2}, {CHANNEL: 3, OTHER_CHANNEL: 4})


def test_buckets_keep_a_shared_channel_id_separate_per_guild(counter):
    fill(counter, CHANNEL, 2, GUILD)
    fill(counter, CHANNEL, 3, OTHER_GUILD)
    counter.rotate()

    newest = (
        counter.buckets(KEY)[0].count,
        counter.buckets(ChannelKey(OTHER_GUILD, CHANNEL))[0].count,
    )

    assert newest == (2, 3)


def test_clear_leaves_the_same_channel_id_in_another_guild(counter):
    fill(counter, CHANNEL, 2, GUILD)
    fill(counter, CHANNEL, 3, OTHER_GUILD)
    counter.rotate()

    counter.clear(KEY)

    assert counter.total_counts(GUILD) == {}
    assert counter.total_counts(OTHER_GUILD) == {CHANNEL: 3}


def test_clear_drops_the_in_flight_bucket_so_counts_do_not_reappear_on_rotation(counter):
    fill(counter, CHANNEL, 2)

    counter.clear(KEY)
    counter.rotate()

    assert counter.total_counts(GUILD) == {}


def test_clear_guild_drops_that_guilds_in_flight_and_retained_counts_only(counter):
    fill(counter, CHANNEL, 2, GUILD)
    fill(counter, CHANNEL, 3, OTHER_GUILD)
    counter.rotate()
    fill(counter, OTHER_CHANNEL, 1, GUILD)
    fill(counter, OTHER_CHANNEL, 5, OTHER_GUILD)

    counter.clear_guild(GUILD)
    counter.rotate()

    assert counter.total_counts(GUILD) == {}
    assert counter.total_counts(OTHER_GUILD) == {CHANNEL: 3, OTHER_CHANNEL: 5}


def test_failed_save_leaves_the_bot_counting_flag_unchanged(tmp_path, ignored_roles):
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    unwritable = MessageCounter(blocker / "counter.json", ignored_roles)

    with pytest.raises(OSError):
        unwritable.set_counting_bots(GUILD, True)

    assert unwritable.is_counting_bots(GUILD) is False
