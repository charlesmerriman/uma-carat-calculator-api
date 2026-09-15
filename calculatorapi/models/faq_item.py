"""
One question and its answer on the FAQ page.

`show_on_homepage` replaces the hardcoded list the homepage teaser used to
select by: ticking it on a question puts it on the homepage, in FAQ order.
The frontend shows however many are ticked, so the teaser's length is an
editorial choice too.
"""

from django.db import models


class FaqItem(models.Model):
    category = models.ForeignKey(
        "calculatorapi.FaqCategory",
        on_delete=models.CASCADE,
        related_name="items",
    )
    slug = models.SlugField(
        max_length=80,
        unique=True,
        help_text=(
            "Lowercase, hyphens, no spaces: the question's anchor "
            "(/faq#do-i-need-an-account). Links elsewhere point at it, so don't "
            "change it once published, even if you reword the question."
        ),
    )
    question = models.CharField(max_length=200)
    answer = models.TextField(
        help_text=(
            "Markdown. Blank line between paragraphs, **bold**, "
            "[link text](/privacy-policy)."
        ),
    )
    order = models.PositiveIntegerField(
        default=0, help_text="Questions in a section appear lowest number first."
    )
    show_on_homepage = models.BooleanField(
        default=False,
        help_text="Show this question in the short FAQ teaser on the homepage.",
    )

    class Meta:
        ordering = ("category__order", "order", "id")
        verbose_name = "FAQ question"
        verbose_name_plural = "FAQ questions"

    def __str__(self):
        return str(self.question)
