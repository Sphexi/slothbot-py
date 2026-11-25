import os

import pytest

from slothbot.healthcheck import MAX_AGE_SECONDS, check

HEARTBEAT_MTIME = 1_000_000


@pytest.fixture
def heartbeat(tmp_path):
    path = tmp_path / "heartbeat"
    path.write_text("")
    os.utime(path, (HEARTBEAT_MTIME, HEARTBEAT_MTIME))
    return path


@pytest.mark.parametrize("age", [0, MAX_AGE_SECONDS])
def test_check_passes_within_the_freshness_window(heartbeat, age):
    assert check(heartbeat, now=HEARTBEAT_MTIME + age) is None


def test_check_fails_once_the_heartbeat_is_older_than_the_window(heartbeat):
    reason = check(heartbeat, now=HEARTBEAT_MTIME + MAX_AGE_SECONDS + 1)

    assert reason is not None
    assert "old" in reason


def test_check_fails_when_the_heartbeat_file_is_absent(tmp_path):
    reason = check(tmp_path / "absent", now=HEARTBEAT_MTIME)

    assert reason is not None
    assert "missing" in reason
