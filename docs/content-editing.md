# Content Editing Guide

How to manage the game data behind the Uma Musume Carat Calculator through the
admin site. Written for content editors — no technical background needed.


## Logging in

Go to `https://<your-domain>/admin/` and sign in with the staff account you
were given. You'll land on the **Dashboard**, which shows a few headline usage
numbers at the top and a **Reports** card linking to the analytics dashboard.

Everything you can edit is grouped in the left sidebar into sections —
**Banners**, **Events & competitions**, **Characters**, **Income tables**,
**Site content**, and **Users & access**. Click a section heading to expand or
collapse it. The light/dark theme toggle is in the sidebar next to your account
name.

You will only see the models you have permission to edit. User accounts and
players' saved plans are managed separately and never appear in your view.

## The banner structure

Banners are organized in three levels:

1. **Banner timeline** — the date window a set of banners runs in (e.g.
   "Almond Eye", July 10–20). Has a name, start/end dates, and an image.
2. **Uma banner / Support card banner** — the actual gacha banners inside a
   timeline. Each has a name and the number of **free pulls** it grants
   (the calculator counts these toward what players can afford).
3. The **umas / support cards featured on the banner**, each with an optional
   recommendation text.

### Adding a new banner, start to finish

1. **Banners → Timelines → Add**. Fill in the name, dates, and
   image, then **Save and continue editing**.
2. Still on the timeline page, add rows under **Uma banners** and/or
   **Support card banners** (name + free pulls). Save.
3. Click the **Change** link next to a banner row to open the banner's own
   page. Under **Umas on banner** (or **Support cards on banner**), pick each
   featured uma/card — start typing a name and the box searches for you — and
   optionally add a recommendation. Save.

If a featured uma or card doesn't exist yet, create it first under **Umas** /
**Support cards** (name + image), or use the green **+** next to the picker.

### Confirmed vs. predicted dates

A row's **JP server dates** are always filled in; the **global server dates**
stay blank until the event is officially confirmed — until then the site shows
players dates *predicted* from the JP schedule. The list shows each row's state
as a **Confirmed** (green) or **Predicted** (amber) badge, and the **Global
dates** filter in the sidebar lists everything still awaiting confirmation, so
you can spot what needs dates filled in.

This same JP/Global date system applies to **banner timelines, Champions
Meetings, and League of Heroes events** — all three have split JP/Global date
sections, the Confirmed/Predicted badge, and the Global dates filter.

### Fixing predicted dates when global falls behind schedule

Predictions assume global keeps working through JP's back-catalogue at a steady
pace. When it doesn't — a banner gets delayed, a break week gets inserted —
every predicted date after that point is wrong by the same number of days.

The **Schedule offset (advanced)** box on each row is the fix. Set it on the one
row where the delay starts, and:

- **that row moves forward by that many days**, and
- **so does every later banner, Champions Meeting and League of Heroes event.**

⚠️ **This is not a local edit.** One offset moves a large part of the calendar.
That is the whole point — a one-week delay really does push everything after it
back a week — but it means you should only reach for it when global's schedule
has genuinely slipped, not to nudge a single row you think looks wrong.

You can set more than one, and they add up. If you set **+7** on a banner in
August and later set **+3** on an event in September, everything from September
onward moves **10** days.

Two things that keep this safe:

- **It only affects predicted rows.** Once a row's global dates are confirmed,
  its offset stops doing anything — both to itself and to the rows after it.
  Confirmed dates are facts and are never moved.
- **You don't have to clean up after yourself.** When a delayed banner is finally
  confirmed, its offset switches itself off, and the rows after it start
  predicting from that banner's real date instead. The list shows a leftover
  value as *"+7d (inactive)"* so you can tell it's no longer doing anything.

The **Offset** column shows which rows are carrying one, and the **Schedule
offset** filter in the sidebar lists them all — the quickest way to see what is
currently shifting the calendar. To undo an offset, set it back to `0`.

You can also enter a negative number to pull dates *earlier*, if global gets
ahead rather than behind.

### Seeing which banners players care about

The Uma banner and Support card banner lists have a **Planned by** column —
how many players currently have that banner in their pull plan. Click the
column header to sort by it.

### Marking a banner as Recommended

