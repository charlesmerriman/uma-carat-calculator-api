# Django Admin — developer notes

How the admin is built and the rules you must follow when extending it.

This is the **developer** view. For the client-facing guide to *using* the admin as a
content editor, see [content-editing.md](content-editing.md).

---

## Signing in: password, authenticator code, lockout

Since 2026-10-15 the admin login takes a **password and a code from an authenticator
app**, and five wrong passwords pause sign-in for that username from that address for
15 minutes. The code is `calculatorapi/admin_site.py` (the site class, the login form, the
setup and backup-code pages) and the "Admin login" block in `settings.py`; the site is
installed as `admin.site` through `calculatorproject/apps.py`, which is why
`INSTALLED_APPS` carries `unfold.apps.BasicAppConfig` rather than plain `unfold`.

**First login.** A staff account with no authenticator yet types its password and leaves
the code blank. It is NOT signed in; it lands on `/admin/two-factor/setup/`, scans the QR
code with any TOTP app (Google Authenticator, Microsoft Authenticator, Authy, 1Password,
Bitwarden), types the first code, and is signed in. Ten **backup codes** are shown once,
right then; each signs in once in place of the app. Keep them in a password manager.

**Every login after that.** Username, password, and the six digits the app shows, or a
backup code. Six digits is read as the app, anything else as a backup code; there is no
device dropdown. **Two-factor settings** (sidebar, Security; also the header dropdown)
shows the device, how many backup codes are left, and issues a new set, which cancels
the old one.

**Lost phone.** The owner opens the person's user page, deletes the "Authenticator app"
row in the two-factor inline, and the person's next login is a first login again. Their
backup codes keep working until the new set replaces them.

**Lost phone AND backup codes, and you are the only owner.** Set `ADMIN_REQUIRE_OTP=False`
on the API component in the DigitalOcean dashboard (the app restarts), sign in with the
password, delete the lost authenticator on your user page, remove the variable again (the
default is on), and enrol at the next login. The variable is the whole recovery path on
purpose: it takes DigitalOcean access, a higher bar than the admin's own login.

**Locally.** `ADMIN_REQUIRE_OTP=False` in `backend/.env` keeps the local admin
password-only. Leave it unset to walk through the real flow.

**How the gate works.** `CalculatorAdminSite.has_permission` is `AdminSite`'s (staff and
active) and then `request.user.is_verified()`, which `django_otp.middleware.OTPMiddleware`
answers from the device id stored in the session at login. Every admin page and every view
wrapped in `admin.site.admin_view()` (analytics, the image library, the autocomplete) goes
through it, so **a staff-only view mounted any other way skips the second factor**. A
session that is signed in but not verified sees only the login page. Tests get past the
gate through `tests/base.py`'s `VerifiedClient`, whose `force_login` also marks a staff
session verified; the login form itself is tested with a plain `Client` in
`tests/test_admin_security.py`.

**The lockout** is django-axes (`AXES_*` in settings). The pair (username, forwarded
client address) is refused for 15 minutes after 5 wrong passwords, a right password
resets the count, and the PAIR is the unit so nobody can lock the owner out by hammering
the username alone. The address is `visits.client_ip`, the first `X-Forwarded-For` entry,
which a caller can set: a determined attacker rotating it is not stopped by the lockout.
The second factor is the wall; the lockout quiets sprays and keeps a record. Under
**Security** in the sidebar (owner only: the content editors group holds no axes
permission): **Sign-in log**, every successful login with its sign-out time, and
**Locked out**, the live counters, where deleting a row unlocks the pair early. axes's own
log lines go to stdout too, so `doctl apps logs` shows them.

**Early access is not staff.** A tester gets the early-access switch on their user page
(see "Early access grants" below), never `is_staff`: staff-ness is admin login.

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
PvP guide → its sections, one tab per running style), autocomplete pickers, image previews, and
`CustomUser` mounted on Django's `UserAdmin`.

- `GameEvent`'s reward amounts are plain fields on its own edit page (a "Rewards"
  fieldset), **not** an inline — a `GameEvent` carries at most one reward package.
- The three M2M join models are edited only via inlines; they are not registered
  top-level.
- `DailyLegendRaceRelease` edits its umas through `DailyLegendRaceUmaInline`
  (autocomplete). An uma already in another release is refused as a form error by the
  junction's `UniqueConstraint`. The list shows the banner's **confirmed** start plus
  `offset_days` (no prediction maths, same rule as `ScenarioAdmin`), an uma count
  (annotated, one query), and **On the site**, one of three: "Yes, with a date"
  (linked), "Tentative, no date" (no banner, has umas) or "Hidden: no banner or umas"
  (a draft). The same three cases `_build_public_payload` applies.
  The list runs newest first by the banner's **JP** start (a predicted banner has no
  global date, so sorting on that left most rows tied), unlinked releases last. The
  model's `image` is not on the form: nothing on the site draws it.
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

