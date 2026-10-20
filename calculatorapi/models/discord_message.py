"""One row per message the site has sent to Discord, keyed by what it is about.

WHY A TABLE
-----------
Two jobs, both impossible without remembering what was sent:

* **Never twice.** `sync_changelog` runs on every deploy, a deploy can run it
  more than once, and later steps add a worker that restarts and announcements
  that fire on a clock. The unique `key` (e.g. ``changelog:42``) is the one
  rule all of them share: the site never posts twice for one key. A send
  claims the key by inserting the row FIRST, so two runs racing each other
  cannot both win; the loser sees the row and stops.
* **Edit later.** Discord edits a message by its id, so a patch note fixed
  after it shipped, or (later) a moderation card whose report was resolved,
  needs the `message_id` Discord handed back.

WHAT IT HOLDS
-------------
Routing only: which channel, which message, when. Never the message's text, and
never anything about a person; the content lives in Discord and in the row the
key names. PRIVATE in `content_snapshot` all the same, because it describes
production's Discord and means nothing on a local database.

A row with a blank `message_id` is a claim in flight: the send is under way, or
it crashed between claiming and posting. `discord_messages` deletes the claim
when Discord refuses, so a failed send can be retried by the next run.

NOT REGISTERED IN THE ADMIN: there is nothing in it an editor would change, and
a hand edit could only break the link between a key and its message.
"""

from django.db import models


class DiscordMessage(models.Model):
    # "<what>:<id>[:<milestone>]", e.g. "changelog:42". Stable for the life of
    # the thing it describes; see discord_messages for the key helpers.
    key = models.CharField(max_length=120, unique=True)
    # Discord ids are 64-bit "snowflakes". Stored as text, the way the API
    # sends them, so nothing is ever rounded through a float.
    channel_id = models.CharField(max_length=32)
    message_id = models.CharField(max_length=32, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Discord Message"
        verbose_name_plural = "Discord Messages"

    def __str__(self):
        return self.key
