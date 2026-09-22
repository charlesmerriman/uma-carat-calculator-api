# Authentication & Privacy

Developer notes on how sign-in works and which properties must be preserved.
For endpoint request/response shapes see [api-reference.md](api-reference.md).

---

## The core privacy constraint

Ordinary accounts exist **only** as a Google/Discord/Patreon identity. The site
holds no email, no name, and no usable password for them.

This is a deliberate design constraint, not an incidental side effect of using
OAuth. Changes in this area should preserve it.

Concretely, a non-staff `CustomUser` row carries:

- blank `email`, `first_name`, `last_name`
- an unusable password
- a generated `user_xxxxxx` username
- and, since 2026-09-13, a **preference** the person may set on their account
  page: `display_name` (see below)

The linked `SocialAccount` row stores `(provider, subject_id)` — unique together —
and **nothing else about the person**. No name, no email, no picture. The opaque
`subject_id` is the only *identifying* value stored anywhere in the system.
An account may hold **several** rows (one per provider); `SocialAccount.user` is a
ForeignKey, not a OneToOne, precisely so that linking is possible.

> For one unshipped day (2026-09-12 to 2026-09-13) the provider's profile
> picture was stored on this row and Google's `profile` scope was requested for
> it. Both were removed before reaching production: the account picture is now
> a **supporter perk** drawn from the site's own art (see "Oshis" below), and
> free accounts have no picture. Any future profile attribute needs the same
> explicit decision this one got, and a policy edit first.

**Preferences are not profile attributes.** `CustomUser.display_name` (a name
shown beside the handle) and a supporter's **oshis** (`UserOshi`: an ordered
list of umas from the catalogue, the first of which is their picture) are things
the person *tells* us through `PATCH /account`, never things we *learn* from a
provider — the scopes and `oauth.Identity` are untouched by them. The handle
stays the row's identity; the name sits beside it and is unique (ignoring case,
because it will be visible to other users one day). The display
name is personal data (a chosen name is) and is served only to its owner and
blanked by `purge_user_pii`; the oshis are the site's own art, are not personal
data, and survive the purge. Neither reaches any public route today. (Oshis
**will** be shown publicly by a future feature; whatever route does so must join
through `PatreonSupporter.linked_user`, honour `is_public`, and never carry the
display name or handle alongside.)

**Oshis are entitlement-gated, and entitlement is derived.** How many a person
may hold is `benefits.oshi_slots(user)` — 5 / 3 / 1 by tier via
`OSHI_SLOT_LADDER`, 0 for a free account — resolved on every request from the
linked `PatreonSupporter` row, never stored. A lapse or a downgrade **keeps every
row** and simply stops covering some of them: `GET /account` still lists them
all, the picture is the first one only while `oshi_slots >= 1`, and `PATCH`
refuses only a list that **adds** past the slot count (reordering and removing
among what is already held is always allowed). → `views/account.py`,
`models/user_oshi.py`

Staff accounts are the exception: they keep password login so `/admin` and the
analytics dashboard remain reachable.

---

## The OAuth2 flow

Implemented in `calculatorapi/oauth.py` (pure logic) and
`calculatorapi/views/social_auth.py` (the views). Standard authorization-code flow:

1. `GET /auth/<provider>/start` returns the provider's consent URL plus a signed `state`.
2. The provider redirects the browser to the **frontend** at `/auth/callback`.
3. The SPA posts `{provider, code, state}` to `POST /auth/social`.
4. Django exchanges the code server-to-server and returns a DRF token.

The client secret never leaves Django. The `code` that transits the browser is safe —
single-use, valid for seconds, and unredeemable without the secret — and is strictly
better than putting a long-lived token in a URL.

`oauth.py` is a pure-logic module (no views, no ORM), mirroring the
`predictions.py` / `analytics.py` split used elsewhere in this app.

---

## Invariants

### Scopes are the narrowest that yield an id — do not widen them

- Google: `openid` — yields `sub` and nothing about the person. `profile` (name,
  picture, locale) and `email` are separate scopes and are not requested.
- Discord: `identify` — id, username, avatar hash, …; only `id` is read. Discord
  has no narrower scope that yields the id.
