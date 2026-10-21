from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework import routers
from calculatorapi.views import (
    TeamTrialsRankViewSet,
    ClubRankViewSet,
    ChampionsMeetingRankViewSet,
    LeagueOfHeroesRankViewSet,
    LeagueOfHeroesViewSet,
    CalculatorViewSet,
    GameEventViewSet,
    ChangelogEntryViewSet,
    PatreonSupporterViewSet,
    UmaViewSet,
)
from calculatorapi.admin_pvp import PictureAutocompleteJsonView
from calculatorapi.views.admin_images import admin_image_library
from calculatorapi.views.analytics import analytics_dashboard
from calculatorapi.views.user import user_logout
from calculatorapi.views.account import account_detail
from calculatorapi.views.plan_routes import plan_detail, plan_list
from calculatorapi.views.plan_public import public_plan_copy, public_plan_summary
from calculatorapi.views.account_linking import (
    account_link_complete,
    account_link_delete,
    account_link_start,
)
from calculatorapi.views.visits import site_visit
from calculatorapi.views.bot_api import (
    bot_blocked_term,
    bot_item,
    bot_moderate,
    bot_reports,
    bot_unblock,
    bot_undo,
)
from calculatorapi.views.patreon_supporters import patreon_sync
from calculatorapi.views.site_content import site_content
from calculatorapi.views.pvp_guide import pvp_guide_detail
from calculatorapi.views.pvp_catalogue import pvp_catalogue
from calculatorapi.views.pvp_team import pvp_team_detail, pvp_team_list
from calculatorapi.views.gallery import (
    gallery_comment_detail,
    gallery_my_posts,
    gallery_post_comments,
    gallery_post_copy,
    gallery_post_detail,
    gallery_post_view,
    gallery_post_vote,
    gallery_report,
)
from calculatorapi.views.pvp_gallery import pvp_gallery_feed, pvp_team_publish
from calculatorapi.views.gallery_share import share_page, share_preview
from calculatorapi.views.plan_share import share_page as plan_share_page
from calculatorapi.views.plan_share import share_preview as plan_share_preview
from calculatorapi.views.social_auth import social_auth_start, social_auth_complete

router = routers.SimpleRouter(trailing_slash=False)
router.register(r"teamtrialranks", TeamTrialsRankViewSet, basename="teamtrialrank")
router.register(r"clubranks", ClubRankViewSet, basename="clubrank")
router.register(
    r"championsmeetingranks",
    ChampionsMeetingRankViewSet,
    basename="championsmeetingrank",
)
router.register(
    r"leagueofheroesranks",
    LeagueOfHeroesRankViewSet,
    basename="leagueofheroesrank",
)
router.register(r"leagueofheroes", LeagueOfHeroesViewSet, basename="leagueofheroes")
router.register(r"events", GameEventViewSet, basename="event")
router.register(r"changelog", ChangelogEntryViewSet, basename="changelog")
# Public thank-you list for the home page. Read-only by construction — the
# viewset has no write actions at all, not merely permission-gated ones.
router.register(r"supporters", PatreonSupporterViewSet, basename="supporter")
# The uma catalogue as picker options (id, name, image), for the oshi picker
# on /account — a page that never loads /calculator-data. Read-only, public.
router.register(r"umas", UmaViewSet, basename="uma")

