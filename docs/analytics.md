# Analytics Dashboard

A staff-only page inside the Django admin that answers questions like *"what
percentage of users pay for the Daily Carat Pack?"*, *"which banners are people
planning to roll on?"* and *"how much traffic did we get last month?"*

Most of the page aggregates the stats and pull plans that logged-in users
already save through the calculator. The **Site traffic** section is the one
exception: it counts page loads, which means it is the only section that can see
guests, and the only one that accumulates history rather than reporting a
snapshot.

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

### Total vs. engaged users

Every stat on a new account defaults to *off/zero/none*, so accounts that
registered but never touched the calculator would drag every percentage down.
The dashboard therefore reports two denominators:

- **Total users** — every non-staff account.
- **Engaged users** — accounts that changed at least one calculator setting
  (a rank, a resource amount, a paid-product toggle) **or** planned at least
  one banner or one campaign purchase.

Percentages are shown against both. "% of engaged" is usually the more honest
answer to "what share of our *actual* users do X?".

### Site traffic

Two tables, daily (last 30 days) and monthly (last 12).

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

Days with no traffic are omitted rather than shown as zero. Known crawlers,
`curl` and Python clients are filtered out by user agent, so the numbers are
lower — and more honest — than a raw request count.

Two things do **not** appear here: visits made while running
`npm run dev:live` (the frontend suppresses the beacon when a dev server points
at a remote API, so local work can't inflate production), and anything at all if
a visitor blocks the request.

### Paid products

Adoption of the two purchasable income sources — **Daily Carat Pack**
(`daily_carat`) and **Training Pass** (`training_pass`). A user "has" the
product if the toggle is on in their income settings right now.

Below those, **Campaign selectors** lists every selector product a campaign
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

Median, mean and dropped-value count for each resource field (carats, tickets,
crystals, shards) across **engaged users only**.

**Read the median, not the average.** Carat balances are long-tailed — a
handful of genuine whales pull a mean well above where most people actually
sit, even when every value in the set is honest. A median cannot be moved by an
extreme value at all, which makes it the figure that answers "what does a
typical user have?"

### Popular banners

Separate tables for Uma and Support banners, ranked by:

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

## CSV export & tracking trends over time

The **Download CSV** button (or `?format=csv`) exports every table into a
single dated file (`analytics-YYYY-MM-DD.csv`) that opens directly in Google
Sheets or Excel — use it for charts or to share numbers.

**Site traffic is the only section with history.** Everything else is a
**snapshot**: it shows the state of the database at the moment you load it. To
track trends in those (e.g. "is Training Pass adoption growing?"), download the
CSV on a regular schedule — the first of each month works well — and keep the
files. The dated filenames make it easy to build a trend spreadsheet later.

## Implementation notes (for developers)

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
- Tests cover the aggregation math, access control, and CSV response — see the
  `Analytics*` test classes in `calculatorapi/tests/test_analytics.py`.
