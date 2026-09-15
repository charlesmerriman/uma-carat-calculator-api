*A plain-English guide. No coding knowledge needed.*

## The one-sentence version

The calculator takes the carats you say you have today, walks forward through the
calendar one day at a time adding everything the game gives you, and stops at the
end of each banner you've planned to tell you what you'll have at that moment.

That's all it is. Everything below is detail about what gets added and when.

---

## 1. It's a running balance, not a formula

Think of it as a bank statement that hasn't happened yet.

You tell the calculator "I have 12,000 carats right now." That's the opening balance.
Then it goes through the calendar day by day, from tomorrow until the last banner in
your plan, writing down every deposit: daily login, event rewards, club payouts, and
so on.

Whenever it reaches the **end date of a banner you've planned**, it takes a snapshot of
the balance on that date. That snapshot is the number shown on the banner's card. Then
it subtracts whatever your planned pulls on that banner cost, and keeps walking to the
next banner with whatever's left over.

So the banners in your plan are checkpoints along a road. The calculator drives the
whole road once and reports the odometer at each checkpoint.

**Two things follow from that:**

- **Order matters, and spending carries forward.** If you plan 100 pulls on an early
  banner, the later banners see a smaller balance, because you already spent it.
- **Today earns nothing.** The walk starts tomorrow. Anything you'd get today is
  assumed to already be in the number you typed in.

---

## 2. There are two kinds of carats

The calculator keeps two separate piles:

| Pile | Where it comes from | What it's for |
|---|---|---|
| **Free carats** | Almost everything: daily login, events, ranks, clubs, campaigns | Normal pulls at 150 each |
| **Paid carats** | Only the two things you buy with money: the Daily Carat Pack's purchase bonus, and part of the paid Training Pass | Normal pulls at 150, **or** discounted pulls at 50 |

Why split them? Because the game lets you buy **one discounted pull per day for 50
paid carats** instead of 150. That discount only works with purchased carats, so the
calculator has to know which of your carats are which. If you don't buy anything, your
paid pile stays at zero and the split never matters. The maths comes out the same as if
there were one pile.

The number shown on a banner card is the **two piles added together**.

---

## 3. Where the carats come from

Here is every income source the calculator knows about, and the schedule it uses.

### Daily login *(always on)*

- **75 carats every day**, no exceptions.
- Plus a **bonus on 4 days out of every 7**: +25 on three of them, +75 on one.

Over any full week that's **675 carats** (525 base + 150 in bonuses), or roughly
**2,900 a month** just for logging in.

One caveat: the bonus days are counted as "day 3, day 5, day 6, day 7 counting from
today", not as specific weekdays. Over any 7-day stretch you always get the same 675,
so the totals are right, but don't expect the bonus to line up with, say, every real
Saturday.

### Daily Carat Pack *(your toggle)*

If you've told the calculator you buy this:

- **50 free carats a day**, every day.
- **500 paid carats every 30 days.**

The 500 is a purchase, so it goes in the paid pile. The first one is counted **30 days
from today**, not today. The pack you're holding right now is assumed to be in the
balance you typed in already.

### Club rank *(monthly, on the 1st)*

Whatever your club rank pays, once per calendar month, on the 1st. A B+ club pays
1,800; an A+ club pays 2,700; and so on up the table.

Only actual **1st-of-the-month crossings** count. If your banner ends on the 28th,
you haven't reached the next payout yet.

### Team Trials rank *(weekly, on Mondays)*

Your Team Trials class pays out every Monday. Class 5 is 225 a week, Class 3 is 75,
Class 1 is nothing. The calculator counts the Mondays between now and the banner's end
date.

### Champions Meeting & League of Heroes *(on the event's end date)*

Each of these events pays out once, **on the day it finishes**, based on the rank you
told the calculator you usually finish at. If the event ends after your banner does,
you don't get to spend it on that banner.

### Game events *(two different shapes)*

Events (anniversaries, story campaigns, collabs) can pay two ways, and many pay both:

1. **A lump sum on the day the event starts.** The whole amount drops in on that date.
2. **A trickle spread across the event's run.** This one is front-loaded: you earn the
   reward faster at the beginning than at the end, because most people clear the easy
   event content early and grind the tail slowly.

   Roughly: about **20%** of the trickle is earned in the first tenth of the event,
   **40%** by the quarter mark, **60%** by the halfway point, and the rest trickles in
   to reach **100%** on the event's final day.

   If an event is already running when you open the calculator, you only get credited
   for the share still remaining. The part that has already been paid out is treated
   as carats you've collected, so it's in the balance you typed in.

