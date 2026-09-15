"""
A prose page the team edits from the admin: About, and the carat income guide.

WHY A FIXED LIST OF SLUGS
-------------------------
A page only exists if the frontend has a route for it, and a route is code.
`slug` is therefore a `choices` field rather than free text: editors change the
words on a page that exists, and adding a page is a code change (a new choice
here, a seed file, a frontend route). The admin refuses add and delete for the
same reason; see SitePageAdmin.

WHY MARKDOWN
------------
The site already renders markdown (the guide page did before this model
existed), it is plain text in the database, and react-markdown does not render
raw HTML by default, so a row can never put script on the site. Terms and the
Privacy Policy are NOT here: they make claims the code has to keep true, so
they stay in the frontend repo where a change to the code can change them.

WHO OWNS THE TEXT
-----------------
The seed files in calculatorapi/data/site_content/ create these rows once, on
first migrate (get_or_create, never overwrite). From then on the database owns
the words; the files are only what a fresh database starts from.
"""

from django.db import models


class SitePage(models.Model):
    class Slug(models.TextChoices):
        ABOUT = "about", "About"
        CARAT_INCOME_GUIDE = "carat-income-guide", "Carat income guide"

    slug = models.SlugField(
        max_length=50,
        unique=True,
        choices=Slug.choices,
        help_text="Which page this is. Fixed: each one has a route in the site's code.",
    )
    title = models.CharField(
        max_length=120,
        help_text="The page heading. Also the browser tab title.",
    )
    meta_description = models.CharField(
        max_length=300,
        help_text=(
            "One or two sentences search engines show under the page's title. "
            "Not shown on the page itself."
        ),
    )
    body = models.TextField(
        help_text=(
            "The page text, in markdown. Blank line between paragraphs, "
            "## for a section heading, **bold**, [link text](/faq)."
        ),
    )
    # Shown on the page as "Last updated", so an editor never has to type a date.
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("slug",)
        verbose_name = "page"
        verbose_name_plural = "pages"

    def __str__(self):
        return str(self.title)
