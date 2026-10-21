"""Stand-ins for discord.py's Interaction and for the site's API, so the
handlers run with no Discord and no network. Each records what it was asked."""

from types import SimpleNamespace

from bot.api import Answer, SiteUnavailable
from bot.config import Config

GUILD = 1410431883912941619
MOD_ROLE = 1413131483102707853
MOD_QUEUE = 1562201700507586681

CONFIG = Config(
    token="t",
    guild_id=GUILD,
    moderator_role_id=MOD_ROLE,
    mod_queue_channel_id=MOD_QUEUE,
    api_base_url="https://example.test/api",
    api_secret="k",
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

    async def unblock(self, kind, ref, moderator):
        return self._next("unblock", kind, ref, moderator)

    async def add_term(self, term, match, moderator):
        return self._next("add_term", term, match, moderator)
