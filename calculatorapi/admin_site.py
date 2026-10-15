"""The admin site: django-unfold's theme with django-otp's second factor in
front of it, installed as `admin.site` through calculatorproject/apps.py.

WHY A SITE SUBCLASS AND NOT A MIDDLEWARE
----------------------------------------
`AdminSite.has_permission` is the one gate every admin page, and every view
wrapped in `admin.site.admin_view()` (analytics, the image library, the
autocomplete), already passes through. django-otp's OTPAdminSite adds "and this
session passed the second factor" to it. Everything else here exists to make
that gate liveable: a login form with one code field, a first-login setup page,
backup codes, and a page to reissue them.

HOW A LOGIN GOES
----------------
1. POST username, password and code to /admin/login/. AdminLoginForm checks
   the password first through Django's own AdminAuthenticationForm (staff and
   active only). django-axes counts a wrong password and, at
   AXES_FAILURE_LIMIT, refuses the (username, address) pair for the cool-off
   before the password is even compared.
2. ADMIN_REQUIRE_OTP off: logged in, as before this module existed.
3. On, and the account has a confirmed authenticator: the code is checked
   against it (six digits) or against the backup codes (anything else). A
   right code sets `user.otp_device`; django-otp's user_logged_in handler
   stores that device in the session, and `has_permission` reads
   `request.user.is_verified()` on every admin request from then on.
4. On, and the account has NO confirmed authenticator yet: the person is NOT
   logged in. A short-lived note in the session (who, when) sends them to
   /admin/two-factor/setup/, which shows a QR code, confirms the device with
   the first code it produces, logs them in verified, issues ten backup codes
   and shows them exactly once.

A session that is signed in but not verified cannot come out of this form, and
`has_permission` would refuse it anyway. Lost phone: the owner deletes the
authenticator row on the person's user page (calculatorapi/admin.py) and the
next login is step 4 again. Lost phone AND backup codes, for the only owner:
ADMIN_REQUIRE_OTP=False in the DigitalOcean dashboard is the way back in.
-> docs/admin.md ("Signing in and two-factor")
"""

import time
from base64 import b32encode

import qrcode
import qrcode.image.svg
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth.views import LoginView
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django_otp.admin import OTPAdminSite
from django_otp.forms import OTPAuthenticationFormMixin
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from unfold.forms import AuthenticationForm as UnfoldAuthenticationForm
from unfold.sites import UnfoldAdminSite
from unfold.widgets import BASE_INPUT_CLASSES

# Session keys. ENROL_KEY is written by the login view for an account that
# passed the password check but owns no authenticator yet, and read ONLY by the
# setup page. It carries a user id and a time, never a logged-in state.
ENROL_KEY = "admin_otp_enrol"
# How long the setup page honours that note: long enough to install an
# authenticator app on the spot, short enough that a note left in an
# abandoned browser is worthless by the time anyone finds it.
ENROL_WINDOW_SECONDS = 600
# Freshly issued backup codes, parked for ONE render of the backup-codes page
# and popped by it. django-otp keeps each unused code on its StaticToken row;
# the page is the one time the person is shown the whole set to copy.
BACKUP_CODES_KEY = "admin_otp_backup_codes"
BACKUP_CODE_COUNT = 10
AUTHENTICATOR_NAME = "Authenticator app"
BACKUP_DEVICE_NAME = "Backup codes"
# ModelBackend by name: with axes's backend also configured, auth_login() has
# to be told which one vouched for the user, and the setup page vouches
# through a password check that already happened.
PASSWORD_BACKEND = "django.contrib.auth.backends.ModelBackend"


def otp_required():
    """Whether the admin asks for a second factor right now. Read at call
    time, not import time, so override_settings works in tests and a flip in
    the dashboard takes effect on the next restart with no code change."""
    return settings.ADMIN_REQUIRE_OTP


def authenticator_for(user):
    """The user's confirmed authenticator-app device, or None."""
    return TOTPDevice.objects.devices_for_user(user, confirmed=True).first()


def backup_device_for(user):
    """The user's backup-code device, or None. Confirmed or not: the set is
    issued confirmed and never exists unconfirmed, but the lookup should not
    hide a row that somehow is."""
    return StaticDevice.objects.filter(user=user, name=BACKUP_DEVICE_NAME).first()


def issue_backup_codes(user):
    """Replace the user's backup codes with BACKUP_CODE_COUNT fresh ones and
    return them. ONE device per user, reused, so reissuing never leaves an old
    set valid beside the new one."""
    device, _ = StaticDevice.objects.get_or_create(
        user=user, name=BACKUP_DEVICE_NAME, defaults={"confirmed": True}
    )
    if not device.confirmed:
        device.confirmed = True
        device.save(update_fields=["confirmed"])
    device.token_set.all().delete()
    codes = [StaticToken.random_token() for _ in range(BACKUP_CODE_COUNT)]
    StaticToken.objects.bulk_create(StaticToken(device=device, token=code) for code in codes)
    return codes


