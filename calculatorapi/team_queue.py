"""
The team board's ordered queue (models/team_queue.py), as the bot API changes
it. Every function leaves positions 1..N with no gaps or repeats, inside one
transaction, so two changes at once can't leave two posts at position 3.
"""

from django.db import transaction

from calculatorapi.models import TeamQueueEntry


def entries():
    """Thread ids in queue order."""
    return list(TeamQueueEntry.objects.order_by("position").values_list("thread_id", flat=True))


def _renumber(thread_ids):
    """Write `thread_ids` as the whole queue, in that order."""
    rows = {row.thread_id: row for row in TeamQueueEntry.objects.select_for_update()}
    TeamQueueEntry.objects.exclude(thread_id__in=thread_ids).delete()
    for position, thread_id in enumerate(thread_ids, start=1):
        row = rows.get(thread_id)
        if row is None:
            TeamQueueEntry.objects.create(thread_id=thread_id, position=position)
        elif row.position != position:
            row.position = position
            row.save(update_fields=["position"])


def add(thread_id):
    """Put a post at the bottom. Returns False when it was already queued
    (it keeps its place)."""
    with transaction.atomic():
        current = entries()
        if thread_id in current:
            return False
        _renumber([*current, thread_id])
    return True


def remove(thread_id):
    """Take a post out; the ones below it move up. Returns whether it was there."""
    with transaction.atomic():
        current = entries()
        if thread_id not in current:
            return False
        _renumber([tid for tid in current if tid != thread_id])
    return True


def move(thread_id, position):
    """Put a queued post at `position` (1 = top; past the end = bottom).
    Returns False when it isn't queued."""
    with transaction.atomic():
        current = entries()
        if thread_id not in current:
            return False
        rest = [tid for tid in current if tid != thread_id]
        index = max(0, min(position - 1, len(rest)))
        _renumber([*rest[:index], thread_id, *rest[index:]])
    return True


def sync(thread_ids):
    """Make the queue hold exactly `thread_ids`: anything missing is dropped,
    anything new joins at the bottom in the order given, and every post that
    stays keeps its place. The worker's hourly repair after a missed event."""
    wanted = list(dict.fromkeys(thread_ids))
    with transaction.atomic():
        kept = [tid for tid in entries() if tid in wanted]
        _renumber([*kept, *[tid for tid in wanted if tid not in kept]])
    return entries()
