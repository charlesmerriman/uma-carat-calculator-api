# Analytics Dashboard

A staff-only page inside the Django admin that answers questions like *"what
percentage of users pay for the Daily Carat Pack?"*, *"which banners are people
planning to roll on?"* and *"how much traffic did we get last month?"*

Most of the page aggregates the stats and pull plans that logged-in users
already save through the calculator. The **Site traffic** section is the one
exception: it counts page loads, which means it is the only section that can see
guests. Traffic accumulates its own history; for everything else the server keeps
one copy of the report a day, which the **Overview** and **History** sections
compare against.

## Where to find it

- **Local dev**: <http://localhost:8000/admin/analytics/>
- **Production**: `https://<your-domain>/admin/analytics/`
- There is also a **Reports → Analytics dashboard** link at the top of the
  admin home page (`/admin/`).

You must be signed in to the Django admin with a **staff** account. To create
one:

```bash
# Local dev
python manage.py createsuperuser

# Production (DigitalOcean): open the API component's Console tab and run
python manage.py createsuperuser
```

## Privacy boundaries

- Only **aggregates** are shown: counts, percentages, averages. The page never
  displays usernames, emails, or any individual user's plan.
- **Staff accounts are excluded** from every user metric, so admin/test accounts
  don't skew the numbers.
- **Guests are invisible to the planning sections**: anonymous users plan
  entirely in the browser and never send that data to the server. They *are*
  counted in Site traffic, which is why that section's totals dwarf the account
  numbers.
- **No IP address is ever stored.** Traffic counting hashes IP + user agent with
  a salt that includes the calendar **month**, keeps the hash only to
  deduplicate within that month, and discards it after 90 days
  (`prune_visitor_hashes`, which runs on every deploy).
- **A visitor can be recognised for one month, and no longer.** That span is the
  price of a real monthly-active number — counting someone once per month means
  recognising them across it — and the hash changes completely at every month
  boundary, so nobody can be followed from one month into the next. No cookie or
  client-side identifier is involved, so none of this can be correlated with
  anything outside our own database.
- This use of planning data is disclosed in the site's Privacy Policy
  ("How We Use Your Information"), and traffic counting under "Traffic
  Measurement".

## Reading the numbers

### How fresh they are

The report is built at most once every five minutes and then held, so reloading
the page within that window shows the same numbers, and a CSV downloaded from it
matches what the page showed. The page prints when its copy was built; **Refresh
now** rebuilds it on the spot. The admin home page's stat cards read the same
copy.

### The 30-day comparison and History

The server keeps one copy of the whole report per day (an `AnalyticsSnapshot`
row). Nothing needs scheduling: the first time the report is rebuilt on a new
day, by anyone opening the admin home page or this page, it keeps that day's
copy, and every deploy adds one too (`manage.py snapshot_analytics`). A day when
nobody looked and nothing deployed has no copy, and that is fine.

- **Overview: 30 days ago / Change.** The figure from the nearest copy on or
  before 30 days ago; the help line under the table names its date. Blank until
  a copy that old exists, so the first month after this shipped (2026-10-09)
  shows no comparison.
- **History.** One row per month for the last twelve: users, engaged users, the
  two paid products and selector buyers from that month's **first** copy (where
  things stood as the month began), beside that month's unique visitors. Months
  from before copies began still list their visitors, with the account figures
  blank.
- **The admin home page cards** say "+N since YYYY-MM-DD" from the same
  comparison.

A stored copy is never shown as a page of its own. The report gains sections
between releases, so an old copy can lack a figure a newer page asks for; that
figure reads blank rather than wrong.

### Total vs. engaged users

Every stat on a new account defaults to *off/zero/none*, so accounts that
registered but never touched the calculator would drag every percentage down.
The dashboard therefore reports two denominators:

