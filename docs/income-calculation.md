# Income Calculation Reference

> **Where these numbers live now.** Every flat rate and schedule on this page is
> a field on the `CalculationConstants` singleton, editable in Django admin under
> **Configuration → Calculation constants** and served to the frontend on every
> `/calculator-data` request — changing one no longer needs a deploy. The model's
> field defaults are the current calibration and each carries a `help_text`
> naming the source spreadsheet cell. The per-rank and per-event amounts (rank
> tables, `GameEvent` rewards, campaign products) remain ordinary rows as
> described below.
>
> Two of those constants are known to differ from the sheet and are deliberately
> left at our current values until the parity harness can measure the change —
> see the `/sheet-parity` skill.

This document records the game mechanics behind every carat and ticket income source encoded in the backend fixtures and reflected in the frontend projection engine. Update this file whenever real game values change.

---

## Daily Base Income

Every day grants **75 carats**.

On top of that, certain days within a rolling 7-day cycle grant bonuses. The cycle is anchored to the current date (the day the user opens the calculator):

| Days since anchor (mod 7) | Bonus |
|---|---|
| 0 (first day of each week) | +25 carats |
| 3 | +25 carats |
| 5 | +25 carats |
| 6 (last day of each week) | +75 carats |

The cycle repeats every 7 days. Day 0 is anchored to the day the user opens the calculator.

---

## Daily Carat Pack (opt-in)

If the user enables the **Daily Carat** toggle, the pack contributes income of two different
kinds:

| Part | Amount | Schedule | Balance credited |
|---|---|---|---|
| Daily drip | **50 carats/day** | every day in the projection window | **free** carats |
| Purchase bonus | **500 carats** | every 30 days, first payout 30 days out | **paid** carats |

The daily 50 is a separate daily login reward distinct from the base 75, and behaves like
every other income source — it lands in the free/earned balance.

The **500-carat purchase bonus** is the money part of the pack, so it lands in the **paid**
balance (`current_paid_carat`) — one of only three things in the projection that credit
paid carats, and therefore a main way discounted paid pulls stay funded over a long
horizon. (The others are the paid Training Pass's 350/month, below, and campaign
purchases from the Selectors page — see "Campaign purchases" at the end of this
document.)

Its schedule is a rolling 30-day cycle **anchored to the day the user opens the calculator**,
identical to the 50-Day Login Bonus machinery below (`calculateIntervalOccurrences`). The first
payout lands on day 30, then day 60, day 90, and so on — never on day 0, since the pack the
user holds *today* is assumed to be already reflected in the paid-carat balance they entered.
A banner ending sooner than 30 days out therefore sees no purchase bonus at all. Anchoring to
today rather than to each banner's window keeps the payout instants absolute, so adding or
removing banners never changes the total. Constants: `DAILY_CARAT_PACK_PER_DAY`,
`DAILY_CARAT_PACK_PAID_CARATS` / `DAILY_CARAT_PACK_CYCLE_DAYS` in
`frontend/src/constants/gameConstants.ts`.

---

## Misc Earnings Approximation (daily drip after a 30-day ramp-in, toggle — on by default)

A flat **`misc_earnings_monthly / 30` carats per day** — 90 with the live value of 2,700 —
approximating miscellaneous earnings the projection doesn't
model individually — gifts, Team Trials extras, and career-mode rewards. This mirrors the
source sheet's "Misc Earnings" figure, which the projection is calibrated against; the
sheet accrues it per-day in exactly this way.