- Patreon: `identity` — the sparse fieldset names one throwaway boolean
  (`hide_pledges`), so the response carries the id and nothing about the person.
  (It must name *something*: an empty `fields[user]=` is an HTTP 400 from
  Patreon, which took sign-in down on 2026-09-09; with no fieldset at all
  Patreon sends the full default profile, picture included.)

None of them transmits an email address, a name or a picture we keep.
`exchange_code()` returns an `oauth.Identity(subject_id)` — a one-field
`NamedTuple`, so a second value cannot ride through without editing the type.
Widening a scope or an extractor changes what the privacy policy promises;
change the policy first.

**Patreon's email has its own scope, `identity[email]`. Never request it.** Unlike
the creator token used by the supporters sync — which carries every v2 scope
automatically, so `MEMBER_FIELDS` is the only thing narrowing it — this is a
user-consented app where the scope list genuinely is the boundary. Asking for the
email here would put an address in the response for every person who signs in.

### `state` is signed and provider-bound

Signed with `django.core.signing.dumps`, salt `calculatorapi.social-auth-state`,
`max_age` = `OAUTH_STATE_MAX_AGE_SECONDS`. It carries the provider name, so a Google
state cannot be replayed at Discord.

The frontend keeps a matching copy in `sessionStorage` under `oauthState.v1` — **that
browser binding is what actually defeats login CSRF**, not the signature alone.

sessionStorage rather than a cookie because dev is cross-origin (`:5173` → `:8000`),
and a cookie would need `SameSite=None; Secure`.

### Linking is a different door from sign-in, with its own salt

`views/social_auth.py` is `AllowAny` and **creates an account** for an identity it
does not recognise. `views/account_linking.py` is `IsAuthenticated` and **must never
create one** — it attaches an identity to the account already signed in.

They are separate views deliberately. Behind one view and a `mode` flag, a single
mis-evaluated branch turns "link my Patreon" into "sign me in as whoever owns this
Patreon".

Two things make the link flow safe, and **both** are required:

1. **The state is bound to the user**, carrying `u` (the user id it was minted for).
   Without it, an attacker starts a link on their own account, gets a victim's
   browser to complete it, and the attacker's identity lands on the victim's
   account — after which they can sign in as the victim at will.
2. **A separate salt**, `calculatorapi.account-link-state`. A *sign-in* state carries
   no `u` at all, so if the link view accepted sign-in salts, check 1 would pass
   vacuously and provide nothing.

The redirect-URI allowlist check is **imported** from `social_auth.py` rather than
reimplemented. Two copies could drift, and a drifted copy is an open redirector.

### An identity belongs to one account, and is never moved

Completing a link for an identity another account already owns returns **409**. We
do not reassign it: that would let anyone who can complete a consent screen strip
another account of its sign-in method.

A second identity for a provider the account already has is also **409** — one per
provider per account, so `DELETE /account/link/<provider>` stays unambiguous.

Consequence, and it is a real one: someone who signs in with Google today and
Patreon tomorrow lands in **two separate accounts**, and we cannot tell. `/login`
carries a line telling people to sign in the way they did before and link
afterwards. Merging two accounts means reconciling two plans, two purchase sets and
two selection sets — a real feature, deliberately out of scope here.

### Unlinking never removes the last way in

An ordinary account has an unusable password and no email to send a reset to, so
removing its only provider is an **unrecoverable lockout**, not an inconvenience.
`DELETE /account/link/<provider>` refuses with 400 when it would leave a
password-less account with no providers. Staff are exempt — their password works.

Enforced server-side. Hiding the button is a suggestion; this has to be a rule.

### Deleting an account is self-serve and takes the person's data with it

`DELETE /account` (`views/account.py`) exists because an account that holds no
email has no other way to ask. It deletes the `CustomUser` (display name with it)
and lets the models' `on_delete` rules decide the rest: the token, the
`SocialAccount` rows, the oshis and the whole plan cascade; the `PatreonSupporter` row
is `SET_NULL` and survives with its pointer cleared — the same treatment a
pledge gets on an unlink, a lapse or a purge. Staff are refused (`403`); admin
accounts are deleted in the admin, deliberately and logged. There is no undo.

### Google's `id_token` is decoded without signature verification

In `_decode_jwt_payload`. This is Google's documented approach for the authorization-code
flow: the token comes straight from their token endpoint over TLS, in exchange for the
client secret. `iss` / `aud` / `exp` are still asserted.

**If an `id_token` ever arrives from any other source, this must become a verifying parse.**

