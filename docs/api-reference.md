# API Reference

All endpoints are relative to the API base URL (e.g. `http://localhost:8000` in development).

A generated OpenAPI schema is served at `/schema`, with Swagger UI over it at `/docs`
([live](https://umacaratcalculator.com/api/docs)). This file stays the fuller reference: the
generator can't infer the bodies of the function-based views, which build their requests and
responses by hand.

Token authentication is required for all protected endpoints. Include the token in every request header:

```
Authorization: Token <token>
```

Read-only reference endpoints (rank tables, events, League of Heroes) and `GET /calculator-data` are public. Note that a request carrying an *invalid* token still returns `401` even on public endpoints — DRF authenticates the token before checking permissions. Clients should drop a rejected token and retry without one.

Not part of the public API: `/admin/analytics/` is a staff-only aggregate analytics page served inside the Django admin (session auth, not token auth) — see [analytics.md](analytics.md). `/admin/image-library/` is likewise staff-only — it lists the media bucket as JSON for the admin's image picker (`?prefix=umas/`, `?refresh=1`), and only accepts folders that back an actual `ImageField`.

---

## Authentication

Ordinary accounts are created and authenticated **only** through Google, Discord or Patreon (OAuth2 authorization code flow). There is no registration endpoint, and `POST /login` is restricted to staff. A social account stores nothing but the provider's opaque subject id and a generated `user_xxxxxx` handle — no email, name, or password. See `calculatorapi/oauth.py` and `calculatorapi/views/social_auth.py`.

### `GET /auth/<provider>/start`

Public. `<provider>` is `google` or `discord`. Returns the provider consent URL to redirect the browser to, plus the signed `state` the caller must echo back.

**Query parameters**

| Name | Required | Meaning |
|---|---|---|
| `redirect_uri` | no | Where the provider should return the browser. Omitted → `settings.OAUTH_REDIRECT_URI`. Supplied → must appear verbatim in `settings.OAUTH_ALLOWED_REDIRECT_URIS`, else `400`. The value is sealed into the signed `state` and reused for the token exchange. → `auth-and-privacy.md` |

**Response `200`**
```json
{ "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth?...", "state": "string" }
```

**Response `400`** — `redirect_uri` was supplied but is not allowlisted for this deployment. The rejected value is not echoed back.
**Response `404`** — unknown provider.
**Response `503`** — the provider's client id/secret is not configured on the server.

The `state` is signed with `django.core.signing` (salt `calculatorapi.social-auth-state`) and carries the provider name plus a nonce. It expires after `OAUTH_STATE_MAX_AGE_SECONDS` (default 600). The client should also keep its own copy and compare on return — that browser binding is what defeats login CSRF.

---

### `POST /auth/social`

Public. Redeems the one-time authorization code and returns an API token. The code is exchanged server-to-server; the client secret never leaves the backend.

**Request body**
```json
{ "provider": "google", "code": "string", "state": "string" }
```

**Response `201`** — first sign-in for this provider account (a new user was created).
**Response `200`** — returning user.
```json
{ "token": "string" }
```

**Response `400`** — one generic body for every failure (bad/expired/mismatched state, missing code, expired or replayed code, provider error). The specific reason is deliberately not disclosed.
```json
{ "error": "Could not complete sign in. Please try again." }
```

**Response `404`** — unknown provider.

The redirect URI is derived server-side as `<FRONTEND_URL>/auth/callback` and must match the value registered in the provider console exactly.

---

### `POST /login`

Public route, **staff only**. Authenticates a staff user by password and returns their auth token. This exists so admins can reach `/admin` and the analytics dashboard; ordinary accounts have unusable passwords and must use the social endpoints above.

**Request body**
```json
{ "username": "string", "password": "string" }
```

**Response `200`**
```json
{ "token": "string" }
```

**Response `400`** — wrong credentials **or** a correct password on a non-staff account. Both return an identical body, so the endpoint cannot be used to discover which usernames exist.
```json
{ "error": "Invalid Credentials" }
```

---

### `POST /logout`

Protected. Deletes the user's current auth token.

**Response `200`**
```json
{ "message": "Successfully logged out" }
```

### `GET /account`

Protected. Who the caller is, and what they are entitled to. `401` for anonymous
callers — which is what lets the SPA treat it as the source of truth: a revoked
token gets a 401 here, so the client learns the string it holds has stopped
meaning anything instead of rendering a signed-in shell around nothing.

**Response `200`**
```json
{
  "username": "user_a3f9c1",
  "avatar_url": "https://lh3.googleusercontent.com/a/ACg8ocJ…=s96-c",
  "linked_providers": [
    { "provider": "google", "linked_at": "2026-07-02",
      "avatar_url": "https://lh3.googleusercontent.com/a/ACg8ocJ…=s96-c" },
    { "provider": "patreon", "linked_at": "2026-09-08", "avatar_url": "" }
  ],
  "supporter": { "is_supporter": true, "tier": "Junior Class", "benefits": ["ad_free"] }
}
```

- `linked_providers` is empty for staff, who sign in with a password and hold no
  `SocialAccount` rows. That is a correct answer, not an error.
- **`avatar_url`** (top level) is the picture to show in the navbar: the one from
  the provider the person most recently **signed in** with (a link-only provider
  counts by its link date), skipping providers with no picture. **`null`** when no
  linked provider has one — null rather than `""`, so a client draws its fallback
  instead of loading an empty `src`. Per provider, `avatar_url` is `""` when that
  provider has no picture. Refreshed on every sign-in or link through that
  provider; it is the one profile attribute the account holds, and it is only
  ever served here, to its owner. → [auth-and-privacy.md](auth-and-privacy.md)
- **`subject_id` is never serialized, for any provider.** The serializer's
  explicit field list is the only thing keeping it off the wire — the same role
  `PatreonSupporterSerializer`'s list plays for the supporter email.
- `supporter` is **derived on every request** from the linked `PatreonSupporter`
  row — `linked_user` set, `is_active`, and a tier — never read from a flag on
  the account. → `calculatorapi/benefits.py`
- With no entitlement the block is `{"is_supporter": false}` and **nothing
  else**. No null tier, no empty benefits array: either would let a client read
  the absence of a tier as a tier, or "we checked and they have none" as "we
  have not checked".
- `benefits` is a list of capability KEYS, not tier arithmetic. Whether
  `ad_free` needs any paid tier or a specific one is decided server-side in
  `benefits.BENEFITS`; a client comparing tier orders would be a second
  implementation of the paywall, free to disagree with the real one. There is
  deliberately no tier `order` in the response.
- Nothing identifying the *supporter row* is here even for a supporter — no
  display name, no admin email, no `patreon_user_id`, no row id.

Deliberately its own route rather than a key on `/calculator-data`: that payload
is not fetched on the home page, the FAQ or the changelog, and everything in it
but the four user-scoped keys is served from a shared process-wide cache, which
entitlement must never be answerable from.

---

### `DELETE /account`

Protected. Removes the caller's account. **`204`**, no body.

- **Gone, by cascade:** the account row, its API token (the request's own token
  stops working immediately), its `SocialAccount` rows (avatar URLs included)
  and every planned banner, purchase and step-up selection. Signing in again
  with the same provider creates a fresh, empty account.
- **Kept, with the pointer to the account cleared:** feedback the person sent
  (`user` → null) and their `PatreonSupporter` row (`linked_user` → null — the
  same treatment as an unlink, a lapse or a purge; that table is not ours to
  delete from).
- `403` for staff — their accounts are managed in the admin. `401` anonymous.
- No confirmation body. The account page makes the person type a phrase first;
  the request carries the token of the very account it deletes, and there is no
  recovery afterwards (no email is held to send a reset to, by design).

---

### `GET /account/link/<provider>/start`

Protected, throttled to **20/hour per user**. Returns a consent URL for attaching
`provider` to the account already signed in.

**Response `200`** — same shape as `/auth/<provider>/start`:
```json
{ "authorize_url": "https://www.patreon.com/oauth2/authorize?...", "state": "..." }
```

`404` unknown provider · `400` unlisted `redirect_uri` · `401` anonymous ·
`503` provider credentials not configured.

The `state` is signed with salt `calculatorapi.account-link-state` — **not** the
sign-in salt — and carries the id of the user it was minted for.

### `POST /account/link/<provider>/complete`

Protected. Redeems the one-time code and attaches the identity.

**Request** `{ "code": "...", "state": "..." }`

**Response `201`** (linked) or **`200`** (already linked — completing twice is not
an error, and it refreshes the stored avatar):
```json
{ "provider": "patreon", "linked_at": "2026-09-08",
  "avatar_url": "https://c10.patreonusercontent.com/…/thumb.png" }
```

- `409` — that identity belongs to a different account, **or** this account
  already has a login for that provider. Never reassigns; see
  [auth-and-privacy.md](auth-and-privacy.md).
- `400` — bad or expired state, a state minted for another user, a **sign-in**
  state, or a failed exchange. One generic message for all of them.
- **This endpoint never creates a `CustomUser`.** That invariant is what separates
  it from `POST /auth/social`.

### `DELETE /account/link/<provider>`

Protected. Detaches the provider. **`204`** on success.

- `404` — not linked to this account (including when it is linked to someone else).
- `400` — it is the account's **last** sign-in method and the account has no usable
  password. An ordinary account has neither a password nor an email to reset
  through, so this would be an unrecoverable lockout. Staff are exempt.

---

## Core Calculator

### `GET /calculator-data`

Public. Returns a single aggregated payload containing all reference data and user-specific state. The frontend calls this once on mount.

For anonymous requests, all reference keys are populated as usual but the user-scoped keys are empty: `user_stats_data` is `null`, and `user_planned_banner_data` / `user_planned_purchase_data` / `user_step_up_selection_data` are `[]`. The frontend uses the `null` stats to detect guest mode and seed local defaults.

**Everything except the four user-scoped keys is cached server-side.** The reference half is
built once, kept as rendered JSON, and reused until a content write drops it — so a cache hit
runs no catalogue queries for either a guest or a signed-in user. The user-scoped keys are
always read fresh per request and never enter the cache. Invalidation hangs off
`post_save`/`post_delete`/`m2m_changed`, so it does **not** catch `bulk_create()`,
`bulk_update()`, `queryset.update()` or raw SQL; a writer using those should call
`public_payload_cache.invalidate()` itself. Full reasoning, and the single-process deployment
assumption it rests on, in `calculatorapi/public_payload_cache.py`.

**Response `200`**
```json
{
  "club_rank_data":              [ ClubRank ],
  "team_trials_rank_data":       [ TeamTrialsRank ],
  "champions_meeting_rank_data": [ ChampionsMeetingRank ],
  "league_of_heroes_rank_data":  [ LeagueOfHeroesRank ],
  "banner_uma_data":             [ BannerUma ],
  "banner_support_data":         [ BannerSupport ],
  "banner_step_up_data":         [ BannerStepUp ],
  "user_planned_banner_data":    [ UserPlannedBanner ],
  "champions_meeting_data":      [ ChampionsMeeting ],
  "league_of_heroes_event_data": [ LeagueOfHeroes ],
  "events_data":                 [ GameEvent ],
  "user_stats_data":             UserStats,
  "banner_timeline_data":        [ BannerTimeline ],
  "anniversary_event_data":      [ AnniversaryEvent ],
  "scenario_data":               [ Scenario ],
  "user_planned_purchase_data":  [ UserPlannedPurchase ],
  "user_step_up_selection_data": [ UserStepUpSelection ],
  "income_ledger":               [ IncomeLedgerRow ],
  "calculation_constants":       CalculationConstants
}
```

`user_planned_banner_data`, `banner_uma_data`, `banner_support_data`, `champions_meeting_data`, `league_of_heroes_event_data`, `events_data`, `anniversary_event_data`, `scenario_data` and `user_planned_purchase_data` are all ordered by each row's **resolved** (confirmed-or-predicted) global start date, sorted server-side in Python since predicted dates aren't a DB column.

---

### `PATCH /calculator-data`

Protected. Upserts the user's planned banners, planned purchases and step-up card
selections, and updates their stats, in one request.

**Upsert semantics** — identical for `user_planned_banner_data`, `user_planned_purchase_data`
and `user_step_up_selection_data`:
- Key absent from the body → that collection is left completely alone
- Key present as `[]` → every row in that collection is deleted
- Row with `id` → update that row (`404` if the id isn't this user's)
- Row without `id` → create new row
- Any row in the database not present in the payload → deleted

> **`user_step_up_selection_data` must be sent WITHOUT row ids.** It is the only
> collection carrying a UNIQUE constraint, and the id-carrying form updates rows one at
> a time — so moving a card from slot 3 to slot 4 transiently duplicates slot 4 and trips
> `unique_step_up_selection_slot`. Id-less rows make the reconcile a
> delete-all-then-create, which cannot collide. A selection's content is its identity, so
> there is nothing to preserve across the replace.

The whole request is one transaction: if any section fails validation, **nothing** is
written, including sections already applied earlier in the same request. This relies on
`transaction.set_rollback(True)` — returning a `Response` from inside an atomic block
exits it normally, so Django would otherwise commit the accepted half.

**Request body** (all keys are optional)
```json
{
  "user_stats_data": {
    "current_carat":          0,
    "current_paid_carat":     0,
    "uma_ticket":             0,
    "support_ticket":         0,
    "uma_selector_ticket":    0,
    "support_selector_ticket": 0,
    "daily_carat":            false,
    "training_pass":          false,
    "misc_earnings":          true,
    "monthly_shop_tickets":   true,
    "discounted_paid_pulls":  true,
    "full_price_paid_pulls":  true,
    "include_purchases_in_projection": false,
    "webstore_bonus":         false,
    "sr_shards":              0,
    "sr_crystals":            0,
    "ssr_shards":             0,
    "ssr_crystals":           0,
    "club_rank":              1,
    "team_trials_rank":       1,
    "champions_meeting_rank": 1,
    "league_of_heroes_rank":  1
  },
  "user_planned_banner_data": [
    { "id": 5, "number_of_pulls": 20, "reserved_copies": 0, "banner_uma": 3, "banner_support": null },
    { "number_of_pulls": 10, "reserved_copies": 2, "banner_uma": null, "banner_support": 7 }
  ],
  "user_planned_purchase_data": [
    { "id": 2, "product": 14, "quantity": 3, "target_uma": null, "target_support": null },
    { "product": 17, "quantity": 1, "target_uma": 88, "target_support": null }
  ]
}
```

Rank fields accept the integer primary key of the corresponding rank row. Exactly one of `banner_uma` / `banner_support` must be non-null per planned banner row (enforced by both the serializer and a DB check constraint).

`reserved_copies` is how many copies the user plans to take with a selector ticket or an
SSR crystal rather than by pulling. Only the count is stored — which resource pays is
derived client-side per render from the projected balances and JP eligibility.

For a planned purchase, **at most one** of `target_uma` / `target_support` may be set, it
must match the product's type, and a carat pack may have neither. A selector target is
additionally rejected (`400`) when the card was released on JP after the product's
effective cutoff — see `calculatorapi/eligibility.py`.

**Response `200`**
```json
{ "message": "Data updated successfully" }
```

---

## Reference Data (read-only)

These endpoints return static rank tables. All are public and support `list` and `retrieve`.

| Endpoint | Resource |
|---|---|
| `GET /clubranks` | Club rank tiers and monthly income amounts |
| `GET /teamtrialranks` | Team Trials rank tiers and weekly income amounts |
| `GET /championsmeetingranks` | Champions Meeting placement tiers and per-event income amounts |
| `GET /leagueofheroesranks` | League of Heroes rank tiers and income amounts |
| `GET /events` | Game events, including their own reward amounts |
| `GET /changelog` | Patch-note entries (newest first) with nested, ordered change lines |
| `GET /supporters` | Patreon thank-you list — **not** an array, see below |

All list responses return an array of the resource object, **except `/supporters`** (an object — the anonymous count is not derivable from the rows). Retrieve by appending `/<id>`; `/supporters` has no retrieve action.

---

## Telemetry

### `POST /visit`

Public. Records one site visit for the admin analytics dashboard. Takes no
request body — anything sent is ignored — and returns `204 No Content` with an
empty body.

The frontend is a separate static site on the CDN, so Django never sees a page
load; this beacon is the only way it learns of one. `frontend/src/services/visitBeacon.ts`
fires it **once per browser session**, not per route change, and suppresses it
entirely when a dev server is pointed at a remote API (`npm run dev:live`).

The response is deliberately uninformative. A request filtered as a bot and a
request that was counted both return `204`, so the filter cannot be probed by
trial and error.

Throttled at 60/hour per address (`visit_beacon` scope) — far above one beacon
per session, with headroom for several people behind one NAT. Over the limit
returns `429`.

No IP address is stored. See `backend/docs/analytics.md` for what is recorded,
and for why the monthly unique-visitor count is deliberately smaller than the
sum of the daily ones.

---

### `POST /feedback`

Public. Stores one message from the site's feedback form and returns
`201 Created` with an empty body. The site's other public write endpoint, and it
follows `POST /visit` above in both respects that matter: it is rate limited, and
its response says nothing about how the submission was handled.

Request body:

```json
{
  "category": "bug",              // bug | feature | data | other
  "message": "…",                 // required, non-blank, max 4000 chars
  "source_path": "/app/timeline", // optional, the route the sender was on
  "website": ""                   // honeypot — see below
}
```

`user`, `submitted_at` and `is_resolved` are **not** writable. A client cannot
attribute its message to someone else's account, backdate it, or file it
pre-resolved; the view sets the user from the request itself.

Auth is optional. A signed-in caller's submission is linked to their account so
repeat reporters are visible in the admin; a guest's is stored with `user` null.
Guests are the common case — the whole site works signed out.

`website` is a honeypot. The form renders it hidden, tab-skipped and
autocomplete-off, so a person never fills it and a naive bot fills everything. A
non-empty value makes the endpoint discard the submission **and still return
`201`** — answering "spam detected" would tell whoever is probing which field to
leave alone next time. Same reasoning as the beacon's bot filter.

Throttled at 10/hour per address (`feedback` scope); over the limit returns
`429`. Much lower than the beacon because a real person submits once, and each
request here writes a row. A blank or over-long `message` is a `400` with a
field error.

No IP address and no contact details are stored — the form has no reply-address
field at all. See the "Feedback you send us" section of the privacy policy.

---

### `POST /patreon/sync`

The scheduled trigger for the Patreon supporters sync, called once a day by
`.github/workflows/patreon-sync.yml`. Takes no body.

**Authorised by a shared secret, not by a user.** The caller is a GitHub Action,
which has no account here and should not be given one — a staff token would hand
a CI secret the run of the admin API. So it presents `X-Patreon-Sync-Key`, which
authorises this one action and nothing else, compared with `hmac.compare_digest`
so a wrong key cannot be recovered a character at a time by timing.

| Condition | Response |
|---|---|
| `PATREON_SYNC_SECRET` unset | `404` — the route does not exist |
| Header missing or wrong | `403`, generic body |
| Patreon unreachable / token dead | `502`, message recorded on `PatreonCredentials` |
| Success | `200` with counts |

The `404` is deliberate: there is no state in which the endpoint exists but
accepts anything. The `502` is deliberate too — the scheduled job fails on
non-200, so a dead token surfaces as a red X rather than a list going quietly
stale.

```json
{
  "members_returned": 22, "created": 1, "reactivated": 0,
  "tier_changed": 0, "deactivated": 2, "dates_filled": 1,
  "emails_updated": 0, "ids_filled": 0, "linked": 1, "ambiguous": 0,
  "unchanged": 19
}
```

`linked` counts patrons newly matched to a website account. `ambiguous` counts
rows the reconcile **refused to act on** because two stored supporters share
that display name and the incoming row carried no Patreon id to tell them
apart — nothing was written for those, and they need an editor.

**Counts, never names** — the job log is a third-party surface, and most
supporters have not been cleared for publication. Throttled at 12/hour
(`patreon_sync` scope).

This endpoint **cannot publish a name**: it takes no caller-supplied content and
runs the same `apply_patreon_import` as every other path, which never writes
`is_public`. The worst a stolen key achieves is an early refresh and some spent
Patreon quota.

---

## Shape Reference

### `UserStats`
```json
{
  "current_carat":          0,
  "current_paid_carat":     0,
  "uma_ticket":             0,
  "support_ticket":         0,
  "uma_selector_ticket":    0,
  "support_selector_ticket": 0,
  "daily_carat":            false,
  "training_pass":          false,
  "misc_earnings":          true,
  "monthly_shop_tickets":   true,
  "discounted_paid_pulls":  true,
  "full_price_paid_pulls":  true,
  "include_purchases_in_projection": false,
  "webstore_bonus":         false,
  "sr_shards":              0,
  "sr_crystals":            0,
  "ssr_shards":             0,
  "ssr_crystals":           0,
  "club_rank":              1,
  "team_trials_rank":       1,
  "champions_meeting_rank": 1,
  "league_of_heroes_rank":  1
}
```

Rank fields are returned as integer IDs (primary keys).

`uma_selector_ticket` / `support_selector_ticket` are **not** gacha tickets. A gacha
ticket is worth one pull and is spent by the pull strategy; a selector takes a specific
card outright and never funds a pull. They are the user's current holdings and are
treated as unrestricted (no JP cutoff); tickets projected from campaigns carry their
campaign's cutoff instead.

### `UserPlannedBanner` (response)

On GET, `banner_uma` and `banner_support` are expanded to nested objects (not IDs). On PATCH request bodies they must be integer IDs.

```json
{
  "id": 1,
  "user": 1,
  "number_of_pulls": 20,
  "reserved_copies": 0,
  "banner_uma": { ... BannerUma object ... },
  "banner_support": null
}
```

### `AnniversaryEvent` (from `anniversary_event_data`)

A dated campaign that sells discounted carat packs and grants selector tickets. Public.

It owns **no dates**: `start_date` / `end_date` are resolved by spanning the
`BannerTimeline` "Parts" it links to (earliest start, latest end), so it follows exactly
the same confirmed-or-predicted rules as everything else on the calendar. Both are
`null` when the campaign has no linked parts with resolved dates, and `is_predicted` is
true if **any** contributing part is predicted.

`main_start_date` is a **third** date, and the one most consumers want: when the event the
campaign is named after actually begins. An anniversary opens with a Part 1 run-up of
login rewards and only reaches the anniversary itself at Part 2, ~10 days later — so
`start_date` places the campaign's opening while `main_start_date` places the anniversary.
It is null exactly when `start_date` is, always falls inside the window, and equals
`start_date` for `new_year` and `campaign` rows, which have no run-up. Use it to place a
campaign on a calendar or to date a purchase; use `start_date`/`end_date` to describe the
campaign's span. Full rule: `backend/docs/data-model.md`.

```json
{
  "id": 8,
  "name": "3rd Anniversary",
  "event_type": "anniversary",
  "jp_cutoff_date": "2024-01-31",
  "image": null,
  "accent_label": "",
  "start_date": "2027-07-17T22:00:00Z",
  "main_start_date": "2027-07-27T22:00:00Z",
  "end_date": "2027-08-19T21:59:59Z",
  "is_predicted": true,
  "applied_offset_days": 0,
  "products": [ AnniversaryEventProduct ],
  "banner_parts": [ { "banner_timeline": 142, "part_number": 1 } ]
}
```

`event_type` is one of `anniversary` / `new_year` / `campaign` — the source sheet plans
New Years campaigns and one-off promotions alongside anniversaries, and this keeps them
in one table without the name lying about what a row holds.

> `AnniversaryEvent.event_type` is the **campaign kind**. It is not the timeline
> discriminated-union tag of the same name emitted by `EventTypeMixin` on
> `banner_timeline_data` / `champions_meeting_data` / `league_of_heroes_event_data`.
> Don't merge the two.

### `Scenario` (from `scenario_data`)

A training scenario — a new, optional way to play the game (URA Finals, Aoharu, Grand
Live, Hashire! Mecha Umamusume). Public. Grants no resources: it is purely a marker on
the timeline and in the calculator's section bands.

**There is no `end_date` key in this payload, and that is deliberate.** A scenario is
released and then stays available permanently — a newer scenario does not retire an older
one, it just tends to get played more because it is more rewarding. So there is no end to
report. It is omitted rather than emitted as a permanent `null`, because a
structurally-always-null field invites a consumer to render a range that doesn't exist.
This is the only collection on `/calculator-data` with a start and no end.

`start_date` is borrowed from the scenario's launch banner (`banner_timeline`) and follows
the same confirmed-or-predicted rules as everything else on the calendar; `is_predicted`
and `applied_offset_days` propagate from that banner. `start_date` is `null` when the
scenario has no linked banner, or that banner has no resolved start.

`image` is frequently `null`: scenarios get entered before their art exists, and every
consumer is expected to render without it.

```json
{
  "id": 3,
  "name": "Hashire! Mecha Umamusume",
  "image": null,
  "banner_timeline": 142,
  "start_date": "2028-02-08T22:00:00Z",
  "is_predicted": true,
  "applied_offset_days": 0
}
```

`banner_timeline` is a bare id, not a nested object — the frontend already holds every
banner in `banner_timeline_data`, and it needs the id to pin the scenario's band directly
above that banner's row in the planner.

### `AnniversaryEventProduct`

One purchasable line on a campaign. Packs and selectors share one shape, tagged by
`product_type` (`carat_pack` / `uma_selector` / `support_selector`) — narrow on the tag,
never on which fields happen to be set.

```json
{
  "id": 14,
  "product_type": "carat_pack",
  "name": "7500 Carat Pack",
  "usd_cost": 70.0,
  "paid_carat_amount": 7500,
  "webstore_multiplier": 1.1,
  "max_quantity": 10,
  "jp_cutoff_date": "2024-01-31",
  "jp_cutoff_date_override": null,
  "order": 1
}
```

`usd_cost` and `webstore_multiplier` are JSON **numbers**, not DRF's default
Decimal-as-string, so the client can do arithmetic on them directly.

`jp_cutoff_date` is already resolved against the campaign's — the client never has to
reimplement the fallback. `jp_cutoff_date_override` exposes the product's own value and
is `null` when the cutoff came from the campaign.

### `UserPlannedPurchase` (from `user_planned_purchase_data`)

`product` stays an integer id on both read and write — unlike planned banners, nothing is
nested, because the client already holds the whole campaign catalogue and joins on it.

```json
{
  "id": 2,
  "user": 1,
  "product": 14,
  "quantity": 3,
  "target_uma": null,
  "target_support": null
}
```

### `UserStepUpSelection` (from `user_step_up_selection_data`)

One of the ten cards the user intends to select at a Select Step-Up banner. Keyed to the
**banner**, not to a planned row — "which ten would I pick here" is a fact about the banner,
so it needs no plan to exist and survives one being deleted.

`is_target` marks the step 5 pick, the copy the player chooses outright rather than being
handed at random. At most one per (user, banner).

Exactly one of `uma` / `support` is set, and it must match the step-up's `card_type`. An
empty slot is an **absent row**, never a row with no card.

**Nothing in the projection reads this.** The step-up target rate is 3% ÷ 10 and holds
whichever ten are chosen, so a partial or empty selection changes no number — matching the
source sheet, whose Selection 1–10 columns feed no formula either.

```json
{
  "id": 7,
  "user": 1,
  "banner_step_up": 3,
  "uma": 41,
  "support": null,
  "slot": 5,
  "is_target": true
}
```

Validation returns `400` for: a card whose type does not match the pool, a `slot` outside
1–10, a row naming neither card or both, and a card released on JP after the campaign's
`jp_cutoff_date`. That last check **grandfathers pairs the user already had stored**, so an
editor narrowing a cutoff cannot 400 a plan its owner never touched.

### `BannerUma`
```json
{
  "id": 1,
  "name": "string",
  "free_pulls": 0,
  "is_recommended": false,
  "admin_comments": "string | null",
  "banner_timeline": { "id": 1, "name": "string", "start_date": "ISO8601", "end_date": "ISO8601", "is_predicted": false, "jp_start_date": "ISO8601 | null", "jp_end_date": "ISO8601 | null", "global_start_date": "ISO8601 | null", "global_end_date": "ISO8601 | null", "image": "url | null" },
  "umas": [ { "id": 1, "name": "string", "image": "url | null", "admin_comments": "string | null", "purpose": "string", "first_jp_date": "ISO8601 | null", "is_time_limited": false, "is_three_star": true } ]
}
```

`is_recommended` is the editorial "Recommended" flag, set **per banner** — the uma and
support banners sharing a window are flagged independently, and `BannerStepUp` has no such
field. Presentation only: it stars the planner dropdown's option and gives the Timeline
panel its SSR treatment; no projection reads it.

`purpose` on a nested uma or support card is its **public** one-liner (at most 100
characters), rendered as the overlay on its Timeline tile. Never `null` — `""` means none.
Unlike the per-banner `recommendation` on `banner_timeline_data`'s cards, it describes the
card itself, so it is identical on every banner the card appears on.

`first_jp_date` on a nested uma or support card is the earliest JP banner it appeared
on, derived server-side (never stored) and the key the **temporal** half of selector
eligibility is judged on: a selector may only take cards released on JP on or before its
cutoff, inclusive. `null` means the card has never been featured on a banner in our data —
treat that as *unknown*, not *ancient*; eligibility refuses `null` under a real cutoff.

`is_time_limited` / `is_three_star` are the **intrinsic** half, and appear on umas only —
a support card has no equivalent. They are stored, not derived, and are independent of the
cutoff: a `true` / `false` here bars the uma from every selector and step-up there is,
including one with a `null` (unrestricted) cutoff. A client must check both halves. See
`calculatorapi/eligibility.py`.

### `BannerSupport`
```json
{
  "id": 1,
  "name": "string",
  "free_pulls": 0,
  "is_recommended": false,
  "admin_comments": "string | null",
  "banner_timeline": { ... },
  "support_cards": [ { "id": 1, "name": "string", "image": "url | null", "admin_comments": "string | null", "purpose": "string", "first_jp_date": "ISO8601 | null" } ]
}
```

### `BannerStepUp` (from `banner_step_up_data`)
```json
{
  "id": 1,
  "banner_timeline": { ... },
  "anniversary_event": 12,
  "name": "5th Anniversary ★3 Select Step-Up",
  "card_type": "uma | support",
  "banner_count": 2,
  "max_steps": 10,
  "jp_cutoff_date": "ISO8601 date | null",
  "image": "url | null",
  "admin_comments": "string | null",
  "order": 0
}
```

`banner_timeline` is nested exactly as it is on `BannerUma` / `BannerSupport`, which is
what lets the client resolve all three kinds of planner row through one code path.

Two fields are derived server-side rather than left to the client:

* `max_steps` — `banner_count * 5`. A step-up runs five steps per banner, and sending the
  product keeps the count and the rule that interprets it together.
* `jp_cutoff_date` — the **campaign's** cutoff, folded in the same way
  `AnniversaryEventProduct` folds it. A step-up's candidates are back-catalogue cards
  released on JP on or before this date. `null` means unrestricted. Read it from here
  rather than joining `anniversary_event_data`.

`anniversary_event` is the campaign's **FK id only** — the campaign itself arrives in
full under `anniversary_event_data`, and nesting it here would repeat the whole product
catalogue once per step-up.

There is **no step-up equivalent of `free_pulls` or featured cards.** A step-up grants no
free pulls, and the player picks their own cards from the back catalogue, so there is no
`umas` / `support_cards` array to send.

### `GameEvent`

`start_date`/`end_date`/`is_predicted`/`applied_offset_days` are RESOLVED from the linked `banner_timeline` (a `GameEvent` has no `schedule_offset_days` of its own — it inherits whatever offset its banner ended up with)
(not stored columns) — `end_date` trails the banner's own resolved end date by 4 days.
`banner_timeline` is a nullable id: not every event ties to a single banner (some tie to
Champions Meeting rewards instead, some are multi-banner campaign events), in which case
`start_date`/`end_date` are `null` and `is_predicted` is `false`. The standalone `GET
/events` route only ever resolves **confirmed** dates (no prediction) — the richer,
possibly-predicted dates shown here are exclusive to `/calculator-data`.

Reward amounts are fields on the event itself (no separate reward model/list).
`carat_amount` and the ticket/shard/crystal fields are earned once `start_date` passes;
`carats_throughout` is carats only, prorated client-side by elapsed time across
`start_date`..`end_date` — see `backend/docs/income-calculation.md`.

```json
{
  "id": 1,
  "name": "string",
  "image": "url | null",
  "start_date": "ISO8601 | null",
  "end_date": "ISO8601 | null",
  "is_predicted": false,
  "banner_timeline": 1,
  "carat_amount": 0,
  "carats_throughout": 0,
  "support_ticket_amount": 0,
  "uma_ticket_amount": 0,
  "sr_shard_amount": 0,
  "sr_crystal_amount": 0,
  "ssr_shard_amount": 0,
  "ssr_crystal_amount": 0
}
```

### `IncomeLedgerRow` (from `income_ledger`)

The flat, date-sorted timeline the projection queries for cumulative income totals, instead of accruing income window by window as it walks. Assembled by `calculatorapi/ledger.py` from the `GameEvent`, `ChampionsMeeting` and `LeagueOfHeroes` rows and date maps already built for this request — no extra queries, no prediction of its own.

```json
{
  "date": "ISO8601",
  "kind": "event | champions_meeting | league_of_heroes",
  "source_id": 1,
  "name": "Narita Brian",
  "is_predicted": false,
  "throughout_end": "ISO8601 | null",
  "event_number": null,
  "carats": 80,
  "carats_throughout": 1050,
  "uma_tickets": 0,
  "support_tickets": 0,
  "ssr_shards": 0,
  "ssr_crystals": 0,
  "sr_shards": 0,
  "sr_crystals": 0
}
```

Five things to know:

- **`date` is the instant the reward lands** — an event's resolved start; for a race event, its resolved **end less that kind's `RACE_REWARD_LEAD_TIME`**. A Champions Meeting settles its placements **24 hours before** its window closes, so its row sits a day ahead of the end date the timeline shows; League of Heroes has no lead time and is dated at its end. The offset is a `timedelta`, so it preserves time of day — a CM closing 21:59:59 pays at 21:59:59 the day before.
- **Race rows carry no amounts.** `champions_meeting` / `league_of_heroes` rows are indicators; what a placement pays depends on the user's rank row, which only the client knows. Every amount field is still present (as `0`), so the client never guards on shape.
- **`event_number` says which race event a row is**: `cm_number` / `loh_number` on race rows, `null` on `event` rows. It exists because a few specific events pay *below* the user's rank. League of Heroes #1 only ran to Platinum 1, so the client caps it there (`RACE_RANK_CAPS` in `frontend/src/utils/incomeLedger.ts`). The number rather than `source_id`, because it is the identity the game and the sheet use, and it is stable across databases.
- **`throughout_end` is the linked banner's end, with `GAME_EVENT_END_DATE_BUFFER` already removed.** The `carats_throughout` pool decays over the banner, not over the event, whose own `end_date` trails it by 4 days. Emitting it pre-stripped is what stops the client keeping its own copy of that constant.
- **No rows are filtered by "today".** The ledger is a set of dated facts, past ones included; the projection applies `today < date <= end` client-side so the whole calculation shares one anchor. Rows with no *resolvable* date are dropped, since a ledger row's only purpose is its position on the calendar.

### `CalculationConstants` (from `calculation_constants`)

Every tunable number the carat projection uses, from a singleton row edited in
Django admin under **Configuration → Calculation constants**. Served on every
request so an edit takes effect on the next page load without a deploy.

Field names and meanings come straight from
`calculatorapi/models/calculation_constants.py`, where each carries a `help_text`
naming the spreadsheet cell it corresponds to. The frontend mirrors the shape in
`src/types/constants.ts` and falls back to `DEFAULT_CONSTANTS` when the key is
absent.

Two things to know:

- **The decimal fields arrive as numbers, not strings.** DRF serialises
  `DecimalField` as a string by default; these are coerced to floats because the
  client feeds them straight into arithmetic, and `"0.664" * 2` is a silent `NaN`
  in JavaScript rather than an error. Affects `prediction_factor`,
  `throughout_decay_k`, `throughout_decay_linear_slope` and `step_up_target_rate`.
  **Every `DecimalField` added here needs the same coercion**, and a test walks the
  model to enforce that — a missed one is not a type error anywhere, just a `NaN`
  deep in the odds.
- **`training_pass_start_date` is a plain `YYYY-MM-DD` calendar day**, not a
  datetime.

`id` is deliberately excluded — there is only ever one row.

### `ChangelogEntry` (from `GET /changelog`)

Entries are returned newest-first by `date`. Each entry nests its `changes`,
ordered by their `order` field. `version` is an optional label (empty string when
unset). `category` is one of `"added"`, `"fixed"`, `"changed"`.

```json
{
  "id": 1,
  "title": "string",
  "version": "v1.2",
  "date": "YYYY-MM-DD",
  "changes": [
    {
      "id": 1,
      "category": "added",
      "text": "string",
      "order": 0
    }
  ]
}
```

### `Supporters` (from `GET /supporters`)

The public Patreon thank-you list rendered on the home page. Read-only by
construction: `PatreonSupporterViewSet` defines `list` and nothing else, so the
router never routes a write to it.

**Only supporters with `is_public=True` AND `is_active=True` appear as rows.**
Everyone else who is active is represented solely by `anonymous_count` — the
name never leaves the server. `is_public`, `is_active` and `patron_since` are
editorial state and are deliberately absent from the wire.

`tiers` is the full ordered tier list, sent so a client can render an ordering
or legend; `tier_name`/`tier_order` are flattened onto each supporter because
the list renders inline and needs nothing else about the tier. Both are `null`
for a supporter with no tier.

```json
{
  "tiers": [
    { "id": 1, "name": "Junior Class", "order": 10 }
  ],
  "supporters": [
    {
      "id": 1,
      "display_name": "string",
      "tier_name": "Junior Class",
      "tier_order": 10
    }
  ],
  "anonymous_count": 16
}
```

### `BannerTimeline` (from `banner_timeline_data`)

The `banner_timeline_data` key uses an expanded serializer that nests uma and support banners (including per-card/uma recommendation text from the through table), distinct from the flat `BannerTimelineSerializer` used inside `BannerUma`/`BannerSupport` objects.

**Date fields.** `start_date`/`end_date` are the **resolved** global dates: the confirmed global dates when set, otherwise dates **predicted** from the JP schedule (see `backend/calculatorapi/predictions.py`). `is_predicted` is `true` when they are an estimate. The raw source fields (`jp_start_date`, `jp_end_date`, `global_start_date`, `global_end_date`) are also exposed; `global_*` is null until a banner is officially confirmed. The same resolved values and `is_predicted` appear on every nested `banner_timeline` (inside `banner_uma_data`, `banner_support_data`, and `user_planned_banner_data`), keyed consistently by timeline id.

**Schedule offsets.** `schedule_offset_days` is the row's own manual correction to the prediction (0 for almost every row); `applied_offset_days` is the cumulative total — its own plus every offset earlier in the calendar — **already baked into** `start_date`/`end_date`. Both are 0 on confirmed rows, which offsets never touch. The cascade spans banners, Champions Meetings and League of Heroes together, so a banner's offset can show up as a non-zero `applied_offset_days` on a later Champions Meeting. `applied_offset_days` is diagnostic only — the dates are complete without it. See `backend/docs/data-model.md` for the rule.

**`event_type`.** A constant tag identifying which model a row came from. The frontend merges `banner_timeline_data`, `champions_meeting_data` and `league_of_heroes_event_data` into one sorted timeline array and narrows on this. It exists because the three shapes are **not** reliably distinguishable structurally: `ChampionsMeeting` and `LeagueOfHeroes` are field-identical apart from their number, and a `BannerTimeline` shares every base field with both. Emitted by `EventTypeMixin` (`calculatorapi/views/mixins.py`) on the three serializers that feed those keys — *not* on the flat `BannerTimelineSerializer` used for nested banners and `GET /bannertimelines`.

```json
{
  "id": 1,
  "name": "string",
  "event_type": "banner_timeline",
  "start_date": "ISO8601 (resolved: confirmed or predicted)",
  "end_date": "ISO8601 (resolved: confirmed or predicted)",
  "is_predicted": false,
  "jp_start_date": "ISO8601 | null",
  "jp_end_date": "ISO8601 | null",
  "global_start_date": "ISO8601 | null",
  "global_end_date": "ISO8601 | null",
  "schedule_offset_days": 0,
  "applied_offset_days": 0,
  "image": "url | null",
  "banner_umas": [ { "id": 1, "name": "string", "free_pulls": 0, "is_recommended": false, "admin_comments": "string | null", "umas": [ { ...uma + "recommendation": "string | null" } ] } ],
  "banner_supports": [ { ... } ],
  "anniversary_event": { "id": 8, "name": "3rd Anniversary", "event_type": "anniversary", "accent_label": "", "image": "url | null", "part_number": 2 },
  "banner_step_ups": [ { "id": 1, "name": "string", "card_type": "uma | support", "banner_count": 2 } ]
}
```

**`banner_step_ups`.** Select Step-Ups running in this window, as a summary — the full
records are sent under `banner_step_up_data`. Reusing `BannerStepUpSerializer` here would
nest `banner_timeline`, which is the very row this hangs off, sending each timeline back
inside itself.

Because a step-up's FK points at the campaign **Part** it runs in, this list is non-empty
on exactly one timeline row per campaign, with no extra filtering. The frontend sums it
per pool into a chip such as "2 ★3 + 3 SSR Step-Up".

**`anniversary_event`.** The campaign this banner is a Part of, or `null`. A flat summary
rather than the full `AnniversaryEvent`: the timeline only needs enough to draw the
attached strip, and the same campaign is already sent in full — with its products — under
`anniversary_event_data`. Nesting it here would repeat the whole catalogue once per Part.
Note the link is owned entirely by `AnniversaryEventBanner`; `BannerTimeline` itself has
no campaign column.

### `ChampionsMeeting` (from `champions_meeting_data`)

Same **resolved-date** contract as `BannerTimeline`: `start_date`/`end_date` are the confirmed global dates when set, otherwise dates **predicted** from the JP schedule; `is_predicted` flags an estimate; the raw `jp_*`/`global_*` fields are exposed (`global_*` null until confirmed). Champions Meetings resolve against their own anchor set, independent of banners and League of Heroes. Schedule offsets are the one exception — they cascade across all three content types, so `applied_offset_days` here may originate from a banner.

**Course details.** `track` through `direction` and the five `*_recommendation` values are hand-entered in the admin and unknown until a meeting is announced. The columns are non-null, so "unknown" is a **sentinel, not a null**: `"TBD"` for the text fields and `0` for the recommendations. Clients should treat those two values as "not announced" rather than displaying them raw.

```json
{
  "id": 1,
  "name": "string",
  "event_type": "champions_meeting",
  "cm_number": 1,
  "start_date": "ISO8601 (resolved: confirmed or predicted)",
  "end_date": "ISO8601 (resolved: confirmed or predicted)",
  "is_predicted": false,
  "jp_start_date": "ISO8601 | null",
  "jp_end_date": "ISO8601 | null",
  "global_start_date": "ISO8601 | null",
  "global_end_date": "ISO8601 | null",
  "schedule_offset_days": 0,
  "applied_offset_days": 0,
  "image": "url | null",
  "track": "string", "surface_type": "string", "distance": "string", "length": "string",
  "track_condition": "string", "season": "string", "weather": "string", "direction": "string",
  "speed_recommendation": 0, "stamina_recommendation": 0, "power_recommendation": 0,
  "guts_recommendation": 0, "wit_recommendation": 0
}
```

### `LeagueOfHeroes` (from `league_of_heroes_event_data`, and `GET /leagueofheroes`)

Same resolved-date contract as above, with its own anchor set (schedule offsets excepted — those cascade across all three content types). Note the standalone `GET /leagueofheroes` route serves raw confirmed dates only (`is_predicted` is always `false` and `applied_offset_days` always 0 there); predictions and offsets are emitted only via `GET /calculator-data`.

**Identical to `ChampionsMeeting` apart from `event_type` and `loh_number`** — same course details, same stat recommendations, same `"TBD"`/`0` sentinels — because the two render through one shared timeline card. Treat them as one shape with two tags: a field added to one belongs on the other.

```json
{
  "id": 1,
  "name": "string",
  "event_type": "league_of_heroes",
  "loh_number": 1,
  "start_date": "ISO8601 (resolved: confirmed or predicted)",
  "end_date": "ISO8601 (resolved: confirmed or predicted)",
  "is_predicted": false,
  "jp_start_date": "ISO8601 | null",
  "jp_end_date": "ISO8601 | null",
  "global_start_date": "ISO8601 | null",
  "global_end_date": "ISO8601 | null",
  "schedule_offset_days": 0,
  "applied_offset_days": 0,
  "image": "url | null",
  "track": "string", "surface_type": "string", "distance": "string", "length": "string",
  "track_condition": "string", "season": "string", "weather": "string", "direction": "string",
  "speed_recommendation": 0, "stamina_recommendation": 0, "power_recommendation": 0,
  "guts_recommendation": 0, "wit_recommendation": 0
}
```
