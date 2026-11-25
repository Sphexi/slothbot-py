import json
from pathlib import Path

import pytest

from slothbot.storage import RoleSet, read_json_list, write_json
from support import GUILD, IGNORED_ROLE, OTHER_GUILD, OTHER_ROLE


@pytest.fixture
def roles(tmp_path: Path) -> RoleSet:
    return RoleSet(tmp_path / "moderator.json")


def test_read_json_list_returns_empty_when_file_is_missing(tmp_path):
    assert read_json_list(tmp_path / "absent.json") == []


@pytest.mark.parametrize("content", ["not json", '{"a": 1}'])
def test_read_json_list_moves_unusable_content_aside_and_returns_empty(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content)

    result = read_json_list(path)

    assert result == []
    assert not path.exists()
    assert (tmp_path / "bad.json.corrupt").read_text() == content


def test_write_json_failure_propagates(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")

    with pytest.raises(OSError):
        write_json(blocker / "child.json", [])


def test_write_json_leaves_the_original_intact_when_the_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("[1]")

    def fail_replace(src, dst):
        raise PermissionError("replace refused")

    monkeypatch.setattr("slothbot.storage.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace refused"):
        write_json(path, [2])

    assert path.read_text() == "[1]"
    assert list(tmp_path.iterdir()) == [path]


def test_role_set_failed_save_leaves_the_role_out_of_memory(unwritable_path):
    unwritable = RoleSet(unwritable_path)

    with pytest.raises(OSError):
        unwritable.add(GUILD, IGNORED_ROLE)

    assert not unwritable.contains(GUILD, IGNORED_ROLE)


def test_role_set_add_reports_whether_the_role_was_new(roles):
    first = roles.add(GUILD, IGNORED_ROLE)
    second = roles.add(GUILD, IGNORED_ROLE)

    assert (first, second) == (True, False)


def test_role_set_remove_reports_whether_the_role_was_present(roles):
    roles.add(GUILD, IGNORED_ROLE)

    first = roles.remove(GUILD, IGNORED_ROLE)
    second = roles.remove(GUILD, IGNORED_ROLE)

    assert (first, second) == (True, False)


def test_role_set_scopes_roles_to_their_guild(roles):
    roles.add(GUILD, IGNORED_ROLE)

    assert roles.contains(GUILD, IGNORED_ROLE)
    assert not roles.contains(OTHER_GUILD, IGNORED_ROLE)
    assert roles.get(OTHER_GUILD) == frozenset()


def test_role_set_persists_in_the_on_disk_shape_and_reloads(tmp_path):
    path = tmp_path / "moderator.json"
    first = RoleSet(path)
    first.add(GUILD, OTHER_ROLE)
    first.add(GUILD, IGNORED_ROLE)

    reloaded = RoleSet(path)

    assert json.loads(path.read_text()) == [
        {"guild": str(GUILD), "roles": [str(IGNORED_ROLE), str(OTHER_ROLE)]}
    ]
    assert reloaded.get(GUILD) == {IGNORED_ROLE, OTHER_ROLE}


def test_role_set_removing_the_last_role_drops_the_guild_entry(tmp_path):
    path = tmp_path / "moderator.json"
    roles = RoleSet(path)
    roles.add(GUILD, IGNORED_ROLE)

    roles.remove(GUILD, IGNORED_ROLE)

    assert json.loads(path.read_text()) == []


def test_role_set_from_missing_file_is_empty(roles):
    assert roles.get(GUILD) == frozenset()


def test_role_set_skips_malformed_entries_on_load(tmp_path):
    path = tmp_path / "moderator.json"
    valid = {"guild": str(GUILD), "roles": [str(OTHER_ROLE)]}
    path.write_text(json.dumps([{"guild": "x"}, valid]))

    loaded = RoleSet(path)

    assert loaded.get(GUILD) == {OTHER_ROLE}
