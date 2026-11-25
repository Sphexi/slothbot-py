"""Container healthcheck: succeeds while the bot's heartbeat file is fresh."""

import os
import sys
import time
from pathlib import Path

from slothbot.paths import heartbeat_path

MAX_AGE_SECONDS = 60


def check(path: Path, now: float | None = None) -> str | None:
    """Return a failure reason, or None when the heartbeat is fresh."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return f"heartbeat file {path} is missing"
    age = (time.time() if now is None else now) - mtime
    if age > MAX_AGE_SECONDS:
        return f"heartbeat file {path} is {age:.0f}s old (limit {MAX_AGE_SECONDS}s)"
    return None


def main() -> int:
    override = os.environ.get("SB_HEARTBEAT_FILE")
    path = heartbeat_path(
        Path(os.environ.get("SB_DATA", "data")), Path(override) if override else None
    )
    reason = check(path)
    if reason is not None:
        print(reason, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