- **Total users** — every non-staff account.
- **Engaged users** — accounts that have used the calculator in any way a new
  account has not: changed a setting from its default (a rank, a balance, any
  of the nine income toggles, a shop ticket count), planned a banner (in any
  plan) or a campaign purchase, picked step-up cards or a favourite uma, made
  an income profile or a second plan, or set a display name.

The rule behind that list: **if a section counts people who did something,
doing it makes a person engaged.** Otherwise a section's numerator would hold
people its denominator does not, and "% of engaged" could pass 100. A new
feature that gets a row on this page gets a clause in `people.engaged_q()`.

Percentages are shown against both. "% of engaged" is usually the more honest
answer to "what share of our *actual* users do X?".

### Site traffic

Three tables: daily (last 30 days), the last 7 days against the 7 before, and
monthly (last 12).

- **Page views** — one per *browser session*, not per click or per client-side
  route change. The SPA fires a single beacon at `POST /visit` when it loads and
  remembers that it did, so a visitor who reads four pages counts once.
- **Unique visitors, daily** — distinct IP + user-agent buckets seen that day.
- **Unique visitors, monthly** — a true monthly-active count: someone who
  visited on fifteen days counts **once**.

**The monthly figure is smaller than the sum of that month's daily uniques, and
the two are not meant to reconcile.** Adding up thirty daily numbers counts a
regular visitor thirty times; the monthly row counts them once. If the two ever
match exactly, every visitor that month came exactly once.

That sum still means something, as long as it is called what it is:
**visit-days**. The daily table's total row and the weekly comparison report it,
never as "visitors". The monthly table divides it by the month's unique visitors
to give **visit-days per visitor**: ten visitors where one came on twenty days
and nine came once is 29 visit-days over 10 people, 2.9. A month where everyone
came once reads 1.0. The month still running is marked **Partial**, since its
figures only grow.

Days with no traffic are omitted rather than shown as zero. Known crawlers,
`curl` and Python clients are filtered out by user agent, so the numbers are
lower — and more honest — than a raw request count.

