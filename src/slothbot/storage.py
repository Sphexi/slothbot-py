"""JSON persistence helpers and the guild-to-roles set shared by moderator and ignored roles."""

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _set_aside(path: Path) -> None:
    aside = path.with_name(path.name + ".corrupt")
    try:
        os.replace(path, aside)
    except OSError:
        log.warning("Could not move unparseable %s aside", path, exc_info=True)
    else:
        log.warning("Moved unparseable %s to %s", path, aside)


def read_json_list(path: Path) -> list[Any]:
    """Return the list stored at path; a missing or unreadable file yields an empty list."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.info("No file at %s; one will be created if needed", path)
        return []
    except OSError:
        log.warning("Could not read %s", path, exc_info=True)
        return []
    try:
        data = json.loads(text)
    except ValueError:
        _set_aside(path)
        return []
    if not isinstance(data, list):
        _set_aside(path)
        return []
    return data


def write_json(path: Path, payload: list[Any]) -> None:
    """Atomically replace path with payload; OSError propagates to the caller."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise


class RoleSet:
    """Persisted mapping of guild id to a set of role ids."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._roles: dict[int, set[int]] = {}
        self._load()

    def add(self, guild_id: int, role_id: int) -> bool:
        """Add a role; return False if it was already present."""
        roles = self._roles.get(guild_id, frozenset())
        if role_id in roles:
            return False
        updated = {**self._roles, guild_id: {*roles, role_id}}
        self._save(updated)
        self._roles = updated
        return True

    def remove(self, guild_id: int, role_id: int) -> bool:
        """Remove a role; return False if it was not present."""
        roles = self._roles.get(guild_id, frozenset())
        if role_id not in roles:
            return False
        updated = {g: r for g, r in self._roles.items() if g != guild_id}
        if remaining := roles - {role_id}:
            updated[guild_id] = set(remaining)
        self._save(updated)
        self._roles = updated
        return True

    def contains(self, guild_id: int, role_id: int) -> bool:
        return role_id in self._roles.get(guild_id, ())

    def get(self, guild_id: int) -> frozenset[int]:
        return frozenset(self._roles.get(guild_id, ()))

    def _save(self, roles_by_guild: Mapping[int, set[int]]) -> None:
        payload = [
            {"guild": str(guild_id), "roles": [str(role_id) for role_id in sorted(roles)]}
            for guild_id, roles in sorted(roles_by_guild.items())
        ]
        write_json(self._path, payload)

    def _load(self) -> None:
        for entry in read_json_list(self._path):
            try:
                guild_id = int(entry["guild"])
                roles = {int(role_id) for role_id in entry["roles"]}
            except (KeyError, TypeError, ValueError):
                log.warning("Skipping malformed entry in %s", self._path)
                continue
            if roles:
                self._roles.setdefault(guild_id, set()).update(roles)
