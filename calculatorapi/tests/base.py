"""The base class every test inherits, and the test-only settings shared across modules."""

from django.core.cache import caches
from django.test import Client, TestCase
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_static.models import StaticDevice


class VerifiedClient(Client):
    """Django's test client with a force_login that also passes the admin's
    second factor.

    The admin site (calculatorapi/admin_site.py) lets a session in only when
    django-otp can find a device of the user's in it. force_login writes the
    sign-in but no device, so every admin page would answer 302 and the
    hundred-odd admin tests would each have to enrol a device by hand. This
    client does it once per login: a confirmed backup-code device with no
    codes, and its id in the session, which is exactly what a real login
    leaves behind. Only for staff: an ordinary account has nothing to verify
    and the tests that force_login one expect to be turned away.

    The login FORM is tested with a plain Client (tests/test_admin_security.py),
    because there the form is the thing under test.
    """

    def force_login(self, user, backend=None):
        super().force_login(user, backend=backend)
        if not getattr(user, "is_staff", False):
            return
        device, _ = StaticDevice.objects.get_or_create(
            user=user, name="test-verified", defaults={"confirmed": True}
        )
        session = self.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()


class CalculatorTestCase(TestCase):
    """
    The base class for every test class in this package: a TestCase that starts
    each test with an empty cache, and whose client passes the admin's second
    factor on force_login (VerifiedClient above).

    Django rolls the database back between tests but leaves the cache alone,
    and the cache here is LocMem living in this one process. It holds the
    /calculator-data public payload and the throttle counters, so without this
    whatever one test cached is still there for the next, and a test's result
    can depend on which tests happened to run before it.

    The clear lives in run(), not setUp(), on purpose. Django does not require
    subclasses to call super().setUp() (its own __call__ docstring says so),
    and most classes here define setUp() without it, so a clear placed in
    setUp() would silently never run for them. Django calls run() for every
    test, after its own per-test setup and before the class's setUp().
    """

    client_class = VerifiedClient

    def run(self, result=None):
        """Clear every cache, then run the test exactly as TestCase would.
        Every alias, not just "default": the gallery keeps its view marks,
        throttle counters and preview images in caches of its own."""
        for alias in caches:
            caches[alias].clear()
        return super().run(result)


# Swaps out whitenoise's manifest static storage, which raises on any admin
# template that references a static file unless collectstatic has been run.
# Every module whose tests render admin pages imports it from here.
PLAIN_TEST_STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
