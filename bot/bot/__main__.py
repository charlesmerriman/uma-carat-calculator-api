"""`python -m bot`: read the settings, connect, run until stopped.

A bad setting exits with status 2 and one line saying which, before anything
connects. DigitalOcean restarts a worker that exits, so a typo in the
dashboard shows up as a restart loop with that line in the run log.
"""

import logging
import sys

from bot import config as bot_config
from bot.client import SiteBot


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = bot_config.load()
    except bot_config.ConfigError as exc:
        print(f"bot: {exc}", file=sys.stderr)
        sys.exit(2)
    # log_handler=None: keep the logging set up above instead of discord.py's own.
    SiteBot(config).run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
