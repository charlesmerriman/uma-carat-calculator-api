"""
The worker's settings, from the environment, checked once at start-up.

In production DigitalOcean sets them on the worker component. Locally they
come from a file named by BOT_ENV_FILE (~/.config/uma-discord/bot.env: the
DEV bot and the practice server, never the community one). A real
environment variable wins over the file, so one value can be overridden for a
single run without editing it.

A missing or malformed value stops the worker at once with a line naming the
setting, never its value: a bot that starts half-configured fails later, on a
moderator's click, where it is much harder to see why.
"""

import os
from dataclasses import dataclass, field

REQUIRED = (
    "DISCORD_BOT_TOKEN",
    "DISCORD_GUILD_ID",
    "DISCORD_MODERATOR_ROLE_ID",
    "DISCORD_MOD_QUEUE_CHANNEL_ID",
    "API_BASE_URL",
    "BOT_API_SECRET",
)
# The team board (discord-bot-plan.md, Step 6). Optional: while any of the three
# is unset the worker registers no team commands, so a deploy that adds the code
# before the settings changes nothing.
OPTIONAL = (
    "DISCORD_TEAM_BOARD_ID", "DISCORD_TEAM_ROLE_ID", "DISCORD_WEBSITE_FEEDBACK_ID", "DISCORD_OPS_CHANNEL_ID",
    "DISCORD_DEVLOG_CHANNEL_ID", "DISCORD_ANNOUNCE_CHANNEL_ID", "DISCORD_ANNOUNCE_PING_ROLE_ID",
)
# Discord ids ("snowflakes") are all digits.
ID_SETTINGS = ("DISCORD_GUILD_ID", "DISCORD_MODERATOR_ROLE_ID", "DISCORD_MOD_QUEUE_CHANNEL_ID", *OPTIONAL)


class ConfigError(Exception):
    """A setting is missing or malformed. The message names it, never its value."""


@dataclass(frozen=True)
class Config:
    # repr=False on the two secrets, so a logged or printed Config can't leak them.
    token: str = field(repr=False)
    guild_id: int
    moderator_role_id: int
    mod_queue_channel_id: int
    api_base_url: str
    api_secret: str = field(repr=False)
    team_board_id: int | None = None
    team_role_id: int | None = None
    feedback_forum_id: int | None = None
    # #calc-ops: the Monday summary (Step 6b), the daily digest and the health
    # alerts (Step 7). It no longer holds a pinned "Up next" (see board.py).
    ops_channel_id: int | None = None
    # Step 7. #calc-dev-log for deploy news; a READ-ONLY DigitalOcean token
    # (never the admin's rebuild token) and the app it reads.
    devlog_channel_id: int | None = None
    do_token: str = field(default="", repr=False)
    do_app_id: str = ""
    # The public site the probe checks, from outside, like a visitor.
    site_url: str = ""
    probe_slow_seconds: float = 3.0
    digest_hour_utc: int = 8
    # Step 8: banner and event announcements. On trial in #carat-calculator-website
    # (user, 2026-10-21). The ping role is blank until the owner decides; when
    # set, only "live now" moments ping it, never a reminder.
    announce_channel_id: int | None = None
    announce_ping_role_id: int | None = None

    @property
    def team_enabled(self):
        return bool(self.team_board_id and self.team_role_id and self.feedback_forum_id)

    @property
    def ops_enabled(self):
        """The daily digest and the health probe."""
        return bool(self.ops_channel_id)

    @property
    def deploys_enabled(self):
        return bool(self.devlog_channel_id and self.do_token and self.do_app_id)

    @property
    def board_enabled(self):
        """The queue, the Monday summary and the status mirror."""
        return self.team_enabled and bool(self.ops_channel_id)


def read_env_file(path):
    """KEY=VALUE lines into a dict. Blank lines and # comments are skipped, and
    one pair of surrounding quotes is dropped. Deliberately small: the file is
    ours, so this needs none of a full dotenv parser's cases."""
    values = {}
    with open(os.path.expanduser(path), encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key.strip()] = value
    return values


def load(environ=None):
    """Build the Config, or raise ConfigError naming every bad setting."""
    environ = dict(os.environ if environ is None else environ)
    values = {}
    env_file = environ.get("BOT_ENV_FILE")
    if env_file:
        try:
            values.update(read_env_file(env_file))
        except OSError as exc:
            raise ConfigError(f"BOT_ENV_FILE could not be read: {exc.strerror}") from exc
    values.update({key: value for key, value in environ.items() if value})

    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise ConfigError(f"Missing setting(s): {', '.join(missing)}")
    malformed = [key for key in ID_SETTINGS if values.get(key) and not values[key].isdigit()]
    if malformed:
        raise ConfigError(f"Not a Discord id (digits only): {', '.join(malformed)}")

    return Config(
        token=values["DISCORD_BOT_TOKEN"],
        guild_id=int(values["DISCORD_GUILD_ID"]),
        moderator_role_id=int(values["DISCORD_MODERATOR_ROLE_ID"]),
        mod_queue_channel_id=int(values["DISCORD_MOD_QUEUE_CHANNEL_ID"]),
        api_base_url=values["API_BASE_URL"].rstrip("/"),
        api_secret=values["BOT_API_SECRET"],
        team_board_id=_optional_id(values, "DISCORD_TEAM_BOARD_ID"),
        team_role_id=_optional_id(values, "DISCORD_TEAM_ROLE_ID"),
        feedback_forum_id=_optional_id(values, "DISCORD_WEBSITE_FEEDBACK_ID"),
        ops_channel_id=_optional_id(values, "DISCORD_OPS_CHANNEL_ID"),
        devlog_channel_id=_optional_id(values, "DISCORD_DEVLOG_CHANNEL_ID"),
        do_token=values.get("DO_API_TOKEN", ""),
        do_app_id=values.get("DO_APP_ID", ""),
        # API_BASE_URL is the site plus /api in production; the probe wants the site.
        site_url=values.get("SITE_URL") or values["API_BASE_URL"].rstrip("/").removesuffix("/api"),
        probe_slow_seconds=_number(values, "PROBE_SLOW_SECONDS", 3.0),
        digest_hour_utc=int(_number(values, "DIGEST_HOUR_UTC", 8)),
        announce_channel_id=_optional_id(values, "DISCORD_ANNOUNCE_CHANNEL_ID"),
        announce_ping_role_id=_optional_id(values, "DISCORD_ANNOUNCE_PING_ROLE_ID"),
    )


def _number(values, key, default):
    try:
        return float(values[key]) if values.get(key) else default
    except ValueError as exc:
        raise ConfigError(f"Not a number: {key}") from exc


def _optional_id(values, key):
    return int(values[key]) if values.get(key) else None