Gated behind the user's `misc_earnings` boolean (`CustomUser.misc_earnings`,
`default=True`). Unlike the other flat incomes here, it is **not** credited on
1st-of-month boundaries, and unlike the rolling-cycle incomes it doesn't arrive in lumps:
after a **30-day ramp-in anchored to the day the user opens the calculator**, every single
day earns that amount. Days 1–30 earn nothing (so a banner ending inside the ramp-in gets none of
it) and the drip starts on day 31. Anchoring to today rather than to each banner's window
keeps the drip's start instant absolute, so adding or removing banners never changes the
total — see `countDaysAfterDelay`, which clamps each window's start forward to that
instant before counting days. Constants: `MISC_EARNINGS_PER_DAY` /
`MISC_EARNINGS_DELAY_DAYS` in `frontend/src/constants/gameConstants.ts`.

> **Why a drip, not a lump.** This previously paid **1,800 every 30 days**. Same long-run
> rate (60 × 30 = 1,800), but the lump produced a 0–1,800 sawtooth against the sheet: a
> banner's estimate jumped by 1,800 the moment its end date crossed a cycle boundary, so
> two plans a day apart could read very differently for no real reason.

---

## Monthly Shop Tickets (monthly, toggleable — on by default)

Each month the in-game shop lets you buy **4 uma tickets** and **4 support tickets** with a
currency the calculator doesn't track, so when enabled these are credited as pure ticket
income at **no carat cost**. Gated behind `CustomUser.monthly_shop_tickets` (`default=True`).
Credited on the **2nd of each month** (`MONTHLY_SHOP_TICKET_DAY`) via
`calculateDayOfMonthOccurrences` — *not* on the month boundary Club Rank uses. Constants
`MONTHLY_SHOP_UMA_TICKETS` / `MONTHLY_SHOP_SUPPORT_TICKETS` in
`frontend/src/constants/gameConstants.ts`.

