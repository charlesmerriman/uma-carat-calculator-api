from django.db import models


class DailyVisit(models.Model):
    """One row per calendar day: how much traffic the site saw.

    A permanent record holding nothing but counters -- there is no per-visitor
    row here and no way to work backwards from it to a person.

    Written by calculatorapi/visits.py in response to the SPA's beacon (see
    views/visits.py). Deliberately NOT registered in the Django admin: the
    numbers are reporting output, and a hand-edited counter is worse than no
    counter.
    """

    # UTC calendar day, matching timezone.localdate() at the moment of the hit.
    # Unique because the whole design is "find today's row and increment it";
    # a duplicate would silently split a day's traffic across two rows.
    date = models.DateField(unique=True)
    # Total beacons received. One per browser session, not per route change --
    # the SPA fires once and remembers (see frontend visitBeacon.ts), so this
    # counts sessions rather than client-side navigations.
    page_views = models.PositiveIntegerField(default=0)
    # Distinct visitors seen on this date, counted via VisitorHash below.
    # Rolled up here so the figure survives the pruning of the hashes it came
    # from -- the hashes are scratch, this number is history.
    unique_visitors = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Daily Visit"
        verbose_name_plural = "Daily Visits"
        # Newest first: every consumer wants the recent window.
        ordering = ["-date"]

    def __str__(self):
        return f"{self.date}: {self.page_views} views, {self.unique_visitors} visitors"


class MonthlyVisit(models.Model):
    """One row per calendar month. The same shape as DailyVisit, and separate
    from it on purpose.

    `unique_visitors` here is a TRUE monthly figure: someone who visits on
    fifteen days counts once, which is what makes it a monthly-active number
    rather than a sum of daily counts. That cannot be derived from DailyVisit
    after the fact -- summing daily uniques would count that person fifteen
    times -- so it is accumulated as visits arrive, against the same
    month-scoped hash that VisitorHash stores.

    Permanent, like DailyVisit, and likewise kept out of the admin.
    """

    # First day of the month, standing in for the month itself. A DateField
    # rather than a "2026-08" string so ordering and range filters are the
    # database's job rather than string comparison's.
    month = models.DateField(unique=True)
    page_views = models.PositiveIntegerField(default=0)
    unique_visitors = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Monthly Visit"
        verbose_name_plural = "Monthly Visits"
        ordering = ["-month"]

    def __str__(self):
        return f"{self.month:%Y-%m}: {self.page_views} views, {self.unique_visitors} visitors"


class VisitorHash(models.Model):
    """Deduplication scratch space -- "have we already counted this visitor?"

    Holds a salted digest, never an IP address or any raw request field. The
    salt includes the calendar MONTH (see visits._visitor_hash), which is the
    span over which a visitor can be recognised: within one month the same
    person produces the same value, so they can be counted once for the month;
    at the month boundary the value changes completely and the link is gone.

    Both counters are maintained from these rows: `date` scopes the daily
    figure, the month embedded in the hash scopes the monthly one.

    Disposable. Once a month is over, its rows can only re-confirm counts that
    are already rolled up into DailyVisit and MonthlyVisit --
    `manage.py prune_visitor_hashes` drops them. The retention window must stay
    comfortably longer than a month, or a visitor whose earlier rows were pruned
    mid-month would be counted twice.
    """

    date = models.DateField(db_index=True)
    # 32 hex chars: half a SHA-256, which is far more collision headroom than a
    # monthly visitor count needs, and keeps the indexes narrow.
    visitor_hash = models.CharField(max_length=32, db_index=True)

    class Meta:
        verbose_name = "Visitor Hash"
        verbose_name_plural = "Visitor Hashes"
        constraints = [
            # This constraint IS the daily deduplication: record_visit() relies
            # on get_or_create returning created=False here rather than doing
            # its own "have I seen this?" query, so concurrent requests from the
            # same visitor can never both count as new.
            models.UniqueConstraint(
                fields=["date", "visitor_hash"],
                name="unique_visitor_hash_per_day",
            )
        ]
        indexes = [
            # The monthly check is "any row for this hash since the 1st?", which
            # filters on hash and date together.
            models.Index(fields=["visitor_hash", "date"], name="visitor_hash_date_idx"),
        ]

    def __str__(self):
        # Truncated: the full digest is not a secret, but there is no reason to
        # print it into admin logs or error output either.
        return f"{self.date}: {self.visitor_hash[:8]}..."


class LandingPageVisit(models.Model):
    """One row per (day, page): how many visits STARTED on that page.

    The SPA's beacon fires once per session from wherever the visitor arrived,
    so the path it reports is the landing page. Only the public routes in
    visits.KNOWN_ROUTES are kept by name; every other path (a typo, a probe, an
    old link) counts as "other", so this table holds a short, fixed list of
    pages and nothing a visitor typed.

    Totals only, like DailyVisit: no visitor hash is involved, so a row cannot
    be tied to anyone even inside the database, and the rows are kept. Not in
    the admin, for the same reason as DailyVisit.
    """

    date = models.DateField()
    route = models.CharField(max_length=40)
    page_views = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Landing Page Visit"
        verbose_name_plural = "Landing Page Visits"
        ordering = ["-date", "route"]
        constraints = [
            # The same "find today's row and increment it" design as DailyVisit.
            models.UniqueConstraint(fields=["date", "route"],
                                    name="one_landing_row_per_day_and_route"),
        ]

    def __str__(self):
        return f"{self.date} {self.route}: {self.page_views}"


class ReferrerVisit(models.Model):
    """One row per (day, site): how many visits a site sent us.

    `host` is only the NAME of the linking site (google.com), never the rest
    of the link; "direct" means the browser reported no referrer (a bookmark, a
    typed address, and most chat apps), "other" anything that did not look like
    a hostname or arrived after the day's cap (visits.MAX_REFERRER_HOSTS_PER_DAY).

    Totals only, kept, not in the admin: see LandingPageVisit.
    """

    date = models.DateField()
    host = models.CharField(max_length=100)
    page_views = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Referrer Visit"
        verbose_name_plural = "Referrer Visits"
        ordering = ["-date", "host"]
        constraints = [
            models.UniqueConstraint(fields=["date", "host"],
                                    name="one_referrer_row_per_day_and_host"),
        ]

    def __str__(self):
        return f"{self.date} {self.host}: {self.page_views}"
