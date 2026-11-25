import pytest

from slothbot.commands import UserError, _fit_lines, format_buckets, parse_role_id
from slothbot.rate import Rate

ROLE_ID = 123456789
BUCKET_COUNTS = list(range(12))
FIT_LIMIT = 40
OMITTED_LINE = "…and {count} more"
BUCKET_GRID = (
    "Buckets:   0s 10s 20s 30s 40s 50s\n"
    "      0m   0   1   2   3   4   5\n"
    "      1m   6   7   8   9  10  11"
)


@pytest.mark.parametrize("text", [str(ROLE_ID), f"<@&{ROLE_ID}>", f"  {ROLE_ID} "])
def test_parse_role_id_accepts_ids_and_mentions(text):
    assert parse_role_id(text) == ROLE_ID


@pytest.mark.parametrize("text", ["", "admins", f"<@{ROLE_ID}>", "12 34"])
def test_parse_role_id_rejects_anything_else(text):
    with pytest.raises(UserError):
        parse_role_id(text)


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (None, "Channel is not rate limited"),
        (Rate.per_minute(120), "Limit: 120 per 1 minute (20.0 per 10s)"),
    ],
)
def test_format_buckets_opens_with_the_limit_line(limit, expected):
    text = format_buckets(limit, [Rate.per(10, 0)])

    assert text.split("\n")[1] == expected


def test_format_buckets_lays_counts_out_six_to_a_row_with_minute_labels():
    buckets = [Rate.per(10, count) for count in BUCKET_COUNTS]

    text = format_buckets(None, buckets)

    assert text.endswith(BUCKET_GRID + "```")


@pytest.mark.parametrize(
    ("lines", "expected"),
    [([], ""), (["a", "b"], "a\nb"), (["x" * FIT_LIMIT], "x" * FIT_LIMIT)],
    ids=["empty", "short", "exactly-at-limit"],
)
def test_fit_lines_returns_the_joined_text_when_it_fits(lines, expected):
    assert _fit_lines(lines, FIT_LIMIT) == expected


def test_fit_lines_truncates_to_the_budget_and_counts_the_omitted_lines():
    lines = [f"line{index:02d}" for index in range(10)]

    result = _fit_lines(lines, FIT_LIMIT)

    *kept, remainder = result.split("\n")
    assert len(result) <= FIT_LIMIT
    assert kept == lines[: len(kept)]
    assert remainder == OMITTED_LINE.format(count=len(lines) - len(kept))
    assert kept != []


def test_fit_lines_reports_every_line_omitted_when_none_fit():
    lines = ["x" * FIT_LIMIT * 2]

    result = _fit_lines(lines, FIT_LIMIT)

    assert result == OMITTED_LINE.format(count=1)
