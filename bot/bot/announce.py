"""
Banner and event announcements (discord-bot-plan.md, Step 8): "starts
tomorrow" reminders and "live now" posts, from the site's Timeline.

The site decides WHAT and WHEN (calculatorapi/announcements.py: every moment
with a fixed key and an instant, predicted dates only ever as a reminder);
this module only words them and posts each once. Every 10 minutes it asks for
the moments due soon, and posts any whose instant has passed in the last two
hours, after claiming `announce:<key>` on the site. A moment missed by more
than that (the bot was down) is skipped, not posted late.

Times are Discord timestamp tags (<t:...:F>), which every reader sees in
their own time zone. Plain voice: the bot's persona isn't chosen yet.
"""

import datetime
import logging

import discord

from bot.api import SiteUnavailable

logger = logging.getLogger(__name__)

POST_WITHIN = datetime.timedelta(hours=2)
COLOUR = 0xE6D28A
FOOTER = "From the Uma Carat Calculator timeline"
PREDICTED_NOTE = "This date is the site's prediction. The Timeline updates when it's confirmed."
SCHEDULE_LABELS = (
    ("registration_opens", "Registration opens"),
    ("round_one", "Round 1"),
    ("finals_registration_closing", "Finals registration closes"),
    ("finals", "Finals"),
)


def stamp(iso, style="F"):
    """A Discord timestamp tag for an ISO instant."""
    when = datetime.datetime.fromisoformat(iso)
    return f"<t:{int(when.timestamp())}:{style}>"


def _when(iso):
    return f"{stamp(iso)} ({stamp(iso, 'R')})"


def title_for(moment):
    name, milestone, predicted = moment["name"], moment["milestone"], moment["predicted"]
    kind = moment["kind"]
    if milestone == "tomorrow":
        if kind in ("cm", "loh"):
            return f"{'Predicted: r' if predicted else 'R'}egistration opens tomorrow for {name}"
        if kind == "legend_races":
            return f"{'Predicted: new' if predicted else 'New'} daily legend races tomorrow: {name}"
        return f"{'Predicted to start' if predicted else 'Starts'} tomorrow: {name}"
    return {
        "live": f"New daily legend races are live: {name}" if kind == "legend_races" else f"Live now: {name}",
        "registration_opens": f"Registration is open: {name}",
        "round_one": f"Round 1 has started: {name}",
        "finals_registration_closing": f"Finals registration closes in 12 hours: {name}",
        "finals": f"The finals have started: {name}",
    }[milestone]


def describe(moment):
    """The body: when, what's featured, and the prediction note."""
    lines = []
    milestone = moment["milestone"]
    if moment["kind"] in ("cm", "loh"):
        schedule = moment.get("schedule") or {}
        lines += [f"{label}: {_when(schedule[key])}" for key, label in SCHEDULE_LABELS if key in schedule]
    elif milestone == "tomorrow" and moment.get("starts_at"):
        lines.append(f"Starts {_when(moment['starts_at'])}")
    elif milestone == "live" and moment.get("ends_at"):
        lines.append(f"Runs until {_when(moment['ends_at'])}")
    for label, key in (("Featured umas", "umas"), ("Featured support cards", "cards")):
        if moment.get(key):
            names = ", ".join(discord.utils.escape_markdown(name) for name in moment[key])
            lines.append(f"{'Umas' if moment['kind'] == 'legend_races' else label}: {names}")
    if moment["predicted"]:
        lines += ["", PREDICTED_NOTE]
    return "\n".join(lines)


def render(moment):
    embed = discord.Embed(
        title=title_for(moment)[:256], url=moment.get("url"), description=describe(moment), colour=COLOUR,
    )
    if moment.get("image"):
        embed.set_image(url=moment["image"])
    embed.set_footer(text=FOOTER)
    return embed


def is_due(moment, now):
    at = datetime.datetime.fromisoformat(moment["at"])
    return at <= now <= at + POST_WITHIN


class Announcer:

    def __init__(self, api, config, get_channel):
        self.api = api
        self.config = config
        self.get_channel = get_channel

    async def tick(self, now):
        """Post every moment that just came due, each once. Returns the keys posted."""
        try:
            answer = await self.api.upcoming(days=2)
        except SiteUnavailable:
            return []
        posted = []
        for moment in answer.data.get("moments", []):
            if not is_due(moment, now):
                continue
            key = f"announce:{moment['key']}"
            try:
                claim = await self.api.claim(key, self.config.announce_channel_id)
            except SiteUnavailable:
                break
            if claim.status != 201:
                continue
            channel = await self.get_channel(self.config.announce_channel_id)
            role = self.config.announce_ping_role_id
            ping = role and moment["milestone"] != "tomorrow"
            try:
                message = await channel.send(
                    content=f"<@&{role}>" if ping else None,
                    embed=render(moment),
                    allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(role)])
                    if ping else discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.warning("Could not announce %s", key, exc_info=True)
                await self._release(key)
                continue
            try:
                await self.api.record(key, message.id)
            except SiteUnavailable:
                logger.error("%s posted as %s but not recorded", key, message.id)
            posted.append(key)
        return posted

    async def _release(self, key):
        try:
            await self.api.release(key)
        except SiteUnavailable:
            pass