**Setting your own count.** The 4 + 4 above is the DEFAULT purchase
(`CalculationConstants.monthly_shop_uma_tickets` / `_support_tickets`, the sheet's figure). The
shop sells up to **9 of each** (`monthly_shop_uma_tickets_max` / `_support_tickets_max`), and
`shop_uma_tickets_bought` / `shop_support_tickets_bought` (on `GameStats`, so both the account
and an `IncomeProfile` carry them) hold a player's own monthly count, from 0 to that cap.
`NULL` means the default, and the Settings menu stores a count equal to the default as `NULL`
so it keeps following the default if an editor changes it. The server checks only that the
count is not negative; the cap is applied where the count is read, by `shopTicketsPerMonth`
in `frontend/src/utils/cumulativeIncome.ts`, so a count saved before the cap was lowered never
rejects a save. Both are ignored while the toggle is off.

---

## 50-Day Login Bonus (rolling 50-day cycle, always on)

**150 carats** each time the game's recurring 50-day login campaign completes. This is
universal income (like the base daily carats), so there is **no toggle** — it always
applies.

Its schedule is a rolling 50-day cycle **anchored to the day the user opens the
calculator**, the same machinery as the Daily Carat Pack purchase bonus
(`calculateIntervalOccurrences` — Misc Earnings drips daily instead). The first payout
lands on day 50, then day 100, day 150,
and so on — never on day 0, since the campaign the user is partway through right now is
assumed already reflected in the balance they entered. A banner ending sooner than 50 days
out therefore earns none of it. Anchoring to today rather than to each banner's window
keeps the payout instants absolute, so adding or removing banners never changes the total.

Constants: `FIFTY_DAY_LOGIN_PER_CYCLE` / `FIFTY_DAY_LOGIN_CYCLE_DAYS` in
`frontend/src/constants/gameConstants.ts`.

---

## Annual Gifts — Valentine's Day and White Day (annual, always on)

**500 carats** on **February 14** (Valentine's) and **500 carats** on **March 14** (White
Day, the reciprocal gift a month later). Both are universal income with **no toggle**,
credited in full on the day itself.

Unlike the rolling-cycle incomes above, these are fixed calendar dates, so they use
`calculateAnnualDateOccurrences` — absolute annual instants, counted the same half-open
`(start, end]` way as the 1st-of-month incomes so a gift date landing on a banner
boundary is paid by exactly one window. Constants: `VALENTINES_CARATS`, `VALENTINES_MONTH`
(0-indexed, so `1` = February), `VALENTINES_DAY` and `WHITE_DAY_CARATS`, `WHITE_DAY_MONTH`
(`2` = March), `WHITE_DAY_DAY` in `frontend/src/constants/gameConstants.ts`.

> **Where White Day's 500 comes from.** The source sheet doesn't expose it as a settings
> cell — it buckets both gifts into its "50 Day Login Bonus" column. That column reads 150
> over a 62-day window (one 50-day payout, neither gift date in range) and 2,050 over a
> 366-day window (seven 50-day payouts = 1,050, plus 500 + 500). The sheet's changelog entry
> 4.44 confirms the two gifts are modelled as a pair. Worth re-checking against a primary
> source if the figure is ever disputed.

> Note: the Income tab's *average monthly* figure (`useAverageMonthlyIncome`) averages a
> fixed 5-month forward window, so these only show up there when that window happens to span
> February 14 / March 14. Because the dates are a month apart, a 5-month window usually picks
> up both or neither. The per-banner projection is unaffected and always counts them
> correctly.

---

## Training Pass (monthly, day 24)

> **Available from August 15, 2027.** No training pass income is projected for any period before this date — neither the paid reward nor the free tier.

Carats and tickets behave differently: the paid carat reward **replaces** the free tier's, while the tickets **stack** on top of it. The SSR shard is a third case again — paid tier only, with no free-tier amount to stack on.

| State | Carats | Uma tickets | Support tickets | SSR shards |
|---|---|---|---|---|
| Training pass active | **+2,200** on the 24th of each month (**1,850 free + 350 paid**) | **4/month** (2 free + 2 paid bonus) | **4/month** (2 free + 2 paid bonus) | **1/month** |
| No training pass | **+500** per calendar month (free tier, all free carats) | **2/month** | **2/month** | none |

The 500-carat figure is the free tier of the Training Pass — it applies to all accounts once the feature launches, regardless of whether the paid pass is active. The same is true of the 2 free-tier tickets of each type. The shard is not: it is a paid-pass perk, so a free-tier account earns none.

**The SSR shard is the only uncap income that is not dated.** Every other shard and crystal in the projection comes from a `GameEvent`'s reward amounts or a Champions Meeting / League of Heroes payout, all of which land on a specific instant. The pass's shard is monthly, on the same clock as its carats and tickets, so it does not go through the event/race accrual at all — it is added by `cumulativeTrainingPassIncome` and reaches all three places that report shards (the banner rows, the "Income & Resources" tiles, and the Uncap Crystals panel) from that one helper. Constant: `training_pass_paid_ssr_shards`, mirrored by `TRAINING_PASS_PAID_SSR_SHARDS` in `frontend/src/constants/gameConstants.ts`.

**Free/paid carat split.** Like the Daily Carat Pack, part of the paid pass's reward is purchased currency: 350 of the 2,200 land in the **paid** balance (the only one that can buy discounted pulls), the other 1,850 in the free balance. The free tier's 500 is entirely free carats. The 2,200 total is unaffected, so with the default toggles (discounted pulls off, full-price paid pulls on) projections are unchanged — the split only matters to accounts using discounted pulls. Constants: `TRAINING_PASS_MONTHLY_FREE_CARATS` / `TRAINING_PASS_MONTHLY_PAID_CARATS` in `frontend/src/constants/gameConstants.ts`, with `TRAINING_PASS_MONTHLY_REWARD` derived from their sum.

**Payout day.** All tickets — free tier included — are delivered on the 24th, because the pass resets as a unit. The free tier's 500 carats remain on the 1st of the month, so a free-tier account draws its carats and its tickets on different days. Constants: `TRAINING_PASS_FREE_UMA_TICKETS`, `TRAINING_PASS_FREE_SUPPORT_TICKETS`, `TRAINING_PASS_PAID_BONUS_UMA_TICKETS`, `TRAINING_PASS_PAID_BONUS_SUPPORT_TICKETS` in `frontend/src/constants/gameConstants.ts`.

---

## Club Rank (monthly, 1st of each month)

Paid once per calendar month on the 1st. The projection counts how many month boundaries fall between the previous banner's end date and the current banner's end date.

| Rank | Monthly income (carats) |
|---|---|
| D+ | 225 |
| C | 450 |
| C+ | 900 |
| B | 1,350 |
| B+ | 1,800 |
| A | 2,250 |
| A+ | 2,700 |
| S | 3,150 |
| S+ | 3,600 |
| SS | 4,500 |

---

## Team Trials Rank (weekly, every Monday)

Paid once per week on Monday. The projection counts how many Mondays fall in the window.

| Rank | Weekly income (carats) |
|---|---|
| Class 1 | 0 |
| Class 2 | 35 |
| Class 3 | 75 |
| Class 4 | 150 |
| Class 5 | 225 |
| Class 5.5 | 262 |
| Class 6 | 375 |

---

## Champions Meeting Rank (per event)

Paid once per Champions Meeting event. The projection adds this payout for each `ChampionsMeeting` whose `end_date` falls within the window. Placements grant both carats and pull tickets; the in-game pull-ticket reward is split evenly between uma and support tickets.

| Placement | Carats | Uma tickets | Support tickets |
|---|---|---|---|
| Champion | 3,300 | 5 | 5 |
| Second | 2,400 | 4 | 4 |
| Group B 1st | 1,800 | 3 | 3 |
| Third | 1,600 | 3 | 3 |
| Open League 1st | 1,500 | 3 | 3 |
| Group B 2nd | 1,250 | 2 | 2 |
| Open League 2nd | 1,250 | 2 | 2 |
| Group B 3rd | 1,000 | 1 | 1 |
| Open League 3rd | 1,000 | 1 | 1 |

---

## League of Heroes Rank (per event)

Paid once per League of Heroes event. The projection adds this payout for each `LeagueOfHeroes` whose `end_date` falls within the window. Ranks grant both carats and pull tickets; the in-game pull-ticket reward is split evenly between uma and support tickets.

| Rank | Carats | Uma tickets | Support tickets |
|---|---|---|---|
| Silver 4 | 400 | 0 | 0 |
| Gold 1 | 550 | 0 | 0 |
| Gold 2 | 700 | 1 | 1 |
| Gold 3 | 1,000 | 1 | 1 |
| Gold 4 | 1,300 | 2 | 2 |
| Platinum 1 | 1,800 | 2 | 2 |
| Platinum 2 | 2,300 | 2 | 2 |
| Platinum 3 | 2,800 | 2 | 2 |
| Platinum 4 | 3,300 | 2 | 2 |

---

## Event Rewards

Each `GameEvent` carries its own reward amounts directly (no separate reward model): `carat_amount`, `carats_throughout`, `uma_ticket_amount`, `support_ticket_amount`, `sr_shard_amount` / `sr_crystal_amount`, `ssr_shard_amount` / `ssr_crystal_amount`. These fall into two categories:

- **Immediate (`carat_amount` + all ticket/shard/crystal amounts)**: earned in full as soon as the event's own resolved `start_date` passes. The projection adds these if `start_date` falls strictly after the previous banner's end date and on or before the current banner's end date, same rule as the other date-gated income sources.
- **Throughout (`carats_throughout`)**: carats only, front-loaded across the event's own `start_date`..`end_date` span rather than granted in one lump or spread at a flat rate. More of the pool is earned early in the event's life, tapering off toward `end_date` (where it reaches exactly 100% earned, with no early cutoff — the event's `end_date` already trails the linked banner's resolved end by a flat 4 days, and that whole span earns continuously through to the last day). The decay curve blends a fast exponential leg with a slower linear leg; see `remainingThroughoutForRow` in `frontend/src/utils/incomeLedger.ts` for the exact formula. Independent of `start_date`.

---

## Pull Costs

After recording the forecast for a banner, the projection deducts the pull cost. Carats are
tracked as two separate balances: **free carats** (`current_carat`, which receive nearly all
income) and **paid carats** (`current_paid_carat`, purchased with money — they grow only from
the Daily Carat Pack's 500-carat purchase bonus every 30 days and the paid Training Pass's
350 carats on the 24th).
The spend order for a banner's `number_of_pulls`, after subtracting the banner's `free_pulls`,
is:

1. **Matching tickets** *(if `spend_tickets_on_banners` on, default)* — uma tickets for an
   uma banner, support tickets for a support banner. With the toggle off the step is skipped:
   tickets pay for nothing, carry forward untouched as a reserve, and are left out of
   "Max Pulls" and its breakdown.
2. **Discounted paid pulls** *(if `discounted_paid_pulls` on)* — a once-per-day option to
   spend **50 paid carats** instead of 150 for a single pull. Capped at one pull per day of
   the banner's window and by the paid-carat balance (paid-carats only; the discount stops
   the moment they run out). The day cap is the window's **full length**, counted from the
   banner's start date regardless of how many of its days have already elapsed — so the
   allowance doesn't shrink under a user while the banner is live.
