"""
The bot's voice, in one place (discord-bot-plan.md, "Persona"): a LIGHT touch
of Oguri Cap. Calm, sincere, short sentences, now and then a little
encouragement. No catchphrases, no food jokes, no roleplay, and never styled
as an official Cygames account (owner, 2026-10-21: "mildly influenced", the
"light touch" column).

Only friendly posts carry it: announcements and the replies on public
feedback and requests. Moderation, errors, privacy, health alerts, the digest
and deploy news stay plain (bot/format.py), and so does every title, which
must stay scannable. The one in-voice line the SITE sends, the patch-note
footer, is VOICE_THANKS in calculatorapi/changelog_discord.py; change the two
together.

The copy rules apply here as everywhere players read: no em dashes, and it
should sound like a person wrote it (.claude/rules/copy.md). To switch the
voice off, make every line below plain (or empty, for the flourishes).
"""

# One short sentence added after an announcement's facts, by kind and
# milestone. A moment not listed here gets none: most stay plain on purpose.
FLOURISHES = {
    ("banner", "live"): "Good luck with your pulls.",
    ("banner", "tomorrow"): "Time to check your carats.",
    ("campaign", "tomorrow"): "Time to check your carats.",
    ("cm", "registration_opens"): "Let's give it our all.",
    ("loh", "registration_opens"): "Let's give it our all.",
    ("cm", "finals_registration_closing"): "Don't miss it.",
    ("loh", "finals_registration_closing"): "Don't miss it.",
    ("legend_races", "live"): "New rivals to race.",
}

# Shown on any announcement whose date is the site's prediction.
PREDICTED_NOTE = "This is the site's prediction, so it may move. The Timeline updates when it's confirmed."

# Replies people see on the team board and the public feedback forum.
REQUEST_POSTED = "Posted to the team board: {link}. Thanks."
PUBLIC_UNDER_REVIEW = "Thank you for this. It's on the team's list now. We'll come back to this post when it moves."
PUBLIC_PLANNED = "The team has planned this. We'll do our best with it."
PUBLIC_DONE = "This is done now. Thank you for suggesting it."
# A "no" stays plain: it shouldn't sound cheerful.
PUBLIC_WONT = "The team has decided not to do this one, at least for now. Thanks for suggesting it."


def flourish(kind, milestone, predicted):
    """The in-voice sentence for an announcement, or "" for none. A predicted
    reminder gets none: its prediction note is the important line."""
    if predicted:
        return ""
    return FLOURISHES.get((kind, milestone), "")
