"""
Resource warnings: the API's memory, CPU and restarts, read from DigitalOcean
every 2 minutes and posted in #calc-ops with the likely reason
(added 2026-11-10, after three "MEM_UTILIZATION > 80%" emails nobody else saw).

THE SAME LINES AS THE EMAILS
----------------------------
DigitalOcean emails the owner when the API component goes over 80% CPU or 80%
memory for 5 minutes, or restarts (`alerts:` on calculatorproject in
.do/app.yaml). This watches the same three numbers with the same thresholds,
so the team sees what the owner's inbox sees, plus a reason.

ONE POST PER EPISODE, NOT PER CROSSING (hysteresis)
---------------------------------------------------
On 2026-11-10 memory sat at 79-81% all day and the emails fired three times,
once per wobble across 80%. Here a gauge goes "high" when every sample in the
last 5 minutes is over the line, and only goes back to normal when every sample
in the last 5 minutes is under a LOWER line (75% memory, 70% CPU). A number
hovering at the line therefore posts once and stays quiet.

The state lives in memory, like the health probe's (health.py): a bot restart
starts it at "normal", which at worst repeats one warning.

WHERE THE REASON COMES FROM
---------------------------
Only DigitalOcean's own history (about 26 hours, one sample every ~2 minutes)
and, for CPU, GET bot/health's busiest routes. The reasons are educated
guesses from the shape of the numbers, worded that way:

* Memory, fast: up 8+ points in 30 minutes. Something heavy is running now.
* Memory, creep: no fast jump. Python keeps memory it took during busy spells
  and doesn't hand it back until a restart, so the floor ratchets up after each
  rush (2026-10-20/21/26/27 and 11-10). Names the biggest one-hour climb since
  the last restart and how busy the CPU was then.
* CPU: compared with the same time yesterday, and the daily 15:00 UTC peak.
* Restart: a deploy rolling out (not a crash), else memory or CPU maxed out
  just before, else "look at the crash log".
"""

import statistics

import discord

# Mirrors the `alerts:` block on calculatorproject in .do/app.yaml.
MEMORY_HIGH = 80
CPU_HIGH = 80
# The lower lines a gauge must drop under before it counts as normal again.
MEMORY_CLEAR = 75
CPU_CLEAR = 70
# "Over the line for 5 minutes", as DigitalOcean words its alert window.
WINDOW_SECONDS = 5 * 60
# How far back each 2-minute check reads: the 5-minute window, plus room to see
# a restart counter tick up between two samples.
RECENT_MINUTES = 30
# How far back a reason reads: since the last restart (deploys are days apart,
# but the climb that matters is usually the last day), and this time yesterday.
HISTORY_MINUTES = 26 * 60
# A sample older than this means DigitalOcean has nothing current: change nothing.
STALE_SECONDS = 10 * 60

# A fall of this many points between two memory samples is a fresh process (a
# deploy or a restart), which is where a ratchet starts over.
RESTART_DROP = 10
# The first hour after a restart always climbs (caches filling), so a creep's
# reason looks past it, for the biggest rise over any 3 hours: last night's
# rush (2026-11-09, 65% to 79%) took three.
WARMUP_SECONDS = 3600
CLIMB_SECONDS = 3 * 3600
# "Fast" memory: this many points in this many minutes.
JUMP_POINTS = 8
JUMP_SECONDS = 30 * 60
# Past this, a warning says the server is close to being killed for it.
NEAR_LIMIT = 90
# A restart right after memory or CPU reached these is put down to them.
CRASH_MEMORY = 95
CRASH_CPU = 95
# 15:00-16:00 UTC is the busiest hour every day (memory banner-launch-traffic-spikes).
DAILY_PEAK_HOUR = 15
# Busy compared with yesterday: this many times the same time yesterday.
BUSIER_THAN_USUAL = 1.5

AMBER = 0xF59E0B
RED = 0xE5484D


# ── Reading a series ──
# A series is [(unix seconds, value)], oldest first (DigitalOcean.series).


def fresh(points, now_ts):
    """Whether the newest sample is recent enough to act on."""
    return bool(points) and now_ts - points[-1][0] <= STALE_SECONDS


def last_window(points):
    """The samples in the 5 minutes up to the newest one."""
    newest = points[-1][0]
    return [value for stamp, value in points if stamp >= newest - WINDOW_SECONDS]


def between(points, start, end):
    """The values sampled from `start` to `end` (unix seconds), inclusive."""
    return [value for stamp, value in points if start <= stamp <= end]


def _average(values):
    return sum(values) / len(values) if values else None


