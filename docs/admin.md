# Django Admin — developer notes

How the admin is built and the rules you must follow when extending it.

This is the **developer** view. For the client-facing guide to *using* the admin as a
content editor, see [content-editing.md](content-editing.md).

---

## django-unfold theming

The admin is themed with **django-unfold**.

**Every `ModelAdmin` and `TabularInline` in `admin.py` must inherit unfold's base classes**
(from `unfold.admin`), or it renders completely unstyled. This is the single most common
mistake when adding a new admin class.

`unfold` must sit **before** `django.contrib.admin` in `INSTALLED_APPS` — its `ready()`
swaps in `UnfoldAdminSite`, which is what enables the dashboard callback.

All theme configuration lives in the `UNFOLD` settings dict in `settings.py`:

- **Brand palette** — `COLORS["primary"]` is a gold ramp anchored on the frontend's
  `#E6D28A` (`--color-brand`), so admin buttons, links and active-nav match the app.
- **Curated sidebar** — `SIDEBAR["navigation"]` replaces unfold's auto-generated app list
  with task-based groups (Banners, Events & competitions, Characters, Income tables,
  Site content, Users & access), each with Material Symbols icons.
  **A newly registered model must be added here or it will not appear in the sidebar.**
  It stays reachable by URL, which makes this easy to miss.
- **Dashboard + environment badge** — `DASHBOARD_CALLBACK` and `ENVIRONMENT` point at
  `calculatorapi/admin_dashboard.py`. The dashboard callback injects headline KPI cards
  (reusing `analytics.build_analytics_report()`) rendered by
  `templates/admin/custom_index.html`; the environment callback shows a Local/Production
  badge driven by `DEBUG`.

---

## Structure for non-technical editors

`calculatorapi/admin.py` is customized throughout: model and field `verbose_name`s (app
section "Uma Musume Data"), inlines (timeline → banners; banner → featured umas/cards;
Champions Meeting → recommended umas), autocomplete pickers, image previews, and
`CustomUser` mounted on Django's `UserAdmin`.

- `GameEvent`'s reward amounts are plain fields on its own edit page (a "Rewards"
  fieldset), **not** an inline — a `GameEvent` carries at most one reward package.
- The three M2M join models are edited only via inlines; they are not registered
  top-level.
- `BannerStepUp` is registered top-level, not as an inline. It hangs off **two** parents
  (a `BannerTimeline` and an `AnniversaryEvent`) that have to agree, so an inline under
  either one would hide half of what the editor has to get right. `clean()` rejects a
  timeline that is not one of the campaign's own Parts.
- `create_content_editor_group` builds a "Content editors" group with permissions for
  game content and rank tables only. Editor accounts see nothing else. **A newly
  registered content model must be added to its list**, or editors get a sidebar entry
  they cannot open.

```bash
python manage.py create_content_editor_group   # create or refresh the group
```

---

## Image fields: picker as well as upload

Every `ImageField` admin mixes in `SpacesImagePickerMixin`
(`calculatorapi/admin_image_picker.py`), which adds a **"Choose from library"** button
opening a modal grid of what is already in the Spaces bucket.

**Why this works with no extra model:** an `ImageField` stores the **bucket key as a plain
string**, and `S3Boto3Storage` builds the CDN URL on read. So selecting an existing image
is just assigning that string (`setattr(instance, "image", key)`) — no upload, no copy,
no new table.

- Listing logic lives in `calculatorapi/image_library.py` — pure logic: cached
  `default_storage.listdir`, 5-minute TTL, busted on upload.
- It is served to the modal by the staff-only `/admin/image-library/` endpoint, whose
  `?prefix=` is **allow-listed** against the `upload_to` values derived from the models.
- **The mixin takes no configuration.** Image fields and their folders are read off the
  model, so a new `ImageField` is covered automatically and no fieldsets need editing.

Two deliberate behaviours:
- A file upload always beats a simultaneous library pick.
- A bucket that cannot be reached degrades to upload-only rather than 500ing the change form.

---

## Changelist extras

- Banner lists carry a sortable **SQL-annotated "Planned by" column** (count of users
  planning each banner).
- The four rank tables use `list_editable` grids for fast bulk editing.
- `BannerTimeline`, `ChampionsMeeting` and `LeagueOfHeroes` each show a
  **Confirmed/Predicted** global-dates badge (shared `GlobalDatesStatusMixin`) with a
  matching `GlobalDatesFilter`, and split JP/Global date fieldsets.
- `ChampionsMeetingAdmin` and `LeagueOfHeroesAdmin` are deliberately the same page
  section for section — same fieldsets, same schedule-offset block, same **Track details**
  and **Stat recommendations** groups — because the two models hold the same data and
  render through the same timeline card. Keep them in step when editing either.
  (`ChampionsMeetingAdmin` additionally carries the `RecommendedUmaInline`.)
- `PatreonSupporterAdmin` has **"Show name publicly" as a `list_editable` column**, so the
  whole list can be consent-reviewed in one pass. It is the only thing that puts a name on
  the website, and the CSV importer never sets it.