Two things do **not** appear here: visits made while running
`npm run dev:live` (the frontend suppresses the beacon when a dev server points
at a remote API, so local work can't inflate production), and anything at all if
a visitor blocks the request.

### Growth, activity and sign-in providers

- **New accounts by month / by week** come from when each non-staff account
  joined. An account deleted since is gone from every row; a purged one still
  counts (the purge keeps the join date). Weeks are seven days ending today.
- **Activity** uses only what is already stored, nothing new about anyone: a
  plan or income profile save stamps `updated_at`, a sign-in stamps
  `last_login_at`. **Sign-ins undercount**: a sign-in lasts until the person
  signs out, so a daily user may not have signed in for months. Saves are the
  better signal. "Came back after their first week" asks, of accounts older than
  a week, how many saved or signed in more than a week after joining; each row
  shows its own denominator in "Out of". Django's `last_login` is not
  maintained (token auth never calls `login()`) and is never read.
- **Sign-in providers** counts people per provider, people with two or more,
  and password accounts with none. No provider id ever leaves the database.

### Supporters

Active Patreon supporters by tier, in tier order, with how many are linked to a
site account (staff accounts left out) and how many agreed to be named publicly.
These are **patrons, not users**: someone can support without an account here,
so the staff exclusion that applies to every user figure does not apply to the
tier counts. The supporter's email is never read. History tracks the total.

### Feature adoption

One row per feature shipped since the dashboard was built, with how many people
use it and the share of engaged users: more than one plan, an income profile, a
note, two-card odds, a two-card target, reserved copies, step-up cards, a
favourite, two or more favourites, a campaign purchase, shop ticket counts,
tickets kept off banners, a display name. Anything on a planned banner reads the
active plan only. The list, with the filter that defines each row, is `FEATURES`
in `analytics/people.py`.

### Favourite umas

The 20 umas most often picked as a favourite, by people, with how many show it
as their account picture (their first favourite). A costume is its own uma.

### Income settings and shop tickets

Every income toggle a person can switch (all nine booleans on their stats), led
by the two purchasable income sources, **Daily Carat Pack** and **Training
Pass**. Counted **among engaged users only**: five of the nine start on, so they
are on for every account that never opened the calculator too, and counting
those would make "users on" mostly lurkers.

- **Users on** / **% on** — engaged users with it on right now.
- **Changed from default** / **% changed** — engaged users who switched it from
  where a new account starts: on for a setting that starts off, off for one
  that starts on. For a setting that starts on, this is the number that says
  something; for one that starts off it equals Users on.

**Shop tickets bought a month** lists how many monthly shop tickets people say
they buy, uma and support. "Not set" follows the default an editor sets in
Calculation constants. The counts only move the projection while Monthly shop
tickets is on, but are kept either way.

### Campaign selectors

**Campaign selectors** lists every selector product a campaign
sells (Uma and Support), with how many people plan to buy it:

- **Users** — distinct people with the selector in their planned purchases.
  Someone who plans the same selector for their own account and for an income
  profile is counted once.
- **Card picked** — how many of those have chosen the card on the Selectors
  page. A selector with no pick funds nothing in their projection, so the gap
  between the two columns is people who have not decided yet.
- **Any selector** — people planning at least one selector. It is not the sum
  of the rows above, because someone buying two selectors appears in both.

This is what people *plan* to buy in the calculator, not a record of real
purchases. Carat packs are not listed.

### Rank distributions

For each income rank type (Team Trials, Club Rank, Champion's Meeting, League
of Heroes): how many users selected each rank. Rows are ordered by the rank's
income amount (game progression order); **Not set** counts users who never
picked one.

Both this section and the next read `CustomUser` only: a person's own account.
Income profiles (the stats a plan keeps for the person's other game account,
`IncomeProfile`) are not counted, so the distributions describe people, not
game accounts.

### Current resources

Median, quartiles, share at zero, mean and dropped-value count for each
resource field (carats, tickets, crystals, shards, selector tickets) across
**engaged users only**.

**Read the median, not the average.** Carat balances are long-tailed — a
handful of genuine whales pull a mean well above where most people actually
sit, even when every value in the set is honest. A median cannot be moved by an
extreme value at all, which makes it the figure that answers "what does a
typical user have?"

**p25 and p75** say how spread out people are: half of everyone sits between
them. Nine people holding 0, 0, 2,000, 5,000, 9,000, 12,000, 30,000, 45,000 and
400,000 carats give a median of 9,000, p25 2,000 and p75 30,000, so "half hold
between 2,000 and 30,000", while the mean (55,889) says "whale". **At zero** is
the share holding none, which the quartiles cannot show once it passes a
quarter.

### Popular banners

Separate tables for Uma and Support banners. **Dates are effective dates**:
confirmed once the game announces them, predicted before that (the same
prediction the planner shows, marked in the **Predicted** column). **Status** is
upcoming, running or ended. On the page, ended banners are folded under
**Ended** below each table, since people leave finished banners in their plans;
the CSV keeps every row. The admin home page's "Top planned banner" card skips
ended ones for the same reason.

Rows are ranked by:

- **Planners** — how many distinct users have this banner in their plan (the
  primary popularity signal)
- **Total pulls** — the sum of pulls everyone has budgeted for it
- **Avg pulls** — total pulls ÷ plan rows (how invested each planner is)
- **Ignored values** — plan rows whose pull count was too large to be a real answer

**Only each account's active plan is read.** An account can hold several
plans, and the spare ones are what-ifs: someone comparing "200 pulls" against
"skip it" on the same banner intends one of those, not both. Counting every
plan would report demand from a person who may intend none, and would let one
user with five copies of a plan move an average five times. The active plan is
the one they have open, so it is the best single answer to "what does this
person plan to do", and it keeps every figure here meaning what it meant when
an account had exactly one plan. The **Planned by** column on the admin's Uma
banners and Support banners lists applies the same rule, so the two agree on
which plans count (that column still includes staff accounts, which the
dashboard leaves out).

Being *engaged* is the one place a spare plan still counts: planning a banner
in any plan is using the calculator, so that person is in the engaged
denominator even if the banner tables never show them.

### Step-up banners

One row per step-up anyone plans or picked cards for. **Step-up plans are in
steps, never pulls**: one step is one 10-pull bought with paid carats, at most
five per banner, so these rows have Total steps and Avg steps and never feed a
pull total. **Chose their cards** counts everyone who picked their own ten,
planning to climb or not; an untouched step-up shows a default ten and stores
nothing, so it is not counted. Plans above 50 steps are ignored as implausible
(`SANE_MAX_STEPS`).

### Demand by month

Planned Uma and Support banners grouped by the **month the banner ends**, which
is when the carats leave a saving plan, for this month and the next five, then
**Later** (everything after, and anything still undated). Ended banners are left
out. Per month: how many banners have planners, how many people plan at least
one (someone with two banners that month counts once), the pulls they budget
(implausible rows ignored), and how many people plan a step-up there, counted
apart because those plans are in steps.

### Implausible values

Nothing stops a user typing 999,999,999 into a pull or resource field, and the
API accepts it deliberately: sandboxing "what if I had a billion carats" and
watching the projection respond is a reasonable thing to want from a
calculator. It is also, unfiltered, enough to break this page — a single such
account was adding ~169,000 to every resource mean and reporting one banner's
average as 447,572 pulls.

So the dashboard excludes values above a sanity ceiling
(`SANE_MAX_PULLS` / `SANE_MAX_RESOURCE` in `calculatorapi/analytics/common.py`) from
every figure that treats a stored number as a **quantity**, and from none of
the figures that merely **count people**. Three consequences worth knowing:

- **Planners is not filtered.** Someone who typed a billion into a pull field
  really does have that banner planned, and popularity should say so. Only
  their *number* is discarded.
- **Filtering is per field, not per user.** An account with plausible carats and
  an absurd crystal count still contributes its carats.
- **Nothing is rewritten.** This page filters what it reads; it never edits a
  saved plan. A user's own projection still shows them their billion.

Each affected row reports its **Ignored values** count, so a surprising figure can be
checked against the number of exclusions behind it. The ceilings sit orders of
magnitude above any real answer (2,000 pulls is ten pity copies on one banner,
double what maxing it out costs; 10,000,000 carats is ~66,000 pulls' worth) —
deliberately, because wrongly dropping a real whale would bias the report
silently, while a ceiling this high can only catch values that were never
answers.

## CSV export

The **Download CSV** button (or `?format=csv`) exports every table into a
single dated file (`analytics-YYYY-MM-DD.csv`) that opens directly in Google
Sheets or Excel: use it for charts or to share numbers. It is the same copy of
the report the page shows, History section included.

There is no need to keep monthly downloads for trends any more: the server
keeps a daily copy and the History section reads it (see "The 30-day comparison
and History" above). A download is still the way to keep a figure History does
not track, such as a banner's planners on a given day.

## Implementation notes (for developers)

- **Every row-based figure starts from `banners.active_rows()`**: non-staff,
  active plan (plus the TRANSITIONAL plan-less half until multi-plan release 2).
  **Engaged is built from `Exists()` subqueries** (`people.engaged_q()`), never
  joins through reverse relations, which would multiply each user's rows by
  every related table at once. Banner dates come from ONE
  `predictions.build_effective_date_maps()` call per report.
- All aggregation lives in the `calculatorapi/analytics/` package, pure ORM
  queries with no HTTP concerns. `build_analytics_report()` (`report.py`)
  assembles one plain dict from the section modules (`people.py`,
  `income_settings.py`, `banners.py`, `traffic.py`); the package's `__init__`
  docstring maps them. Import from the package, never from a module inside it.
- **The dict is the contract; `tables.py` is presentation.** `report_tables()`
  turns the dict into a list of sections (title, help, columns with a *kind*,
  rows), and the page and the CSV are each one loop over that list, so they
  cannot disagree about a title or a column. The tests and the KPI cards read
  the dict.
- **Adding a section is two steps:** a function in the right section module
  that returns its rows (aggregates only, staff excluded), called from
  `build_analytics_report()`; and one `_section(...)` entry in
  `report_tables()` naming its columns. Neither the template nor the CSV writer
  changes. `AnalyticsTablesTests` fails if a row is missing a column's key, so
  give the new section a row in its seed. A new key changes the dict's shape,
  so bump `REPORT_CACHE_KEY`'s suffix in the same change.
- Every reader goes through `get_report()`, which holds the report in the
  default cache for five minutes under `analytics:report:v1`: the page, its CSV
  and the admin index's KPI cards (`admin_dashboard.dashboard_callback`, which
  runs on every `/admin/` load). Bump the key's suffix when the dict's shape
  changes. `?refresh=1` rebuilds and redirects back to the plain URL.
- Traffic counting lives in `calculatorapi/visits.py` — same split:
  `record_visit()` writes, `build_visit_report()` reads, and neither knows about
  HTTP responses. `views/visits.py` is the `POST /visit` endpoint (public,
  throttled, always 204 and never a body, so the bot filter can't be probed).
- **Snapshots** (`analytics/snapshots.py`, model `AnalyticsSnapshot`, one row
  per UTC day, unique on `date`). `cache.get_report()` calls `ensure_today()` on
  every rebuild, never on a cache hit: `exists()` first, then `get_or_create`,
  so a race leaves one row. The stored dict leaves out `NOT_STORED` (the traffic
  lists, which have their own tables, and `comparison`/`history`, which are
  built from snapshots). `shape` records `common.REPORT_SHAPE`. **Read a stored
  report only through `snapshots.figure()`**, which answers None for a key an
  older shape lacks; to track a new number over time, add it to
  `snapshots.FIGURES`. Not in the admin, listed in
  `content_snapshot.PRIVATE_MODELS` (never pulled to local) and in
  `public_payload_cache._IRRELEVANT_MODELS` (writing one must not drop the
  `/calculator-data` cache). About 365 rows a year, never pruned.
- `DailyVisit` and `MonthlyVisit` are the permanent records; `VisitorHash` is
  disposable deduplication scratch, dropped after 90 days by
  `manage.py prune_visitor_hashes`, which runs on every deploy (the
  `run_command` in `.do/app.yaml`; a live-spec change, see that file). None are registered in the admin, on purpose — they are
  reporting output, and a hand-edited counter is worse than no counter.
- **Monthly uniques cannot be derived from `DailyVisit` after the fact**, which
  is why `MonthlyVisit` exists as its own counter rather than a `TruncMonth`
  aggregate. They are accumulated as visits arrive, against the month-scoped
  hash.
- **Do not drop the retention window below ~45 days.** The monthly check asks
  "any row for this hash since the 1st?", so pruning a visitor's earlier rows
  mid-month would count them twice.
- `_client_ip()` **must** read `X-Forwarded-For`. On App Platform every request
  reaches Django from the load balancer, so trusting `REMOTE_ADDR` would make
  every visitor hash identically and unique visitors would read 1 forever.
- The view (`calculatorapi/views/analytics.py`) is wrapped with
  `admin.site.admin_view()` in `calculatorproject/urls.py`, which enforces the
  staff-only requirement and redirects everyone else to the admin login.
- Tests: `tests/test_analytics.py` (cache, table shape, snapshots, page and
  CSV), `tests/test_analytics_sections.py` (each section's figures) and
  `tests/test_visits.py` (the visit counters). Run all three after an
  analytics change.