3. **Free carats** — 150 per pull.
4. **Full-price paid carats** *(if `full_price_paid_pulls` on, default)* — 150 per pull. When
   this toggle is off, paid carats are held in reserve and never spent at full price.

Free carats are spent before full-price paid carats so more daily discounts stay available
for later banners. Full-price pulls treat free + (enabled) paid carats as one fungible
150-carat pool. Any pulls that still can't be paid for become a negative free-carat balance,
signalling an unaffordable plan (which cascades to later banners). See
`applyPullStrategy` in `frontend/src/utils/bannerHelpers.ts`, which also computes the
banner's "Max Pulls" figure from the same strategy.

Constants: `PULL_COST_CARATS` (150) and `DISCOUNTED_PULL_COST_CARATS` (50) in
`frontend/src/constants/gameConstants.ts`. Toggles live on `CustomUser`
(both `default=True`, as are the other two projection toggles).

---

## Step-Up Ladder Costs

A **Select Step-Up** row does not spend through the strategy above at all. It buys steps on
a five-step ladder, with **paid carats only** — no tickets, no free pulls, no free carats,
no daily discount:

| Step in round | 1 | 2 | 3 | 4 | 5 | Round |
|---|---|---|---|---|---|---|
| Cost | 500 | 700 | 1,000 | 1,300 | 1,500 | **5,000** |
| Cumulative | 500 | 1,200 | 2,200 | 3,500 | 5,000 | |

