"""
The ONE place that puts a person beside public content.

Until the gallery, an account's display name and picture were served to their
owner alone (GET /account). A gallery post or comment is the person choosing to
show them to everyone: posting is the opt-in, and the post dialog and the
comment box both say what will show. poster_block() is how that happens, and
nothing else may build a person's public block. The privacy rule names this
function rather than a route, so a second gallery adds a route that calls it
and needs no new rule (.claude/rules/auth-privacy.md).

WHAT A BLOCK HOLDS, AND WHY NOTHING ELSE
----------------------------------------
    display_name   the name they chose ("" if they have since blanked it;
                   the client shows "Unnamed trainer")
    portrait       their account picture: the first favourite uma with art
    tier_name      their Patreon tier's NAME, or null
    gallery_style  the look key (benefits.GALLERY_STYLE_LADDER), or null

Never the generated username, a user id, a Patreon handle, an email, a tier
ORDER or a vote weight. The dict below is the field list, the role
PatreonSupporterSerializer's explicit fields play for the supporter email.

`tier_name` and `gallery_style` are both null when the person turned
`show_supporter_badge` off, and both null without a live pledge (a lapse
takes them away on the next request, like every benefit).
"""

from calculatorapi import benefits
from calculatorapi.models import PatreonSupporter, UserOshi


def oshi_rows(user):
    """Every stored oshi of `user`, first to last, as GET /account's wire shape.

    All of them, not just the covered ones: the account page greys out what
    the current tier no longer covers rather than pretending it is gone.
    `image` is the storage URL of the uma's portrait (borderless art, else the
    bordered art), like GET /umas, and "" if an editor has since cleared the
    picture: the row is still theirs, the client just has nothing to draw.
    """
    return [_oshi_row(position, row) for position, row in enumerate(
        user.oshis.select_related("uma").order_by("position")
    )]


def _oshi_row(position, row):
    return {
        "position": position,
        "id": row.uma.pk,
        "name": row.uma.name,
        "image": row.uma.portrait.url if row.uma.portrait else "",
    }


def current_avatar_url(oshis, slots):
    """The account picture: the first oshi's art, or None.

    None, not "", when there is nothing to show, so the client draws its
    default rather than loading an empty src. Nothing while `slots` is 0,
    which since the free slot (2026-10-07) no signed-in account hits; the
    guard stays because the count is the server's to decide, not this
    function's to assume. The first row WITH an image, rather than strictly
    position 0, so an editor clearing one uma's picture degrades to the next
    pick instead of to a broken tile.
    """
    if slots < 1:
        return None
    return next((row["image"] for row in oshis if row["image"]), None)


def _block(user, supporter, oshis):
    slots = benefits.oshi_slots_for(supporter, is_staff=user.is_staff)
    shows_badge = user.show_supporter_badge and supporter is not None
    return {
        "display_name": user.display_name,
        "portrait": current_avatar_url(oshis, slots),
        "tier_name": supporter.tier.name if shows_badge else None,
        "gallery_style": benefits.gallery_style_for(supporter) if shows_badge else None,
    }


def poster_block(user):
    """The public block for one person. See the module docstring."""
    return poster_blocks([user])[user.pk]


def poster_blocks(users):
    """{user id: block} for many people in two queries, for a page of posts
    or comments. Duplicates and None are fine (a deleted comment author)."""
    people = {user.pk: user for user in users if user is not None}
    if not people:
        return {}
    supporters = {
        row.linked_user_id: row
        for row in PatreonSupporter.objects.select_related("tier").filter(
            # The three conditions of benefits.entitled_supporter, for many.
            linked_user_id__in=people, is_active=True, tier__isnull=False
        )
    }
    oshis = {user_id: [] for user_id in people}
    for row in (
        UserOshi.objects.select_related("uma")
        .filter(user_id__in=people)
        .order_by("user_id", "position")
    ):
        oshis[row.user_id].append(_oshi_row(len(oshis[row.user_id]), row))
    return {
        user_id: _block(user, supporters.get(user_id), oshis[user_id])
        for user_id, user in people.items()
    }