### `get_or_create` is passed the callable, not a call

```python
SocialAccount.objects.get_or_create(
    provider=..., subject_id=...,
    defaults={"user": _create_anonymous_user},   # NOT _create_anonymous_user()
)
```

Django only invokes the callable when it actually creates. Adding `()` would create an
orphan `CustomUser` on **every returning sign-in** — silently, because nothing else
breaks. Covered by `test_returning_user_same_account_no_orphan`.

### `POST /login` is staff-only and non-enumerable

A correct password on a non-staff account returns the same status **and the same body**
as a wrong password, so the endpoint cannot be used to enumerate usernames. Covered by
`test_non_staff_rejection_is_indistinguishable_from_wrong_password`.

### Redirect URI must match byte-for-byte

Derived once as `settings.OAUTH_REDIRECT_URI = f"{FRONTEND_URL}/auth/callback"`. It must
match the provider console entry exactly — a trailing slash breaks it.

Note the frontend owns the `/auth/callback` route: the SPA's `catchall_document:
index.html` serves it, and DigitalOcean ingress does **not** proxy it to Django.

### The redirect URI allowlist

`GET /auth/<provider>/start` accepts an optional `?redirect_uri=`. Omitted, it uses the
canonical `OAUTH_REDIRECT_URI` — which is what the deployed SPA does and what every
client did before the parameter existed. Supplied, it must appear **verbatim** in
`settings.OAUTH_ALLOWED_REDIRECT_URIS`, which is `OAUTH_REDIRECT_URI` plus the
comma-separated `OAUTH_EXTRA_REDIRECT_URIS` env var. An unlisted value is refused with a
400 rather than quietly falling back.

**The allowlist is the security boundary.** Honouring an arbitrary client-supplied
`redirect_uri` would make this an open redirector that mails single-use authorization
codes to whatever address the caller named. The server decides; the client only asks.

**Why it exists:** `npm run dev:live` runs the local Vite server against a *deployed*
backend. Without this, a sign-in started on `localhost:5173` returns to the deployed site
and the developer can never be authenticated while looking at real content. Production
sets `OAUTH_EXTRA_REDIRECT_URIS=http://localhost:5173/auth/callback` to permit exactly
that one address.

The consequence is that **`dev:live` can write to production**: a signed-in local
frontend saves to the live database under a real account. Staying signed out is what
keeps a session read-only.

**The chosen URI is sealed into the signed `state`** (`{"p": provider, "n": nonce,
"r": redirect_uri}`) and recovered from it at completion. Providers bind the code to the
redirect URI, so the token exchange must repeat it byte-for-byte — and a value the
browser could edit between the two halves of the flow would be worthless as a binding.
**Never read it from the client's `POST /auth/social` body.** At completion it is no
longer a redirect target (the browser is already back), so it is not re-checked against
the allowlist; that also keeps a login that was in flight during an allowlist change from
failing. A state with no `"r"` — one minted by a previous release, mid-deploy — falls
back to the canonical URI.

Covered by `SocialAuthRedirectUriTests` and `SocialAuthDefaultAllowlistTests`.

---

## `purge_user_pii`

Retires PII from accounts created before the social-login cutover.

```bash
python manage.py purge_user_pii --dry-run   # report only
python manage.py purge_user_pii             # prompts for confirmation
```

Strips email, name, password and the chosen `display_name` from all non-staff
accounts. The `SocialAccount` rows survive untouched — the `(provider,
subject_id)` pair identifies nobody without the provider's own database — and
so do the oshis: they are the site's art, not personal data. **Irreversible.**
After it runs, those accounts cannot sign in at all —
their plans stay in the database but are unreachable. Intended to be run once in
production.

**It always clears `PatreonSupporter.linked_user` for the accounts it purges**,
flag or no flag. The command's job is to make an account unreachable, and a
supporter link pointing at a purged account would outlive it — granting
entitlement to a login nobody can perform. The supporter **row** is untouched;
only the link goes. Blanking supporter *data* is separate and still needs
`--include-patreon`.

---

## Related

- Client-side flow, `oauthState.v1` handling, and the StrictMode double-mount guard:
  [../../frontend/docs/state-and-guest-mode.md](../../frontend/docs/state-and-guest-mode.md)
- Endpoint shapes: [api-reference.md](api-reference.md)