Production runs it on every deploy, in the run command right after `migrate`
(`.do/app.yaml`), so adding a model to `CONTENT_MODELS` needs no console step. Locally
it still has to be run by hand, after `migrate`.

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
  group (with `PVP_GUIDE_POINTER` under it, where the never-shown stat recommendation
  boxes used to be) — because the two models hold the same data and render through the
  same timeline card. Keep them in step when editing either.
- **A column never costs a query per row.** The ones that would are batched:
  `SkillAdmin.versions` (siblings of the page's rows in one lookup, attached in
  `get_changelist_instance`), `AnniversaryEventAdmin.confirmed_start_date` (a `Min`
  annotation), `GalleryReportAdmin.open_on_item` (two filtered `Count`s), and every FK a
  column shows is in `list_select_related`. `AdminQueryCountTests` in `tests/test_admin.py`
  holds the line; add a case there for a new column that reads past the row.
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

**Site content → Pages** holds the About page, the carat income guide and the
intro to the daily legend races tab as markdown (`SitePage`); **Site content → FAQ** holds the categories with their
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

## Gallery moderation

`admin_gallery.py`, under "Users & access": **Gallery reports** (the queue), **Gallery
posts**, **Gallery comments** and **Blocked terms**. Nothing here can be added by hand:
posts, comments, votes and reports come from people through the API.

- **The queue** lists open reports first, each with the item, how many open reports it has
  and whether it is hidden. Three actions work on the ITEMS behind the selected reports and
  close their open reports with the moderator's name: **Keep** (show it again), **Remove**
  (hide it for good; kept in the database, so reversible) and **Block author** (Remove, and
  set `publishing_blocked`). The post and comment pages carry the same three, for something
  spotted without a report. All three go through `gallery_moderation`, the same code the
  auto-hide uses.
- **Auto-hide**: three open reports from different accounts hide an item until someone
  decides. If `MODERATION_WEBHOOK_URL` is set, a Discord channel hears about an item's first
  report and its auto-hide, with a link to the queue (never the text, never the reporter).
- A post's page lists its voters (admin only), for spotting a brigade of new accounts.
- **Blocked terms** are refused in captions, comments and display names. "Whole word" is the
  safe default; "Anywhere" also matches inside longer words. A refusal tells the person to
  check the posting rules, never which word.
- **Users** gain a "Moderation" fieldset: `publishing_blocked` (editable) and the person's
  own `show_supporter_badge` (read-only). Unblocking brings everything they posted back.
- Who moderates: staff, and anyone given the gallery models' permissions.
  `create_content_editor_group` does not grant them: moderation is not content editing.

## Early access grants

**Users** also carry an "Early access" fieldset, directly under Permissions: one switch,
`early_access`. Ticked, the account gets every feature the site still holds in early
access, exactly as a live pledge or staff would (`benefits.early_access_for`), and
nothing else a supporter gets. Use it for testers and friends; **never tick `is_staff`
for that**, which opens this admin's login and skips every supporter limit. The
changelist filters on it and has "Grant early access" / "Revoke early access" bulk
actions; both write a row on each account's History page, so who granted what and when
is always on record. The person cannot set it themselves (`PATCH /account` ignores it),
and `purge_user_pii` leaves it in place.

## Analytics dashboard

`/admin/analytics/` (staff-only, linked from the admin index) shows aggregate usage
stats — paid-product adoption (`daily_carat` / `training_pass`), rank distributions,
resource averages, popular planned banners — plus a `?format=csv` export.

Aggregation logic is in the `calculatorapi/analytics/` package (pure ORM, unit-tested); the view is
wrapped with `admin.site.admin_view()` in `urls.py`. Only aggregates are exposed, never
per-user rows, and staff accounts are excluded from all metrics.

Full metric definitions: [analytics.md](analytics.md).

---

## PvP guides: one page per guide, one tab per running style

`calculatorapi/admin_pvp.py` (its module docstring is the long version). A guide is
edited on one page with a tab strip: **Guide** (event, published, scenario, the overview
text, the stats table, team comps, the general skills) and **Runaway, Front, Pace, Late,
End**, each holding what the site's tab of that name shows, in the site's order: umas,
the advice paragraph, skills, the example decks with their six cards.
One form, one Save. "PvP guides" is the only sidebar entry; `PvpDeck` and every other
child are inline-only (`test_join_models_not_registered_top_level`).