Each step is a 10-pull, so a full round is 50 pulls for 5,000 paid carats against 7,500 at
the standard 150-per-pull rate.

The five costs **repeat**, which is why they are five constants rather than a table:

```
cost(n) = floor(n / 5) * 5000 + cumulative[n % 5]
```

Constants live on `CalculationConstants` (editable in admin, served on every request):

| Constant | Default | Meaning |
|---|---|---|
| `step_up_cost_step_1` … `_5` | 500 / 700 / 1000 / 1300 / 1500 | One round of the ladder |
| `step_up_pulls_per_step` | 10 | Each step is a 10-pull |
| `step_up_target_rate` | 0.003 | The ~3% pool rate split across the player's 10 picks |
| `step_up_max_rounds` | 7 | Sanity bound on absurd data, **not** the live constraint |

The live ceiling is a step-up's own `banner_count * 5`, which is always lower —
`step_up_max_rounds` exists only to stop a mis-entered `banner_count` producing an absurd
projection.

### Rate-up rates (odds only, no carats)

An ordinary banner's odds read the rate-up rule's six constants, also on
`CalculationConstants`. They move no carat total, only the copy-count strip:

| Constant | Default | Meaning |
|---|---|---|
| `rate_up_rate_3` / `rate_up_pool_3` | 0.0075 / 0.03 | ★3 / SSR: each card's rate, and the most they share |
| `rate_up_rate_2` / `rate_up_pool_2` | 0.0225 / 0.03 | ★2 / SR |
| `rate_up_rate_1` / `rate_up_pool_1` | 0.0375 / 0.05 | ★1 / R |

