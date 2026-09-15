"""
A section of the FAQ page: a heading and its ordered questions.

The categories are a display grouping, not an index. A question's slug is
unique across the whole FAQ (see FaqItem) so a deep link never needs to know
which category holds it.
"""

from django.db import models


class FaqCategory(models.Model):
    slug = models.SlugField(
        max_length=80,
        unique=True,
        help_text=(
            "Lowercase, hyphens, no spaces: the section's anchor on the FAQ page "
            "(/faq#the-numbers). Don't change it once published."
        ),
    )
    title = models.CharField(max_length=120, help_text="The section heading.")
    order = models.PositiveIntegerField(
        default=0, help_text="Sections appear lowest number first."
    )

    class Meta:
        ordering = ("order", "id")
        verbose_name = "FAQ category"
        verbose_name_plural = "FAQ categories"

    def __str__(self):
        return str(self.title)