- It also shows **`email`**, in `list_display`, `search_fields` and the first fieldset.
  That is the only surface it appears on: it exists to tell two supporters apart when
  their display names collide or a patron renames themselves, and it is absent from
  `PatreonSupporterSerializer`, so it cannot reach the public `GET /supporters`.
  → `backend/docs/data-model.md` ("`email` — admin-only, and why it is the exception")

---

## Changelog: two authoring routes, one table

`ChangelogEntryAdmin` carries a **Source** column reading either *Written here* or
*Repo file*, off whether `ChangelogEntry.key` is set. It is there because an
editor who changes a *Repo file* entry will see the edit reverted by the next
deploy, and nothing else on the page would explain why. The `key` field sits in
its own "Managed entries" fieldset with help text saying to leave it blank.

The repo-side half is `calculatorapi/data/changelog.yaml` plus
`manage.py sync_changelog` — see `backend/docs/data-model.md`, "The changelog is
authored in the repo, and synced on deploy".

## Site content: Pages and FAQ, and the Rebuild button

**Site content → Pages** holds the About page and the carat income guide as
markdown (`SitePage`); **Site content → FAQ** holds the categories with their
questions as a `StackedInline` (`FaqCategory` / `FaqItem`). Stacked rather than
tabular because an answer is a few paragraphs and a tabular row is too narrow to
write one in. Both are in `CONTENT_MODELS`.

`SitePageAdmin` is change-only: `has_add_permission` and `has_delete_permission`
return `False` for everyone, superusers included, the same posture as
`CalculationConstants`. The rows exist because the frontend routes to them; you
edit their text. → `backend/docs/data-model.md` ("`SitePage`, `FaqCategory`, `FaqItem`")

The Pages changelist carries a **Rebuild website** button, wired like the Patreon
buttons (`get_urls` + an `object-tools-items` override in
`templates/admin/calculatorapi/sitepage/change_list.html`) but as a **POST form**,
because pressing it starts a deployment and a GET should never be able to. The
view is `SitePageAdmin.rebuild_website_view`; the DigitalOcean call is
`calculatorapi/digitalocean_api.py`:

- Gated on `is_staff` only, not on a model permission: a rebuild changes no
  content, it republishes what is already saved. A staff account without view
  permission on Pages lands on the dashboard afterwards instead of the list.
- Reads `DO_API_TOKEN` and `DO_APP_ID` from the environment. Unset, the button
  says it is not configured rather than calling anything.
- Looks at the latest deployment first and refuses while one is
  `PENDING_BUILD` / `BUILDING` / `PENDING_DEPLOY` / `DEPLOYING`, so an eager
  editor cannot queue five rebuilds.
- Then `POST /v2/apps/<id>/deployments {"force_build": true}`, the same call as
  `doctl apps create-deployment --force-rebuild`. Every outcome, including an API
  error, is an admin message, never a 500.

Why a button and not rebuild-on-save: a rebuild takes 5 to 10 minutes and
redeploys the whole app, API included (`migrate` and `sync_changelog` rerun, both
idempotent, and the LocMem cache clears). Editors save many times per session,
and visitors see an edit as soon as it is saved anyway (the site fetches
`/site-content` after it loads); the rebuild is only for what crawlers read.

## Patreon import: two buttons, one reconcile

`PatreonSupporterAdmin` adds **Sync from Patreon** and **Import Patreon CSV** to its
changelist (`get_urls` + a `change_list.html` that overrides `object-tools-items`). Both
views are registered under the model's own URL namespace, so `admin_view()` plus a
`has_add_permission` check gate them exactly like adding a supporter by hand.

They differ only in where the rows come from. **`patreon_api.fetch_members` and
`parse_patreon_csv` emit the same row dicts, and both hand them to
`apply_patreon_import`** — so the reconcile rules below are stated once and hold for the
API sync, the CSV upload, the management command and the scheduled endpoint alike. A
change to what a sync *means* belongs in `admin_patreon_import.py`, never in a caller.

- **Neither ever sets `is_public`.** New supporters are counted anonymously; publishing a
  name stays a deliberate tick on the changelist. This is what makes an unattended
  scheduled sync safe — see `backend/docs/data-model.md`.
- **Preview only** is ticked by default on both. The dry run executes the real reconcile
  inside the atomic block and then `set_rollback(True)`s it, so the preview cannot drift
  from what a live run would do — there is no parallel "what if" code path.
- **Deactivating missing supporters defaults differently, on purpose.** Off for the CSV,
  because a partial or filtered export would deactivate everyone it happens to omit; on
  for the API sync, because a fully-paginated response *is* the complete member list.
- **Matching is on `patreon_user_id` first, casefolded `display_name` as the fallback.**
  API rows carry an id, so a patron can rename themselves without becoming a second row,
  and two patrons who chose the same name stay two people. CSV and hand-entered rows have
  no id and are matched by name; a name match against a row with no id yet *adopts* it,
  which is how existing rows get their id with no data migration (`Patreon id filled` in
  the summary). The one thing the reconcile refuses to guess at is an id-less row whose
  name matches **two** stored rows — nothing is written and the summary says so.
