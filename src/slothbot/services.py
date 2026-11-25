"""Shared application state handed to command handlers."""

import time
from dataclasses import dataclass, field

from slothbot.counter import MessageCounter
from slothbot.limiter import Limiter
from slothbot.storage import RoleSet


@dataclass
class Services:
    counter: MessageCounter
    limiter: Limiter
    moderator: RoleSet
    ignored_roles: RoleSet
    started_at: float = field(default_factory=time.monotonic)
