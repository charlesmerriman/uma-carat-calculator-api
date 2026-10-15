"""App configs for the project package.

CalculatorAdminConfig stands in for "django.contrib.admin" in INSTALLED_APPS so
that `admin.site`, the instance every admin module registers with, is OUR site
class (calculatorapi.admin_site.CalculatorAdminSite: unfold's theme with
django-otp's second factor in front of it) rather than Django's or unfold's.
`AdminConfig.default_site` is Django's supported way to swap the default site.

It only works with unfold loaded through `unfold.apps.BasicAppConfig`: unfold's
default app config overwrites `admin.site` with its own instance in ready(),
which would silently undo this and leave the admin on a password alone.
"""

from django.contrib.admin.apps import AdminConfig


class CalculatorAdminConfig(AdminConfig):
    default_site = "calculatorapi.admin_site.CalculatorAdminSite"