- New tiers are created at the bottom of the order; reorder them on the Patreon Tiers
  page or with `set_patreon_tier_order`.
- `patron_since` is **filled, never overwritten**. Only the API supplies it (the CSV has
  no such column), and a date an editor corrected by hand survives the next sync. The
  Patreon id follows the same rule, for a stronger reason: overwriting one would let a
  name collision move a patron's entitlement onto somebody else's row.
- The summary reports `matched to a website account` when a patron is joined to a site
  login. That is what actually turns a pledge into supporter features on the site.

### The website account column is read-only

`PatreonSupporterAdmin` shows **Website account** on the changelist (a tick when this
patron has linked a site login) and a read-only *Website account* fieldset on the row.
Empty is the normal state — most patrons never make an account here.

Both `linked_user` and `patreon_user_id` are **read-only on purpose**. `linked_user` is
what grants supporter features, and it is set by the person themselves when they sign in
with Patreon or connect it to an existing account; `patreon_user_id` is what the sync
matches on, and hand-editing it would silently reassign one patron's entitlement to
another. `tier` and `is_active` stay editable because those are editorial judgements
about a pledge rather than statements about identity.

**A supporter added from a CSV has no Patreon id and can never be linked.** That is the
strongest practical reason to prefer *Sync from Patreon* over the CSV upload: the CSV
path can thank someone, but it cannot give them the features they paid for.

The commonest support question is "I'm pledging but the site doesn't know" — the answer
is almost always that they have not linked their Patreon login to their account, which
that column answers at a glance.

**Sync from Patreon** additionally shows when the last sync ran and why the last one
failed, and disables its own submit button when no token is configured. The CSV upload
stays as the fallback for exactly that case.

### What the API asks for

`MEMBER_FIELDS` in `calculatorapi/patreon_api.py` names the four fields requested:
`full_name`, `email`, `patron_status`, `pledge_relationship_start` (plus the tier `title`).

**That list is the only thing excluding the rest of the PII — don't assume scopes back it
up.** A creator access token (the kind the developer portal issues, and what this
integration uses) automatically carries *every* v2 scope, including
`campaigns.members[email]` and `campaigns.members.address`. There are no checkboxes to
leave unticked. The token is capable of reading patron postal addresses and phone numbers;
it doesn't because we never ask. `email` is the one contact field we do ask for, and it is
admin-only once stored.

Still better than the CSV path, where the wide export reaches the server and the parser
discards columns — here the data never leaves Patreon. But it rests on one mechanism, not
two, so adding a name to `MEMBER_FIELDS` is the review point.

---

## Analytics dashboard

`/admin/analytics/` (staff-only, linked from the admin index) shows aggregate usage
stats — paid-product adoption (`daily_carat` / `training_pass`), rank distributions,
resource averages, popular planned banners — plus a `?format=csv` export.

Aggregation logic is in `calculatorapi/analytics.py` (pure ORM, unit-tested); the view is
wrapped with `admin.site.admin_view()` in `urls.py`. Only aggregates are exposed, never
per-user rows, and staff accounts are excluded from all metrics.

Full metric definitions: [analytics.md](analytics.md).

---

## Calculation constants (Configuration)

One singleton row holding every tunable number the carat projection uses, at
**Configuration → Calculation constants** in the sidebar. Grouped into fieldsets
(daily income, packs & passes, login campaigns & gifts, pull costs & uncap, the
event carat decay curve, global date prediction); each field's help text names
the source spreadsheet cell it corresponds to.

Its admin is deliberately non-standard, because the usual list → add → edit flow
makes no sense for a single row:

- the changelist **redirects** straight to the row's edit form,
- **add** is refused once the row exists,
- **delete** is refused outright — the projection reads this row on every
  request, and deleting it would have `load()` silently recreate it with
  defaults, throwing away a calibration.

**This is not a content-editor page.** `CalculationConstants` is left out of
`CONTENT_MODELS` (see `management/commands/create_content_editor_group.py`), so
the group gets no permission on it and unfold hides the whole Configuration
section for them. That is intentional: these numbers change every user's
projection, and `prediction_factor` / `game_event_end_buffer_days` change banner
*dates* too.

Model validators catch the obviously wrong (negatives, an out-of-range
prediction factor). Nothing catches a plausible-but-wrong figure, so treat the
page as production data.

## Local gotcha

The local Django server runs with `DEBUG=False` and `collectstatic` has never been run
locally, so **every admin template page 500s on :8000** (whitenoise manifest storage,
"Missing staticfiles manifest entry"). API endpoints are unaffected.

To render admin pages locally, run a second server with the manifest bypassed:

```bash
DEBUG=True python manage.py runserver 8001 --noreload
```

Django tests that render admin templates need an `@override_settings(STORAGES=...)` swap
to plain `StaticFilesStorage` — see `PLAIN_TEST_STORAGES` in `calculatorapi/tests/base.py`.