The rule, its exceptions and where the numbers came from: `data-model.md`, "Rate-up rates".

**Contention worth knowing:** step-ups and discounted pulls draw from the same paid-carat
pool, and walk order (banner start date) decides which drains it first. A step-up planned
earlier in the timeline can leave a later banner unable to fund its discounted pulls.

See `frontend/src/utils/stepUpLadder.ts` and `applyStepUpStrategy` in
`frontend/src/utils/bannerHelpers.ts`.

---

## Campaign purchases (Selectors page)

The third and only non-recurring source of paid carats. A user plans, per anniversary or
other paid campaign, how many discounted carat packs to buy and which selector tickets to
claim; each planned line credits **paid carats** — plus **free** carats when the webstore
bonus applies, see below — and accumulates a USD total.

**Off by default.** Nothing reaches the projection until `include_purchases_in_projection`
is switched on. Until then the Selectors page is pure budgeting and every banner estimate
is byte-identical to what it was before the feature existed.

**Amounts** come from the database, not from constants — see
`AnniversaryEventProduct` in `backend/docs/data-model.md`. Transcribed from the sheet:

| Pack | USD | Carats | Webstore multiplier |
|---|---|---|---|
| 11000 Carat Pack | $140 | 11,000 | 1.2x |
| 7500 Carat Pack | $70 | 7,500 | 1.1x |
| 1500 Carat Pack | $14 | 1,500 | 1.1x |
| Spark Enhancement | $21 | 1,500 | 1.1x |

Selector tiers are Free ($0, 0 carats), $21 (1,500 paid carats) and $70 (7,500 paid
carats), each granting one selector ticket. Confirmed against the sheet's own totals: the
1st Anniversary's two $21 selectors sum to "3,000 Carats / $42".

**Webstore bonus.** When `webstore_bonus` is on, each pack's carats are multiplied by its
own rate — and the two halves are **different currencies**. The pack's own carats stay
**paid**; the extra the webstore adds on top is granted as **free** carats. So a $140 pack
bought through the webstore adds 11,000 paid and 2,200 free, not 13,200 paid.

That distinction is load-bearing rather than cosmetic: only paid carats fund discounted
pulls and step-ups, so folding the bonus into the paid figure would quietly enlarge the
budget a step-up is allowed to spend. `purchaseCarats()`
(`frontend/src/utils/campaignPurchases.ts`) is the single place the split is computed, read
by both the Selectors page totals and the per-banner projection. It derives the bonus as
`total - paid` so the halves always re-sum to the same rounded total.

**When it lands.** At the campaign's resolved **start** — packs go on sale when the
campaign opens. That instant is absolute, which is what keeps the banner windows tiling;
counting purchases per-window instead would inflate totals as soon as a user split their
plan across more banners. A campaign with no resolved date (no linked banner parts) is
skipped rather than credited at a made-up fallback.

**Selector tickets are not gacha tickets** and never enter `applyPullStrategy`. They are
tracked as a *bucketed* pool keyed by JP cutoff, because two tickets with different
cutoffs are different resources — see
`frontend/docs/resource-projection-logic.md` and `frontend/src/utils/selectorTickets.ts`.

**Deliberately excluded from `useAverageMonthlyIncome`.** Purchases are one-off events,
not recurring income; averaging them would corrupt the monthly strip. This is the one
place the "mirror every new income source in both hooks" rule is broken on purpose.
