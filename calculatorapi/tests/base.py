"""The base class every test inherits, and the test-only settings shared across modules."""

from django.core.cache import caches
from django.test import TestCase


class CalculatorTestCase(TestCase):
    """
    The base class for every test class in this package: a TestCase that starts
    each test with an empty cache.

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