### Misc Earnings *(your toggle, on by default)*

A flat **90 carats a day** standing in for all the small stuff the calculator doesn't
model individually: gifts, career mode clears, odd bits of Team Trials, and so on.

It doesn't start straight away. Nothing is credited for the first **30 days**, and
the drip begins on day 31. After that every extra day in your plan is worth another
90, with no jumps.

### 50-Day Login Campaign *(always on)*

**150 carats every 50 days.** The first payout is 50 days from today. No toggle;
everyone gets it.

### Valentine's Day and White Day *(always on)*

**500 carats every February 14, and another 500 every March 14**, if your plan reaches
that far.

### Training Pass *(your toggle)*

This feature doesn't exist in the game until **9 August 2027** (the date at the time of
writing; it is admin-editable), so nothing is counted before that date. After it
launches:

- **Free tier:** 500 carats on the 1st of each month.
- **Paid tier:** 2,200 carats on the **24th** instead, split as 1,850 free + 350 paid.

They don't stack. The paid pass **replaces** the free 500; it doesn't add to it.

Tickets work differently from carats here. Everyone gets 2 uma + 2 support tickets on
the 24th, and the paid pass adds 2 more of each on top. So a free-tier account gets its
carats on the 1st but its tickets on the 24th.

The paid pass also pays **1 SSR uncap shard a month**. The free tier gets none, so this
is the one reward on the pass with no free-tier version. It feeds the Uncap Crystals
panel like every other shard; 20 of them make a crystal.

### Monthly Shop Tickets *(your toggle, on by default)*

**4 uma + 4 support tickets on the 2nd of each month.** These cost no carats, because
in-game you buy them with a currency this calculator doesn't track.

---

## 4. Worked example

Say today is **Wednesday 29 July 2026**, and:

- You have **12,000 carats**
- Your club rank is **B+** (1,800/month)
- Your Team Trials class is **5** (225/week)
- You have the **Daily Carat Pack**
- **Misc Earnings** is on (the default)
- You've planned one banner that **ends 12 August 2026**

That's a 14-day window: 30 July through 12 August.

**Daily login:**

| Date | Base | Bonus | Day total |
|---|---|---|---|
| Thu 30 Jul | 75 | – | 75 |
| Fri 31 Jul | 75 | – | 75 |
| Sat 1 Aug | 75 | +25 | 100 |
| Sun 2 Aug | 75 | – | 75 |
| Mon 3 Aug | 75 | +25 | 100 |
| Tue 4 Aug | 75 | +75 | 150 |
| Wed 5 Aug | 75 | +25 | 100 |
| *(the same pattern repeats)* | | | |
| **14 days total** | **1,050** | **300** | **1,350** |

**Everything else in the window:**

| Source | Working | Carats |
|---|---|---|
| Daily Carat Pack (daily part) | 14 days × 50 | 700 |
| Daily Carat Pack (500 paid bonus) | first one is due 28 Aug, after the window | 0 |
| Club rank B+ | one 1st-of-month crossed (1 Aug) | 1,800 |
| Team Trials Class 5 | two Mondays (3 Aug, 10 Aug) | 450 |
| Misc Earnings | drip doesn't start until 29 Aug, after the window | 0 |
| 50-Day Login | first one due 17 Sep, after the window | 0 |
| Valentine's / White Day / Training Pass | not in range | 0 |

**Total shown on the banner card:**

```
12,000  starting balance
+1,350  daily login
+  700  Daily Carat Pack
+1,800  club rank
+  450  Team Trials
───────
16,300  carats on 12 August
```

Plus whatever events or a Champions Meeting happen to land in those two weeks.

Notice how much of the "monthly" income shows up as **zero** here. That's on purpose.
Two weeks isn't long enough to reach the 30-day and 50-day payouts, and the calculator
never hands you a fraction of a reward you haven't earned yet.

---

## 5. How the carats get spent

When you set a number of pulls on a banner, the calculator pays for them in a set
order, cheapest resource first, so your expensive carats last as long as possible:

1. **Free pulls the banner itself gives you.** Costs nothing.
2. **Matching tickets.** Uma tickets on uma banners, support tickets on support
   banners. They don't cross over.
3. **Discounted paid pulls**, at 50 paid carats each, but only **one per day of the
   banner's run**. A 13-day banner allows at most 13 of these, and only if you have the
   paid carats. The count is the banner's full length, so it stays the same whether you
   plan the banner a month early or halfway through its run. *(Your toggle; off by
   default.)*