urlpatterns = [
    path("", include(router.urls)),
    # Logout for every kind of account: deletes the bearer token. There is no
    # password login here (the staff one went on 2026-10-15: it handed out a
    # token for a password alone, around the admin's second factor) and
    # intentionally no "register" route. Ordinary accounts use /auth/* below;
    # staff sign in at /admin. -> views/user.py
    path("logout", user_logout, name="logout"),
    # GET: who the caller is, and what they are entitled to. Authenticated-only
    # and never cached — it is the SPA's source of truth for "am I signed in?",
    # replacing a localStorage token check that could only describe the browser.
    # DELETE: remove the caller's account (staff refused). → views/account.py
    path("account", account_detail, name="account"),
    # A signed-in user's named pull plans. The banner ROWS are still saved by
    # PATCH /calculator-data (now carrying plan_id); these create, rename,
    # switch, copy and delete the plans themselves. -> views/plan_routes.py
    path("plans", plan_list, name="plan-list"),
    path("plans/<int:plan_id>", plan_detail, name="plan-detail"),
    # A plan's share link, open to anyone: a summary for the landing card, and
    # taking a copy. Both 404 unless the owner switched sharing on.
    # -> views/plan_public.py
    path("plans/public/<str:public_id>", public_plan_summary,
         name="public-plan-summary"),
    path("plans/public/<str:public_id>/copy", public_plan_copy,
         name="public-plan-copy"),
    # Attaching a provider identity to an account that is ALREADY signed in.
    # Separate from /auth/* above and deliberately so: those create accounts,
    # these must never. The signed state here is bound to the user it was minted
    # for. → views/account_linking.py
    path(
        "account/link/<str:provider>/start",
        account_link_start,
        name="account-link-start",
    ),
    path(
        "account/link/<str:provider>/complete",
        account_link_complete,
        name="account-link-complete",
    ),
    path(
        "account/link/<str:provider>",
        account_link_delete,
        name="account-link-delete",
    ),
    # Traffic beacon, pinged once per session by the SPA. Public and write-only
    # — the frontend is a separate static site, so this is the only way Django
    # learns that a page was loaded at all.
    path("visit", site_visit, name="site-visit"),
    # Trigger for the scheduled supporters sync, called by a GitHub Action.
    # Authorised by a shared secret header, and 404s entirely while
    # PATREON_SYNC_SECRET is unset. Cannot publish a name — see the view.
    path("patreon/sync", patreon_sync, name="patreon-sync"),
    # The Discord bot worker's routes: moderators' clicks on report cards and
    # their /mod commands. Shared secret header (X-Bot-Key), 404 while
    # BOT_API_SECRET is unset. A Discord moderator is recorded by name and
    # never gets an account. → views/bot_api.py
    path("bot/reports", bot_reports, name="bot-reports"),
    path("bot/blocked-terms", bot_blocked_term, name="bot-blocked-term"),
    path("bot/items/<str:kind>/<str:ref>", bot_item, name="bot-item"),
    path("bot/items/<str:kind>/<str:ref>/moderate", bot_moderate, name="bot-moderate"),
    path("bot/items/<str:kind>/<str:ref>/unblock", bot_unblock, name="bot-unblock"),
    path("bot/items/<str:kind>/<str:ref>/undo", bot_undo, name="bot-undo"),
    # The admin-editable pages and FAQ, in one public read-only response. The
    # frontend build bakes it into the prerendered HTML; a loaded page fetches
    # it once more to pick up edits made since. → views/site_content.py
    path("site-content", site_content, name="site-content"),
    # One event's PvP guide. <kind> is "cm" or "loh", checked by the view so an
    # unknown kind gets the same JSON 404 as an unknown event. Drafts 404 for
    # everyone but staff. → views/pvp_guide.py
    path("pvp-guides/<str:kind>/<int:event_id>", pvp_guide_detail, name="pvp-guide"),
    # The team builder's catalogue: umas, cards and skills with aptitudes and
    # skill links. Public, cached, fetched lazily. → views/pvp_catalogue.py
    path("pvp-catalogue", pvp_catalogue, name="pvp-catalogue"),
    # A signed-in player's PvP teams. Guests keep theirs on the device.
    # → views/pvp_team.py
    path("pvp-teams", pvp_team_list, name="pvp-team-list"),
    path("pvp-teams/<int:team_id>", pvp_team_detail, name="pvp-team-detail"),
    # The PvP gallery: posting a team and one event's ranked feed.
    # → views/pvp_gallery.py
    path("pvp-teams/<int:team_id>/publish", pvp_team_publish, name="pvp-team-publish"),
    path("pvp-gallery/<str:kind>/<int:event_id>", pvp_gallery_feed, name="pvp-gallery-feed"),
    # One gallery post, whatever gallery it is in: read, take down, vote,
    # count a view, copy. → views/gallery.py
    path("gallery/my-posts", gallery_my_posts, name="gallery-my-posts"),
    path("gallery/posts/<str:public_id>", gallery_post_detail, name="gallery-post-detail"),
    path("gallery/posts/<str:public_id>/vote", gallery_post_vote, name="gallery-post-vote"),
    path("gallery/posts/<str:public_id>/view", gallery_post_view, name="gallery-post-view"),
    path("gallery/posts/<str:public_id>/copy", gallery_post_copy, name="gallery-post-copy"),
    path("gallery/posts/<str:public_id>/comments", gallery_post_comments, name="gallery-post-comments"),
    path("gallery/comments/<int:comment_id>", gallery_comment_detail, name="gallery-comment-detail"),
    path("gallery/reports", gallery_report, name="gallery-report"),
    # A gallery post's permanent link and its link-preview picture. Served
    # at /post on the SITE's host, not under /api: an ingress rule in
    # .do/app.yaml sends /post here. → views/gallery_share.py
    path("post/<str:public_id>", share_page, name="gallery-share"),
    path("post/<str:public_id>/preview.jpg", share_preview, name="gallery-share-preview"),
    # A shared plan's link, the same way: /plan on the site's host, sent here
    # by its own ingress rule, then on to the landing card at /app/<id>.
    # → views/plan_share.py
    path("plan/<str:public_id>", plan_share_page, name="plan-share"),
    path("plan/<str:public_id>/preview.jpg", plan_share_preview, name="plan-share-preview"),
    # Social sign-in (Google / Discord). The <provider> segment is validated by
    # the view against oauth.SUPPORTED_PROVIDERS rather than by a URL regex, so
    # an unknown provider gets a JSON 404 instead of Django's HTML one.
    path("auth/<str:provider>/start", social_auth_start, name="social-auth-start"),
    path("auth/social", social_auth_complete, name="social-auth-complete"),
    # Must come BEFORE admin.site.urls — the admin URLconf ends in a
    # catch-all that would 404 this path. admin_view() makes the page
    # staff-only (redirects everyone else to the admin login).
    path(
        "admin/analytics/",
        admin.site.admin_view(analytics_dashboard),
        name="admin-analytics",
    ),
    # Same placement rule as analytics above: before admin.site.urls, and
    # admin_view() makes it staff-only. Feeds the "choose an existing image"
    # picker on the content change forms.
    path(
        "admin/image-library/",
        admin.site.admin_view(admin_image_library),
        name="admin-image-library",
    ),
    # The admin's autocomplete JSON, with each result's picture added so the
    # uma, card and skill pickers can show one (calculatorapi/admin_pvp.py).
    # Before admin.site.urls: same path as the admin's own "autocomplete/",
    # so reverse("admin:autocomplete") still names it and this view answers.
    path(
        "admin/autocomplete/",
        admin.site.admin_view(PictureAutocompleteJsonView.as_view(admin_site=admin.site)),
        name="admin-autocomplete-pictures",
    ),
    path("admin/", admin.site.urls),
    path(
        "calculator-data",
        CalculatorViewSet.as_view(
            {"get": "get_calculator_data", "patch": "update_calculator_data"}
        ),
        name="calculator-data",
    ),
    # The OpenAPI schema, and Swagger UI over it. Both public. The docs page
    # fetches the schema by a RELATIVE url on purpose: production serves this
    # API under /api and strips the prefix, so the absolute /schema that
    # reverse() would produce lands on the static site instead.
    path("schema", SpectacularAPIView.as_view(), name="schema"),
    path("docs", SpectacularSwaggerView.as_view(url="schema"), name="api-docs"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