- **One Save posts over 1000 fields.** Every saved row sends its visible fields plus the
  hidden id, parent and style, every formset adds four management fields and a blank
  extra row, and a filled guide with its decks runs to 1500 or more. Django's default
  `DATA_UPLOAD_MAX_NUMBER_FIELDS` of 1000 answered a bare 400 (`TooManyFieldsSent`) before
  the view ran, which is what "the admin gives a 400 on Save" means. `settings.py` raises
  it to 10,000; `test_pvp_guides` posts past the old limit to keep it raised.
- **One inline class per child table per style**, made by `for_style(base, style)`: the
  queryset is filtered to the style, the form is a `FixedStyleForm` (the `style` field
  hidden, its initial AND `self.initial` set to the tab's style, `clean_style()` returning
  it whatever was posted), and the formset prefix is `<style>-<related_name>`
  (`front-decks`, `late-uma_recommendations`), because six formsets of one model would
  otherwise post under one name. The fix is on the form, not in `_construct_form`: Django
  builds the "Add another" template row through `empty_form`, which skips
  `_construct_form`. The hidden `style` goes LAST in `fields`: unfold draws the drag
  handle in the first column's cell, and a hidden first column hides the handle.
- **unfold renders a required choice field with no blank option**, so an untouched blank
  row would post the first choice, count as changed and fail on its empty picker.
  `StatConfigForm.style` and `SkillForm.category` carry a blank first choice for that
  reason; a new child table with a required choice field needs the same.
- **The tabs are unfold's `activeTab`** (declared on `<body>`, kept in the URL hash,
  restored by `getCurrentTab()`, which only honours a hash naming an existing
  `<hash>-group` element). The change form template
  (`templates/admin/calculatorapi/pvpguide/change_form.html`) draws the nav from the
  `pvp_tabs` template tag (`templatetags/pvp_admin.py`: rows and errors per tab, nested
  formsets included), wraps the fieldsets in `activeTab == 'general'` and regroups the
  inlines by `tab_group` into `<div id="<tab>-group">`. The two wrapper templates beside
  it are unfold's `tabular.html` / `stacked.html` with one expression changed. A hidden
  `_pvp_tab` input travels with the POST and `response_change` appends `#<tab>` to the
  "continue editing" redirect, since the browser never sends the hash.
- **Decks nest their cards**: `DeckInline.inlines = (DeckSlotInline,)` on unfold's
  `NestedInlinesModelAdminMixin` (already in its `ModelAdmin`). unfold hands a nested
  inline its PARENT row (the deck, unsaved for the template) as `obj`, which is what makes
  `get_extra` and `DeckSlotFormSet.initial` (the missing slot numbers) per deck. The slot
  formset's `clean()` refuses two Borrow ticks. Deck order is a hidden input the drag
  handle writes (`DeckForm`); `PvpDeck.get_inline_title` keeps the stacked title short.
- **The add page has no tables** (`get_inlines` returns `()` without an object): the
  Event dropdown, scenario, published and the overview, then "Save and continue editing".
- **One Event dropdown** (`PvpGuideForm.event`, grouped CM / LoH, events with a guide
  left out) stands in for the two OneToOnes; `clean_event` sets the FK and refuses a
  taken event, a check the form would otherwise skip because the FKs are not form
  fields. The event pages link here (`PvpGuideLinkMixin`, `PVP_GUIDE_FIELDSET`), and
  `?event=cm-12` on the add URL preselects.
