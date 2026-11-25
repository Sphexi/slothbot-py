import pytest

from slothbot.durations import humanize_seconds
from slothbot.rate import Rate


@pytest.mark.parametrize(
    ("rate", "expected"),
    [(Rate(120, 60), 2.0), (Rate(5, 10), 0.5), (Rate(0, 10), 0.0)],
)
def test_per_second_divides_count_by_window(rate, expected):
    assert rate.per_second == expected


@pytest.mark.parametrize(
    ("rate", "other", "expected"),
    [
        (Rate(30, 10), Rate(60, 60), True),
        (Rate(10, 10), Rate(60, 60), False),
        (Rate(5, 10), Rate(60, 60), False),
    ],
)
def test_exceeds_is_strictly_greater_per_second(rate, other, expected):
    assert rate.exceeds(other) is expected


def test_ratio_to_divides_per_second_rates():
    assert Rate(30, 10).ratio_to(Rate(60, 60)) == 3.0


@pytest.mark.parametrize(("count", "seconds"), [(-1, 10), (10, -1)])
def test_negative_values_are_rejected(count, seconds):
    with pytest.raises(ValueError, match=">= 0"):
        Rate(count, seconds)


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        (Rate.per_minute(120), "120 per 1 minute"),
        (Rate.per(10, 5), "5 per 10 seconds"),
    ],
)
def test_humanize_states_count_and_window_in_words(rate, expected):
    assert rate.humanize() == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (1, "1 second"),
        (90, "1 minute 30 seconds"),
        (3600, "1 hour"),
        (3725, "1 hour 2 minutes 5 seconds"),
        (86400, "1 day"),
    ],
)
def test_humanize_seconds_drops_empty_outer_units(seconds, expected):
    assert humanize_seconds(seconds) == expected