Tick **Recommended** on an uma or support card banner when it is exceptionally
worth pulling on. You can tick it on the banner's own page, straight from the
**Uma banners** / **Support card banners** lists (it's a column there), or on
the banner rows of a timeline's page.

Players see it two ways: the banner's **Featured Umamusume** / **Featured
Support Cards** panel on the Timeline turns gold and shimmers, and the banner
gets a gold star in the calculator's banner dropdown. The uma banner and the
support banner in the same window are separate, so ticking one leaves the other
alone. Use it sparingly — a star on everything is a star on nothing.

### A card's purpose

Every uma and support card has a **Purpose** box, under **Shown to players**.
Write one short line on what the card is for, like "Great pace parent" or
"Great for front runners". It is **public**: players see it when they hover
over (or tap) the card's picture on the Timeline. Leave it blank and nothing
shows.

It's capped at 100 characters so it always fits on the smallest card. It
describes the card itself, so it appears on every banner the card is featured
on; advice about one particular banner still belongs in that banner's
recommendation text. On the **Umas** / **Support cards** lists, the **By
purpose → Empty** filter shows every card that doesn't have one yet.

## Events

**Game events** hold an event's name, dates, and image; its **reward amounts**
(carats, tickets, shards, crystals) are edited directly on the same page, under
a **Rewards** section. There are two carat fields:

- **Carat amount** — paid out in full as soon as the event starts (along with
  every ticket/shard/crystal field on the page).
- **Carats throughout** — spread evenly across the whole event's run instead of
  paid all at once, for campaigns that trickle out carats day by day rather than
  granting them in one lump.

Both directly affect players' projections, so keep the amounts accurate to the
in-game event.

## Site pages and FAQ

The **About** page, the **carat income guide** and the whole **FAQ** are edited
here, under **Site content** in the sidebar.

**Pages** has one row per page. Open it, change the **title**, the **meta
description** (the one or two sentences search engines show under the title;
it is not on the page itself) and the **text**, then save. You cannot add or
delete a page: each one has a route in the site's code, so a new page is a
request to whoever maintains the code. The page shows "Last updated" from the
date you saved, so there is no date to type.

**FAQ** is a list of categories. Each category is a section on the FAQ page,
with its questions edited underneath it on the same screen. Add, reorder
(lower **order** numbers come first) and delete freely. Tick **Show on
homepage** on a question to put it in the short FAQ teaser on the homepage;
the homepage shows every ticked question, in FAQ order, so three or four is
about right.

**Don't change a question's slug once it is published.** The slug is the part
after `#` in a link straight to that question (`/faq#do-i-need-an-account`),
and other pages link to some of them. Reword the question all you like; leave
the slug.

**The text is written in markdown**, which is plain text with a few marks:

| You type | You get |
|---|---|
| a blank line between two blocks of text | two paragraphs |
| `## Where the numbers come from` | a section heading |
| `**bold words**` | **bold words** |
| `*italic words*` | *italic words* |
| `[the FAQ](/faq)` | a link to the FAQ page (links to this site start with `/`) |
| `[the sheet](https://docs.google.com/...)` | a link to another site |
| `- one thing` on each of several lines | a bulleted list |
| `1. first step` on each of several lines | a numbered list |

Anything else you type shows up as written. You cannot break the site with a
typo here; the worst case is a page that reads oddly, and you can fix it by
saving again.

**When you save, visitors see the change straight away.** Search engines are
the exception: they read a copy of each page that is baked in when the site is
built. So when you are done editing for the day, go to **Pages** and press
**Rebuild website**. It takes about 5 to 10 minutes, and it picks up every edit
saved before you pressed it, so press it once at the end rather than after
each save. If you press it while a rebuild is already running, it tells you and
does nothing. If it says it is not set up, that is a message for the site
owner (see the technical section below).

## Changelog (patch notes)

**Changelog entries** are the update notes shown on the public Changelog page.
Each entry has a **title**, a **date**, and an optional **version** label (e.g.
`v1.2` — leave it blank if you don't use versions). The individual update lines
are edited in the table at the bottom of the entry's page: give each line a
**category** (Added / Fixed / Changed), the **text** of the change, and an
**order** number (lower numbers show first). Entries appear newest-first on the
site, and the home page shows how long ago the most recent entry was posted.

**Check the "Source" column before you edit one.** Entries marked *Written here*
are yours — they behave like every other page in this admin. Entries marked *Repo
file* are written in the project's code and copied over on each deploy, so an
edit you make to one here is replaced the next time the site updates. There is
nothing wrong with reading them; just ask for a code change rather than editing
in place. New entries you add are always *Written here*: leave the **key** field
blank, which it is by default.

## Patreon supporters

The thank-you list at the bottom of the home page. Two pages in **Site content**:

- **Patreon tiers** — your pledge tiers. Each has a name and an **order** number;
  lower numbers show first, and the first two tiers get a bit more visual emphasis
  on the site. You can renumber the whole ladder on the list screen and save once.
  **The tier name is shown on the site**, as the heading above that tier's
  supporters, so write it as something you are happy for visitors to read.
  Put your *highest* tier on the lowest order number — the site reads the first
  tier as the top one and gives it the strongest styling.
- **Patreon supporters** — the people. Each has a **display name**, an **email**,
  a tier, and three switches.

  The email is filled in by the import and is **only ever visible to you, here**.
  It is never shown on the website. It's there so you can tell two supporters
  apart when their names look alike, or when somebody changes their Patreon name
  and turns up as a second row — you can also search the list by it.

**"Show name publicly" is the important one, and it starts switched off.**

A supporter with it off is still thanked — they are counted in the *"… and 12
anonymous supporters"* line — but their name never appears. Switch it on only for
a name the person chose to be thanked by. This matters because Patreon's export
often lists someone's **real name from their payment details** rather than the
handle they use publicly, and nobody has agreed to have that put on a website.

You can flip the switch for lots of people at once from the supporters list
screen, then hit Save.

**"Is active"** — switch this off when someone's pledge ends. Don't delete the
row: keeping it means that if they come back you still have their details and
your publish decision, instead of starting over.

### Importing the monthly CSV

Rather than typing everyone in, download the **members export** from Patreon and
use the **Import Patreon CSV** button on the supporters list.

1. Leave **Preview only** ticked the first time and hit *Run import*. You'll get a
   summary of exactly what would change — who'd be added, deactivated or moved
   tier — without anything being saved.
2. If it looks right, untick **Preview only** and run it again.

Two things worth knowing:

- **The import never publishes anyone.** New people are added switched off, and it
  won't change the switch for anyone already on your list, either way. Publishing
  is always your separate decision.
- Only the **Name**, **Tier** and **Patron Status** columns are read. Email
  addresses, Discord handles, addresses and payment details in that file are
  ignored and never saved anywhere.
- **"Deactivate supporters missing from this file"** is off by default. Only tick
  it when you've uploaded a *complete* export — otherwise a partial file would
  switch off everyone it happens to leave out.

If new tiers appear in the file they're created for you at the bottom of the
order; set their proper order on the Patreon tiers page afterwards.

## Champions Meetings & League of Heroes

- **Champions Meetings**: the form is grouped into basic info, **JP / Global
  server dates** (see *Confirmed vs. predicted dates* above), **Track details**
  (surface, distance, weather, …) and **Stat recommendations**. Recommended umas
  are added at the bottom of the page with a search picker.
- **League of Heroes events**: name, **JP / Global server dates** (same
  Confirmed/Predicted system), and image.

## Rank tables

**Club ranks, Team trials ranks, Champions Meeting ranks, League of Heroes
ranks** define how many carats (and tickets/shards, where applicable) players
earn per rank. The amounts are edited directly in the list — change as many
rows as you need, then press **Save** once at the bottom. Only change these
when the game itself rebalances payouts — they feed every player's income
projection.

## Images

Anywhere you see an **Image** field, you have two ways to fill it:

- **Upload a new file** — use the upload box exactly as before. The file is
  stored automatically; you don't need to put it anywhere yourself first.
- **Choose from library** — click this button to browse every image already
  on the site. Search by file name, or switch folders (umas, support cards,
  banner timelines, …) with the dropdown to reuse an image from a different
  section — handy when an event or Champions Meeting should carry the same
  artwork as its banner.

Picking from the library does **not** make a second copy, so reusing one image
in several places is free and stays consistent everywhere it appears.

A few things worth knowing:

- If you pick from the library and upload a file in the same edit, the
  **uploaded file wins**. Use **Undo** next to the selection if you change
  your mind before saving.
- The library list is remembered for a few minutes for speed. If you just
  uploaded something in another tab and don't see it yet, press the **refresh**
  icon at the top of the picker.
- If the picker says it can't load the library, the site can still upload
  files normally — tell the site owner, it means the image storage is
  unreachable.

## A few care notes

- **Deleting a timeline deletes its banners**, and deleting a banner removes
  it from any player's saved plan. Prefer editing over deleting.
- Dates are stored with timezones; keep the same convention as existing
  entries (UTC).
- "Admin comments" fields are notes for editors.

---

## For the site owner (technical setup)

Creating a content-editor account:

1. Make sure the permission group exists. Production creates and refreshes it on
   every deploy, so there is nothing to do there. Locally, run it once:
   ```bash
   python manage.py create_content_editor_group
   ```
2. In the admin as a superuser: **Users → Add** → set username/password →
   on the next screen check **Staff status** and add the **Content editors**
   group → Save.

The group grants add/change/delete/view on all game content, rank tables and
site content (pages and FAQ), and nothing else. Rerunning the command is safe — it resets the group's
permissions to exactly the intended set (see
`calculatorapi/management/commands/create_content_editor_group.py`).

The **Rebuild website** button needs two environment variables on the API
component: `DO_APP_ID` (set) and `DO_API_TOKEN` (a DigitalOcean personal access
token that can manage apps). Until the token is set the button explains that it
is not configured and does nothing. Any staff account can press it once it is.

## One page to leave alone

There is a **Configuration → Calculation constants** page in the sidebar. You
almost certainly do not need it, and you will not normally be able to open it —
the content-editor account has no permission for it, so the section is hidden.

It holds the raw numbers behind every carat estimate on the site: how many carats
a day players earn, what a pull costs, how far ahead banner dates are predicted.
Changing one number there changes the estimate every single visitor sees, the
moment they next load the page. A wrong value there doesn't look like a mistake —
it looks like the calculator is broken.

Adding events, banners and campaigns (everything else in this guide) is safe and
reversible. That page is not. Leave it to whoever maintains the code.