- **Pictures in the pickers**: `PictureAutocompleteJsonView` (routed at
  `admin/autocomplete/` ahead of the admin's own pattern in `urls.py`) adds `picture` to
  every autocomplete result, `PictureAutocompleteSelect` puts `data-picture` on the
  chosen option, and `static/admin/pvp-pickers.js` sets select2's `templateResult` /
  `templateSelection` defaults. **That script must load after select2 and before
  Django's `autocomplete.js` runs at DOM ready**, so the widget's `media` property builds
  ONE list (`[*parent._js, "admin/pvp-pickers.js"]`): Django's Media merge only orders
  files that share a list, and `parent.media + Media(js=...)` had put it before select2.
- **No query per row.** Django's picker fetches the chosen option by pk on every render,
  and a filled guide renders a few hundred. `PicturePickersMixin` loads each inline's rows
  with their picks (`select_related` on its `autocomplete_fields`, plus the parent, which a
  row's `__str__` names) and `KnownRowForm` hands each picker the pick, so a saved row
  renders without a lookup; the same object answers the form field on a save when the
  posted value is the one the row holds (`KnownChoiceField`). The mixin is generic: the six
  autocomplete inlines in `admin.py` (umas on a banner, a card's skills, a campaign's
  parts, a legend-race batch) use it too, which also gives their pickers pictures. "Other
  versions" is one `skill_families` lookup per table, the pre-numbered blank slot rows
  read the decks' prefetched `slots`, and the line under the tabs is set in
  `render_change_form` rather than a second `get_object`. The page costs about 60
  queries whatever it holds, plus one per deck for the nested cards
  (`test_the_page_and_an_unchanged_save_ask_nothing_per_row`). What remains server side
  is unfold's markup, about 2.5 ms a row to render, and a 3.5 MB page for the browser: a
  page with fewer rows is the only thing that cuts that.
- **Pickers on a hidden tab wait for their tab.** Django's `autocomplete.js` initialises
  every picker at DOM ready through `$.fn.djangoAdminSelect2`, a few ms each: a full
  guide's 159 held the page for about half a second after it had drawn (headless Chrome
  on a fast machine; a laptop pays more). `static/admin/pvp-pickers.js` wraps that
  helper (it loads after `autocomplete.js`, so the helper exists, and before DOM ready,
  so the wrap is in place when it is called): a picker on a tab that is not open is held
  back, and the hash naming its tab (the tab links are `#<tab>` anchors) initialises it
  on the next animation frame, once Alpine has shown the tab, about 0.4 s for a tab of
  30. Pickers outside the tab wrappers, and on pages without the tab strip, initialise
  as before. No Django test covers it (it is browser work); a headless Chrome run over
  CDP is how it was measured, see the memory note `admin-performance-2026-10`.
- **A save validates what changed.** Django saves only the rows that changed but validates
  every row, five or six queries each (the id and the pick fetched again, the foreign key,
  the unique constraint, the check constraints); a full guide cost about 1,200 queries to
  save. `ChildRowForm`, the base of every child row form, sets Django's `empty_permitted` on
  a bound saved row, so `full_clean()` returns before any check when `has_changed()` is
  false. A changed row and a new row are validated in full. One consequence: a row that is
  already invalid in the database (a mention of a skill since renamed, say) no longer
  blocks the save until someone touches it.
- **One borrowed slot, not one ticked row.** `DeckSlotFormSet.clean()` counts distinct slot
  numbers: the card in slot 6 and its "or" alternative are the same borrow, and the sheet
  loader ticks both (41 of the loaded decks were refusing every save under the old per-row
  rule). Untouched rows are read off their saved instance, since they have no `cleaned_data`.
- **Skill versions**: the autocomplete JSON and the `skill-names/` suggestions carry
  `version` (`Skill.version_label`: `○ white`, `◎ gold`, `evolved`) and the picker draws it
  as a tag, so an editor picks the version the guide means; the skills tables have a
  read-only "Other versions" column from `Skill.family()`. The family itself is derived
  (`data-model.md`, "`Skill` is imported"); nothing to pick or maintain.
- **The text boxes**: `MentionAwareForm` marks every `MENTION_FIELDS` input with
  `data-pvp-mentions` (`[[` suggestions from the `skill-names/` route, global skills only)
  and every textarea with `data-pvp-markdown` (a Preview toggle rendered by the vendored
  `static/admin/vendor/marked.min.js` 15.0.12, HTML escaped first, `==red==` and
  `[[Skill]]` as marked extensions). `static/admin/pvp-guide-editor.js`.
- **Detail actions**: "Open on the site" (`pvp_guides.site_url`, a draft for staff),
  "Copy from another guide" (an unfold dialog; `pvp_guides.copy_guide` adds the chosen
  sections, skipping rows the unique constraints already hold, filling only blank overview
  fields; the dialog posts with htmx, so the action answers 200 with `HX-Redirect` rather
  than a 302 the dialog would follow inside itself) and "Load from the sheet" (a page,
  `pvp_sheet.load_guide_from_sheet`, preview first; see "Safety & data" in CLAUDE.md for
  why an editor's one-event import is an admin edit and not a seed path). Bulk
  publish / unpublish actions call `public_payload_cache.invalidate()` because
  `queryset.update()` raises no signals and the Timeline's `has_guide` is cached.
- **The list** orders by the event's global start (`get_ordering`, an annotation, so not
  in `ordering` which the checks read), shows the JP date marked "(JP)" when global is not
  confirmed, and "Filled" chips from five `.only("style")` prefetches rather than
  conditional counts across joins.
- `pvp_guides.py` holds the tab list, the sections, `missing_summary` (the line under the
  tabs) and `copy_guide`; nothing in it imports the admin, so the template tag and the
  tests read one definition.

## Calculation constants (Configuration)

One singleton row holding every tunable number the carat projection uses, at
**Configuration → Calculation constants** in the sidebar. Grouped into fieldsets
(daily income, packs & passes, login campaigns & gifts, pull costs & uncap, the
event carat decay curve, global date prediction, PvP milestones); each field's help
text names the source spreadsheet cell it corresponds to. The PvP milestones are hours
after a CM / LoH start date; only an owner account can change them.

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
