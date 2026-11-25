"""Whole-second durations rendered as English words."""

_UNITS = (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1))


def humanize_seconds(total: int) -> str:
    """Render seconds as words, for example 3725 as "1 hour 2 minutes 5 seconds"."""
    remaining = max(total, 0)
    parts: list[tuple[str, int]] = []
    for name, size in _UNITS:
        value, remaining = divmod(remaining, size)
        parts.append((name, value))
    while len(parts) > 1 and parts[0][1] == 0:
        parts.pop(0)
    while len(parts) > 1 and parts[-1][1] == 0:
        parts.pop()
    return " ".join(f"{value} {name}{'' if value == 1 else 's'}" for name, value in parts)