def _code_widget(placeholder):
    """A text input dressed like unfold's, hinting the phone at its numeric
    keypad and at autofilling a one-time code it just received."""
    return forms.TextInput(attrs={
        "autocomplete": "one-time-code",
        "inputmode": "numeric",
        "autocapitalize": "off",
        "spellcheck": "false",
        "placeholder": placeholder,
        "class": " ".join(BASE_INPUT_CLASSES),
    })


def _normalised_code(raw):
    """What people type: "123 456", or a backup code in capitals."""
    return (raw or "").replace(" ", "").strip().lower()


def _looks_like_app_code(code):
    return code.isdigit() and len(code) == 6


class AdminLoginForm(OTPAuthenticationFormMixin, UnfoldAuthenticationForm):  # pylint: disable=too-many-ancestors
    """Username, password and one code field.

    django-otp's own admin form adds a device <select>, because since 1.7.4 it
    refuses to try every device in turn (a wrong code would then count as a
    failure against the backup codes too, and throttle them). There is nothing
    to select here: an account has one authenticator and one set of backup
    codes, and the shape of the code says which was typed. Six digits is the
    app; anything else is a backup code.

    `needs_enrolment` is the form's third outcome beside valid and invalid:
    the password was right, the second factor is required, and the account
    has nothing to check a code against yet. AdminLoginView reads it.
    """

    otp_token = forms.CharField(
        required=False,
        label=_("Authentication code"),
        widget=_code_widget("6-digit code, or a backup code"),
    )
    otp_error_messages = {
        **OTPAuthenticationFormMixin.otp_error_messages,
        "token_required": _(
            "Enter the code from your authenticator app, or one of your backup codes."
        ),
        "invalid_token": _(
            "That code is not right. App codes change every 30 seconds; "
            "try the one showing now."
        ),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.needs_enrolment = False

    def clean_otp_token(self):
        return _normalised_code(self.cleaned_data.get("otp_token"))

    def clean(self):
        # The password first: AdminAuthenticationForm raises for a wrong one,
        # a non-staff account or an inactive one, and nothing below runs.
        # (Its clean() fills and returns self.cleaned_data.)
        super().clean()
        user = self.get_user()
        if user is None or not otp_required():
            return self.cleaned_data
        if authenticator_for(user) is None:
            # Right password, no authenticator yet. No error and no code
            # check: the view sends them to set one up instead of in.
            self.needs_enrolment = True
            return self.cleaned_data
        self.clean_otp(user)
        return self.cleaned_data

    def _chosen_device(self, user):
        code = self.cleaned_data.get("otp_token") or ""
        if _looks_like_app_code(code):
            return authenticator_for(user)
        # A backup code, or something that is neither: the authenticator then
        # refuses it, which reads better than "select a device".
        return backup_device_for(user) or authenticator_for(user)


class SetupCodeForm(forms.Form):
    """The first code from a freshly scanned authenticator."""

    code = forms.CharField(
        label=_("Code from the app"), max_length=16, widget=_code_widget("6-digit code")
    )

    def clean_code(self):
        return _normalised_code(self.cleaned_data["code"])


class AdminLoginView(LoginView):
    """Django's LoginView with one extra branch: a staff account that passed
    the password check but has no authenticator is sent to set one up, and is
    NOT logged in on the way."""

    site = None

    def form_valid(self, form):
        if form.needs_enrolment:
            # A fresh session id before anything is written to it, so a
            # session id planted in this browser beforehand holds nothing.
            self.request.session.cycle_key()
            self.request.session[ENROL_KEY] = {
                "user": form.get_user().pk,
                "at": time.time(),
            }
            return HttpResponseRedirect(
                reverse("admin:two_factor_setup", current_app=self.site.name)
            )
        return super().form_valid(form)


def _qr_svg(data):
    """The otpauth:// URL as an inline SVG. SVG rather than a PNG data URL:
    nothing to encode, crisp at any size, and no image library in the path."""
    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    return image.to_string(encoding="unicode")


def _grouped(key):
    """A base32 secret in groups of four, for typing into an app by hand."""
    return " ".join(key[i:i + 4] for i in range(0, len(key), 4))


class CalculatorAdminSite(OTPAdminSite, UnfoldAdminSite):  # pylint: disable=too-many-ancestors
    """unfold's site (theme, search, its extra urls) behind django-otp's gate,
    with the enrolment and backup-code pages added. Instantiated with no
    arguments by Django's DefaultAdminSite, hence the "admin" default: every
    reverse("admin:...") in this project needs that name, and OTPAdminSite's
    own default is "otpadmin"."""

    login_form = AdminLoginForm
    login_template = "admin/two_factor/login.html"

    def __init__(self, name="admin"):
        super().__init__(name)

    def has_permission(self, request):
        # Staff and active first (AdminSite, through unfold), then verified by
        # a device in THIS session while the second factor is required.
        # super(OTPAdminSite, self) on purpose: OTPAdminSite's own version has
        # no notion of the switch.
        if not super(OTPAdminSite, self).has_permission(request):
            return False
        return not otp_required() or request.user.is_verified()

    def get_urls(self):
        # Ours first: AdminSite's list ends in catch-alls.
        two_factor = [
            path("two-factor/", self.admin_view(self.two_factor_status), name="two_factor"),
            # Not admin_view(): nobody is logged in yet when this page is
            # used. Its own gate is the session note (see _enrolling_user).
            path(
                "two-factor/setup/",
                csrf_protect(never_cache(self.two_factor_setup)),
                name="two_factor_setup",
            ),
            path(
                "two-factor/backup-codes/",
                self.admin_view(self.two_factor_backup_codes),
                name="two_factor_backup_codes",
            ),
        ]
        return two_factor + super().get_urls()

    @method_decorator(never_cache)
    def login(self, request, extra_context=None):
        """AdminSite.login with our LoginView in place of Django's, so the
        enrolment branch exists. Mirrors Django 6.0's method line for line
        otherwise; keep it that way when upgrading."""
        if request.method == "GET" and self.has_permission(request):
            return HttpResponseRedirect(reverse("admin:index", current_app=self.name))
        context = {
            **self.each_context(request),
            "title": _("Log in"),
            "subtitle": None,
            "app_path": request.get_full_path(),
            "username": request.user.get_username(),
            "otp_required": otp_required(),
        }
        if REDIRECT_FIELD_NAME not in request.GET and REDIRECT_FIELD_NAME not in request.POST:
            context[REDIRECT_FIELD_NAME] = reverse("admin:index", current_app=self.name)
        context.update(extra_context or {})
        request.current_app = self.name
        return AdminLoginView.as_view(
            site=self,
            extra_context=context,
            authentication_form=self.login_form,
            template_name=self.login_template,
        )(request)

    # ── Enrolment ───────────────────────────────────────────────────────────

    def _enrolling_user(self, request):
        """The account the session note says is mid-setup, or None: no note,
        an expired one, or an account that can no longer enrol (gone,
        deactivated, demoted, or enrolled meanwhile in another tab)."""
        note = request.session.get(ENROL_KEY)
        if not isinstance(note, dict):
            return None
        if time.time() - float(note.get("at", 0)) > ENROL_WINDOW_SECONDS:
            return None
        user = get_user_model().objects.filter(
            pk=note.get("user"), is_active=True, is_staff=True
        ).first()
        if user is None or authenticator_for(user) is not None:
            return None
        return user

    def two_factor_setup(self, request):
        user = self._enrolling_user(request)
        if user is None:
            request.session.pop(ENROL_KEY, None)
            return HttpResponseRedirect(reverse("admin:login", current_app=self.name))
        # Reuse an unconfirmed device from an abandoned attempt, so a reload
        # keeps the QR code the app may already have scanned.
        device = TOTPDevice.objects.devices_for_user(user, confirmed=False).first()
        if device is None:
            device = TOTPDevice.objects.create(user=user, name=AUTHENTICATOR_NAME, confirmed=False)
        form = SetupCodeForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            if device.verify_token(form.cleaned_data["code"]):
                device.confirmed = True
                device.save(update_fields=["confirmed"])
                codes = issue_backup_codes(user)
                # Signed in AND verified in one step: otp_device is what
                # django-otp's user_logged_in handler stores in the session.
                user.otp_device = device
                auth_login(request, user, backend=PASSWORD_BACKEND)
                request.session.pop(ENROL_KEY, None)
                request.session[BACKUP_CODES_KEY] = codes
                return HttpResponseRedirect(
                    reverse("admin:two_factor_backup_codes", current_app=self.name)
                )
            form.add_error(
                "code",
                _("That code is not right. Enter the one the app is showing now; "
                  "scan the QR code again if you need to."),
            )
        request.current_app = self.name
        context = {
            **self.each_context(request),
            "title": _("Set up two-factor authentication"),
            "form": form,
            "qr_svg": _qr_svg(device.config_url),
            "manual_key": _grouped(b32encode(device.bin_key).decode()),
            "username": user.get_username(),
        }
        return TemplateResponse(request, "admin/two_factor/setup.html", context)

    # ── Backup codes and status (both behind admin_view: verified only) ─────

    def two_factor_backup_codes(self, request):
        codes = request.session.pop(BACKUP_CODES_KEY, None)
        if not codes:
            return HttpResponseRedirect(reverse("admin:two_factor", current_app=self.name))
        context = {
            **self.each_context(request),
            "title": _("Your backup codes"),
            "codes": codes,
        }
        return TemplateResponse(request, "admin/two_factor/backup_codes.html", context)

    def two_factor_status(self, request):
        user = request.user
        if request.method == "POST" and request.POST.get("action") == "regenerate":
            request.session[BACKUP_CODES_KEY] = issue_backup_codes(user)
            messages.success(request, _("New backup codes issued. The old ones no longer work."))
            return HttpResponseRedirect(
                reverse("admin:two_factor_backup_codes", current_app=self.name)
            )
        backup = backup_device_for(user)
        context = {
            **self.each_context(request),
            "title": _("Two-factor authentication"),
            "otp_required": otp_required(),
            "authenticator": authenticator_for(user),
            "backup_codes_left": backup.token_set.count() if backup else 0,
        }
        return TemplateResponse(request, "admin/two_factor/status.html", context)
