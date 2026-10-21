"""The team board's committed work in order: what the bot's "Up next" post
numbers 1, 2, 3 (discord-bot-plan.md, Step 6b).

WHY A TABLE
-----------
A Discord forum sorts only by date or activity, and its tags are buckets
(High, Normal, Low), not an order. The order the team agrees on has to live
somewhere that survives a deleted message and that the bot, the tests and a
Claude session can all read, so it is a row per post here, behind the bot API
(team_queue.py). The worker keeps it in step with the board's tags: a post
tagged Planned joins at the bottom, Done or Won't do leaves.

WHAT IT HOLDS
-------------
A Discord forum post's id and its position, nothing else: titles and tags are
read from Discord each time, so a renamed post never shows a stale name.
PRIVATE in `content_snapshot` (production's board, meaningless locally) and
never in /calculator-data.
"""

from django.db import models


class TeamQueueEntry(models.Model):
    # The forum post (a Discord thread id, a 64-bit snowflake kept as text,
    # the way discord_message.py keeps them).
    thread_id = models.CharField(max_length=32, unique=True)
    # 1-based and gap-free: team_queue renumbers after every change.
    position = models.PositiveIntegerField()
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position",)
        verbose_name = "team queue entry"
        verbose_name_plural = "team queue entries"

    def __str__(self):
        return f"{self.position}. {self.thread_id}"
