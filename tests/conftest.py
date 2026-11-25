from pathlib import Path

import pytest

from slothbot.counter import MessageCounter
from slothbot.storage import RoleSet
from support import GUILD, IGNORED_ROLE, FakeClock, FakeSlowmode


@pytest.fixture
def ignored_roles(tmp_path: Path) -> RoleSet:
    roles = RoleSet(tmp_path / "ignored_roles.json")
    roles.add(GUILD, IGNORED_ROLE)
    return roles


@pytest.fixture
def counter(tmp_path: Path, ignored_roles: RoleSet) -> MessageCounter:
    return MessageCounter(tmp_path / "counter.json", ignored_roles)


@pytest.fixture
def set_slowmode() -> FakeSlowmode:
    return FakeSlowmode()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def unwritable_path(tmp_path: Path) -> Path:
    """A state path whose parent is a regular file, so every write raises OSError."""
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    return blocker / "state.json"
