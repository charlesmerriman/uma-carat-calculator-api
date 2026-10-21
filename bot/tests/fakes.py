"""Stand-ins for discord.py's Interaction and for the site's API, so the
handlers run with no Discord and no network. Each records what it was asked."""

from types import SimpleNamespace

from bot.api import Answer, SiteUnavailable
from bot.config import Config

GUILD = 1410431883912941619
MOD_ROLE = 1413131483102707853
MOD_QUEUE = 1562201700507586681
TEAM_ROLE = 1489347113153200268
TEAM_BOARD = 1562200270061043872
FEEDBACK = 1414359698383310949

CONFIG = Config(
    token="t",
    guild_id=GUILD,
    moderator_role_id=MOD_ROLE,
    mod_queue_channel_id=MOD_QUEUE,
    api_base_url="https://example.test/api",
    api_secret="k",
    team_board_id=TEAM_BOARD,
    team_role_id=TEAM_ROLE,
    feedback_forum_id=FEEDBACK,
)


class FakeResponse:
    def __init__(self, calls):
        self.calls = calls

    async def send_message(self, content=None, **kwargs):
        self.calls.append(("send_message", content, kwargs))

    async def defer(self, **kwargs):
        self.calls.append(("defer", None, kwargs))

    async def edit_message(self, **kwargs):
        self.calls.append(("edit_message", kwargs.get("content"), kwargs))

    async def send_modal(self, modal):
        self.calls.append(("send_modal", None, {"modal": modal}))


class FakeFollowup:
    def __init__(self, calls):
        self.calls = calls

    async def send(self, content=None, **kwargs):
        self.calls.append(("followup", content, kwargs))


class FakeInteraction:
    """A click or a command from `roles`, in `guild_id` / `channel_id`."""

    def __init__(  # pylint: disable=too-many-arguments
        self, custom_id=None, *, roles=(MOD_ROLE,), guild_id=GUILD, channel_id=MOD_QUEUE, member=True,
    ):
        self.calls = []
        self.data = {"custom_id": custom_id} if custom_id else {}
        self.guild_id = guild_id
        self.channel_id = channel_id
        user = SimpleNamespace(name="oguri_fan", id=123456789012345678)
        if member:
            user.roles = [SimpleNamespace(id=role) for role in roles]
        self.user = user
        self.response = FakeResponse(self.calls)
        self.followup = FakeFollowup(self.calls)

    def kinds(self):
        return [call[0] for call in self.calls]

    def last_text(self):
        return self.calls[-1][1]


class FakeApi:
    """Answers each method with a queued Answer, or raises SiteUnavailable for
    the string "down". Records (method, args)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def _next(self, name, *args):
        self.calls.append((name, *args))
        answer = self.answers.pop(0) if self.answers else Answer(200, {})
        if answer == "down":
            raise SiteUnavailable
        return answer

    async def reports(self):
        return self._next("reports")

    async def item(self, kind, ref):
        return self._next("item", kind, ref)

    async def moderate(self, kind, ref, action, moderator):
        return self._next("moderate", kind, ref, action, moderator)

    async def undo(self, kind, ref, moderator):
        return self._next("undo", kind, ref, moderator)

    async def unblock(self, kind, ref, moderator):
        return self._next("unblock", kind, ref, moderator)

    async def add_term(self, term, match, moderator):
        return self._next("add_term", term, match, moderator)

    async def claim(self, key, channel_id):
        return self._next("claim", key, channel_id)

    async def record(self, key, message_id):
        return self._next("record", key, message_id)

    async def release(self, key):
        return self._next("release", key)

    async def digest(self):
        return self._next("digest")

    async def find_record(self, message_id):
        return self._next("find_record", message_id)

    async def queue(self):
        return self._next("queue")

    async def queue_add(self, thread_id):
        return self._next("queue_add", thread_id)

    async def queue_remove(self, thread_id):
        return self._next("queue_remove", thread_id)

    async def queue_move(self, thread_id, position):
        return self._next("queue_move", thread_id, position)

    async def queue_sync(self, thread_ids):
        return self._next("queue_sync", thread_ids)


def tag(name):
    return SimpleNamespace(name=name, id=hash(name) % 10**6)


class FakeThread:
    """A forum post: its tags, and what was done to it."""

    def __init__(self, thread_id, parent_id, tags=(), name="A post"):
        self.id = thread_id
        self.parent_id = parent_id
        self.name = name
        self.applied_tags = [tag(name) for name in tags]
        self.jump_url = f"https://discord.com/channels/{GUILD}/{thread_id}"
        self.starter_message = None
        self.content = None
        self.edits = []
        self.sent = []

    async def edit(self, **kwargs):
        self.edits.append([t.name for t in kwargs["applied_tags"]])
        self.applied_tags = list(kwargs["applied_tags"])

    async def send(self, content, **_kwargs):
        self.sent.append(content)

    async def fetch_message(self, message_id):
        return SimpleNamespace(id=message_id, content="(fetched starter)")


class FakeForum:
    """A forum with `tags`; create_thread records the new post."""

    def __init__(self, forum_id, tags, *, fail=False):
        self.id = forum_id
        self.available_tags = [tag(name) for name in tags]
        self.created = []
        self.fail = fail

    async def create_thread(self, **kwargs):
        if self.fail:
            import discord  # pylint: disable=import-outside-toplevel
            raise discord.HTTPException(SimpleNamespace(status=500, reason="boom"), "boom")
        thread = FakeThread(900000 + len(self.created), self.id, name=kwargs["name"])
        thread.content = kwargs["content"]
        thread.applied_tags = list(kwargs["applied_tags"])
        self.created.append(thread)
        return SimpleNamespace(thread=thread)