4. **Free carats at 150 each.**
5. **Paid carats at 150 each.** *(Your toggle; on by default.)*

If you plan more pulls than you can pay for, the balance goes negative. That's the
calculator telling you the plan doesn't fit. Nothing stops you from planning that way on
purpose: the "# Pulls" field accepts any number, and the shortfall carries forward into
the banners after it.

**"Max Pulls"** answers a different question: if you threw everything you have at this
one banner, how many pulls would that buy? It uses the same order and the same prices;
it just ignores the number you planned.

### What the colour of the "# Pulls" box means

The field colours itself to show how the number you typed is doing:

| Colour | Meaning |
|---|---|
| **Bright green** | The number lands exactly on a pity threshold (a multiple of 200). Nothing is left in a part-finished pity counter. |
| **Faded green** | Affordable, but part-way through a pity counter. The pulls past the last multiple of 200 don't contribute a guaranteed copy. |
| **Red** | More pulls than "Max Pulls" says you can afford. The number is kept as typed; it just won't be paid for. |

The first two are the same green at different strengths on purpose. A faded box isn't
a warning. It means there are carats sitting in a pity counter you haven't finished.
Nudge the number up to the next multiple of 200 and it brightens.

Red wins when both apply. 400 pulls with only 300 affordable is red, not green, because
not being able to pay is the more useful thing to know. A banner that has already ended
can afford nothing, so any pulls left on it read as red too.

---

## 6. The "average monthly income" figure

This one is calculated separately and works differently. It looks at a fixed **5-month
window starting today**, adds up every carat you'd earn in it, and divides by five.

Two things follow from that:

- It's **income only**. Your pulls are never subtracted from it.
- It ignores your banner plan entirely. It's a "what does this account earn?" number,
  not a "what will I have?" number.

One quirk: because the window is a fixed five months, once-a-year income like
Valentine's Day and White Day only shows up in the average when that window happens to
cover 14 February or 14 March. Being a month apart, five months usually catches both or
neither. The figure will visibly rise and fall as the date rolls past.

---

## 7. Rules that keep the numbers honest

A few design decisions that explain results that might otherwise look odd:

**Adding a banner never inflates your totals.** Each banner's window runs from the
previous banner's end date to its own, and the shared boundary day belongs to exactly
one of them. Without that rule, every banner you added would hand you an extra day's
income.

**Repeating rewards are anchored to today, not to each banner.** The 30-day and 50-day
cycles are counted from today once, and those dates stay put no matter how you slice
your plan. Otherwise every new banner would restart the clock and invent payouts that
don't exist.

**Nothing is credited before it's earned.** The rolling cycles pay on day 30 and day 50,
never a partial amount on day 5. Monthly income needs an actual 1st of the month to
pass. Weekly income needs an actual Monday.

**The numbers are frozen to the calendar day.** Everything is measured from midnight
this morning, so editing your plan doesn't cause the totals to drift by fractions of a
carat as the clock ticks.

**Overlapping banners don't double-count, and each still reports its own date.** Two
banners often overlap: a short one can open later than a long one and still close first.
The calculator drives the road once, so that shared stretch of time is only ever paid
out once. But it takes the checkpoints **in the order the banners close**, not the order
they appear in your list (which is sorted by when they open). That way a banner closing
on the 20th shows you your balance on the 20th, even if the row above it runs to the
24th. Your rows don't move on screen; only the order the calculator visits them in.

**Every row's number depends only on its own end date.** Two banners closing on the same
day show the same balance (minus whatever the first one spent). Deleting a row you
weren't spending anything on leaves every other row as it was.

---

## 8. Why your number might differ from the game

The calculator is a projection, not a promise. The main sources of difference:

- **Your starting balance is whatever you typed.** Nothing is read from the game. If
  it's stale, everything downstream is off by the same amount.
- **Misc Earnings is a flat approximation.** The per-day figure (90 a day at the time of
  writing, and admin-editable) is a reasonable average, not your actual gifts and career
  clears.
- **Rank income assumes you hold your rank.** Get promoted, or slip, and reality
  diverges from the plan.
- **Future dates for unannounced banners are predictions**, based on the Japanese
  server's schedule. Anything marked as predicted can move.
- **The event trickle is a model of typical play.** Clear an event on day one and
  you'll be ahead of the projection; leave it to the last weekend and you'll be behind.