def _when(stamp, style="t"):
    """A Discord timestamp tag: each reader sees it in their own time zone
    (t = time, f = date and time, R = "3 hours ago")."""
    return f"<t:{int(stamp)}:{style}>"


class Gauge:
    """One number's high/normal state, with the two lines described above."""

    def __init__(self, high, clear):
        self.high_line = high
        self.clear_line = clear
        self.high = False

    def feed(self, points, now_ts):
        """Returns "high" when it goes over, "clear" when it comes back, else None.
        Needs two samples in the window: one sample is a moment, not 5 minutes."""
        if not fresh(points, now_ts):
            return None
        window = last_window(points)
        if len(window) < 2:
            return None
        if not self.high and all(value > self.high_line for value in window):
            self.high = True
            return "high"
        if self.high and all(value < self.clear_line for value in window):
            self.high = False
            return "clear"
        return None


class RestartWatch:
    """Spots the restart counter ticking up between two samples. Works whether
    DigitalOcean reports a running total (0, 0, 1, 1) or a count per interval
    (0, 1, 0): either way a restart shows as a value higher than the one before."""

    def __init__(self):
        self.seen_up_to = None

    def feed(self, points):
        """The time of the newest restart not reported yet, or None."""
        rises = [
            stamp for (_, before), (stamp, value) in zip(points, points[1:])
            if value > before and (self.seen_up_to is None or stamp > self.seen_up_to)
        ]
        if not rises:
            return None
        self.seen_up_to = rises[-1]
        return rises[-1]


# ── The reasons ──


def _since_restart(memory):
    """(index, unix seconds) of the first sample of the current process: just
    after the last big drop, else the start of the history (index 0, None)."""
    for index in range(len(memory) - 1, 0, -1):
        if memory[index - 1][1] - memory[index][1] >= RESTART_DROP:
            return index, memory[index][0]
    return 0, None


def _biggest_climb(memory):
    """(start sample, end sample) of the largest rise within CLIMB_SECONDS, or None."""
    best_rise, best = 0, None
    end = 0
    for start, (stamp, value) in enumerate(memory):
        # Two pointers: `end` only moves forward, to the first sample 3 hours on.
        end = max(end, start)
        while end < len(memory) - 1 and memory[end][0] < stamp + CLIMB_SECONDS:
            end += 1
        rise = memory[end][1] - value
        if rise > best_rise:
            best_rise, best = rise, (memory[start], memory[end])
    return best


def is_fast_jump(memory):
    """Whether memory rose JUMP_POINTS or more in the last 30 minutes, within
    this process (a restart in between is a reset, not a jump)."""
    return _jump_from(memory) is not None


def _jump_from(memory):
    """The value 30 minutes ago when memory has jumped since, else None."""
    now_ts, now_value = memory[-1]
    current = memory[_since_restart(memory)[0]:]
    earlier = [value for stamp, value in current if stamp <= now_ts - JUMP_SECONDS]
    if earlier and now_value - earlier[-1] >= JUMP_POINTS:
        return earlier[-1]
    return None


def _explain_jump(memory, cpu, before):
    now_ts, now_value = memory[-1]
    busy = _average(between(cpu, now_ts - JUMP_SECONDS, now_ts))
    return [
        f"Memory jumped from {before:.0f}% to {now_value:.0f}% in the last 30 minutes"
        + (f" while CPU averaged {busy:.0f}%" if busy is not None else "")
        + ". Something heavy is running right now: a rush of visitors or an expensive request "
        "(the busiest routes are below)."
    ]


def _explain_creep(memory, cpu):
    start_index, restarted_at = _since_restart(memory)
    current = memory[start_index:]
    start = f"{_when(restarted_at, 'f')}, at {current[0][1]:.0f}%" if restarted_at else None
    lines = [
        "Memory has crept up "
        + (f"since the server last restarted ({start})" if start else "over the last day")
        + " and won't come back down on its own: the server keeps memory it used during busy "
        "spells until it restarts, so each rush raises the floor."
    ]
    after_warmup = [point for point in current if point[0] >= current[0][0] + WARMUP_SECONDS]
    climb = _biggest_climb(after_warmup) if restarted_at else _biggest_climb(current)
    if climb:
        lines.append(_climb_line(climb, cpu))
    lines.append("A redeploy resets it. If this keeps happening, the server needs more memory.")
    return lines


def _climb_line(climb, cpu):
    """"The biggest climb was 65% to 79% between 22:00 and 01:00, while CPU
    averaged 26% (a usual moment is 10%)": when the floor rose, and whether
    the server was busy then."""
    (from_ts, from_value), (to_ts, to_value) = climb
    line = f"The biggest climb was {from_value:.0f}% to {to_value:.0f}% between {_when(from_ts, 'f')} and {_when(to_ts, 'f')}"
    busy = _average(between(cpu, from_ts, to_ts))
    if busy is not None and cpu:
        usual = statistics.median(value for _, value in cpu)
        line += f", while CPU averaged {busy:.0f}% (a usual moment is {usual:.0f}%)"
    return line + "."


