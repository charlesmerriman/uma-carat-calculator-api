"""
What the daily masterdata check found on the game's CDN, recorded and reported.

The check itself runs on a GitHub runner (scripts/masterdata_check.py), never
in the web process: it downloads and extracts a 17.7 MB database, and the web
container has had memory outages. It posts its findings to
POST /game-data/versions, which lands here. This module only keeps the
table and tells #calc-ops; it never touches game content.

ONE MESSAGE PER FINDING. Each post goes through discord_messages.send() under
`game-data:<region>:<version>:<hash>`, so a version is announced once, each
revision once, and a re-run of the check (or two racing) posts nothing new.
"""

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from calculatorapi import discord_messages
from calculatorapi.models import GameDataStatus, GameDataVersion


def message_key(region, version, master_hash):
    return f"game-data:{region}:{version}:{master_hash}"


def serialize(row):
    return {
        "region": row.region,
        "version": row.version,
        "master_size": row.master_size,
        "master_hash": row.master_hash,
        "status": row.status,
        "first_seen_at": row.first_seen_at.isoformat(),
        "revised_at": row.revised_at.isoformat() if row.revised_at else None,
    }


def _admin_link(row):
    path = reverse("admin:calculatorapi_gamedataversion_change", args=[row.pk])
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}"


def _report(row, kind):
    """Tell #calc-ops. `kind` is "new" or "revised". Team-facing, plain."""
    region = row.get_region_display()
    if kind == "new":
        text = f"New {region} masterdata on the CDN: **{row.version}**. Not marked live."
    elif row.status == GameDataStatus.LIVE:
        text = (f"{region} masterdata **{row.version}** (live) was revised on the CDN. "
                "The daily check opens a snapshot PR.")
    else:
        text = f"{region} masterdata **{row.version}** ({row.get_status_display().lower()}) was revised on the CDN."
    discord_messages.send(
        message_key(row.region, row.version, row.master_hash),
        settings.DISCORD_OPS_CHANNEL_ID,
        content=f"{text}\n[Open in admin]({_admin_link(row)})",
    )


def record_findings(findings):
    """
    Save what one run of the check saw. `findings` are validated dicts:
    region, version, master_size, master_hash. Returns the changes, as
    [{"region", "version", "change": "new" | "revised" | "unchanged"}].

    A version first seen is NEW and reported. A known version with a new size
    or hash is a revision: the row takes the new values and it is reported,
    whatever its status (an old or ignored version changing is worth knowing,
    and costs one line). Nothing here changes a status: that is a person's.
    """
    now = timezone.now()
    changes = []
    for finding in findings:
        row = GameDataVersion.objects.filter(
            region=finding["region"], version=finding["version"]
        ).first()
        if row is None:
            row = GameDataVersion.objects.create(
                region=finding["region"], version=finding["version"],
                master_size=finding["master_size"], master_hash=finding["master_hash"],
                checked_at=now,
            )
            _report(row, "new")
            change = "new"
        elif (row.master_size, row.master_hash) != (finding["master_size"], finding["master_hash"]):
            row.master_size = finding["master_size"]
            row.master_hash = finding["master_hash"]
            row.revised_at = now
            row.checked_at = now
            row.save(update_fields=["master_size", "master_hash", "revised_at", "checked_at"])
            _report(row, "revised")
            change = "revised"
        else:
            row.checked_at = now
            row.save(update_fields=["checked_at"])
            change = "unchanged"
        changes.append({"region": row.region, "version": row.version, "change": change})
    return changes
