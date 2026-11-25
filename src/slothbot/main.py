"""Process entrypoint."""

import logging
import sys

from pydantic import ValidationError

from slothbot.bot import SlothBot
from slothbot.config import Settings


def main() -> int:
    try:
        settings = Settings()
    except ValidationError as error:
        fields = ", ".join(
            f"SB_{'_'.join(str(p) for p in e['loc']).upper()}" for e in error.errors()
        )
        print(f"Configuration error. Check these environment variables: {fields}", file=sys.stderr)
        return 1
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    SlothBot(settings).run(settings.token.get_secret_value(), log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