def explain_memory(memory, cpu):
    """The likely reason memory is high, as lines. `memory` and `cpu` are the
    last HISTORY_MINUTES of samples."""
    now_value = memory[-1][1]
    before = _jump_from(memory)
    lines = _explain_jump(memory, cpu, before) if before is not None else _explain_creep(memory, cpu)
    if now_value >= NEAR_LIMIT:
        lines.append(
            "**At 100% the server is killed and restarted** and the site drops for a minute or two, "
            "so redeploy soon."
        )
    return lines


def explain_cpu(cpu, now_ts, hour):
    """The likely reason CPU is high, as lines."""
    lines = []
    recent = _average(between(cpu, now_ts - 15 * 60, now_ts))
    yesterday = _average(between(cpu, now_ts - 86400 - 15 * 60, now_ts - 86400 + 15 * 60))
    if recent is not None:
        line = f"CPU has averaged {recent:.0f}% over the last 15 minutes."
        if yesterday:
            if recent >= BUSIER_THAN_USUAL * yesterday:
                line += (
                    f" That's {recent / yesterday:.1f}x this time yesterday ({yesterday:.0f}%): more visitors "
                    "than usual, or a heavier kind of request (a new banner or guide does this)."
                )
            else:
                line += (
                    f" This time yesterday it was {yesterday:.0f}%, so this is the usual daily rush, "
                    "and it's outgrowing the server."
                )
        lines.append(line)
    if hour == DAILY_PEAK_HOUR:
        lines.append("15:00 to 16:00 UTC is the site's busiest hour every day.")
    lines.append(
        "While CPU is maxed out, requests wait in line and the site feels slow; the busiest routes "
        "below show what it's spending the time on."
    )
    return lines


def explain_restart(memory, cpu, restart_ts, during_deploy):
    """The likely reason the API restarted, as lines."""
    if during_deploy:
        return ["A deploy was rolling out at the same moment, so this is most likely the deploy, not a crash."]
    memory_peak = max(between(memory, restart_ts - 10 * 60, restart_ts), default=None)
    cpu_peak = max(between(cpu, restart_ts - 10 * 60, restart_ts), default=None)
    if memory_peak is not None and memory_peak >= CRASH_MEMORY:
        lines = [f"It most likely **ran out of memory**: memory reached {memory_peak:.0f}% just before."]
    elif cpu_peak is not None and cpu_peak >= CRASH_CPU:
        lines = [
            f"CPU was maxed out ({cpu_peak:.0f}%) just before, so it most likely stopped answering "
            "DigitalOcean's health check and was restarted."
        ]
    else:
        numbers = []
        if memory_peak is not None:
            numbers.append(f"memory {memory_peak:.0f}%")
        if cpu_peak is not None:
            numbers.append(f"CPU {cpu_peak:.0f}%")
        looked = f" ({', '.join(numbers)})" if numbers else ""
        lines = [
            f"Memory and CPU looked normal just before{looked}, so the reason is in the crash log: "
            "`doctl apps logs <app> calculatorproject --type run_restarted`."
        ]
    lines.append("A restart empties the server's caches, so the first loads after it are slower.")
    return lines


# ── The posts ──


def render_memory(memory, cpu, route_lines=()):
    now_value = memory[-1][1]
    lines = explain_memory(memory, cpu)
    # The busiest routes are the last 5 minutes: they explain a jump happening
    # now, not a creep built up over a day.
    if is_fast_jump(memory) and route_lines:
        lines += ["", *route_lines]
    return discord.Embed(title=f"API memory is high ({now_value:.0f}%)", description="\n".join(lines), colour=AMBER)


def render_cpu(cpu, now_ts, hour, route_lines=()):
    lines = explain_cpu(cpu, now_ts, hour)
    if route_lines:
        lines += ["", *route_lines]
    return discord.Embed(title=f"API CPU is high ({cpu[-1][1]:.0f}%)", description="\n".join(lines), colour=AMBER)


def render_restart(memory, cpu, restart_ts, during_deploy):
    lines = [f"The API restarted at {_when(restart_ts)}.", *explain_restart(memory, cpu, restart_ts, during_deploy)]
    return discord.Embed(
        title="The API restarted",
        description="\n".join(lines),
        colour=AMBER if during_deploy else RED,
    )


def render_clear(name, value):
    return f"API {name} is back to normal ({value:.0f}%)."
