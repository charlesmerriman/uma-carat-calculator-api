"""
Reads the site content seed files: what a fresh database's pages and FAQ
start as.

Used by the migration that created the tables (once, get_or_create by slug)
and by the tests. Kept free of model imports so a migration can call it
through `apps.get_model` without importing the live models.

After the first migrate the database owns the text. Editing a file here does
not change a deployed site; it changes what the NEXT fresh database starts
from. See SitePage's module docstring.
"""

from pathlib import Path

import yaml

SEED_DIR = Path(__file__).resolve().parent / "data" / "site_content"

# Title and meta description per page. The body is the markdown file of the
# same name. The slugs must be SitePage.Slug values.
PAGES = {
    "about": {
        "title": "About",
        "meta_description": (
            "What the Uma Musume Carat Calculator is, who makes it, and where its "
            "carat and ticket numbers come from. An unofficial fan project, not "
            "affiliated with Cygames."
        ),
    },
    "carat-income-guide": {
        "title": "How the Calculator Works Out Your Carats",
        "meta_description": (
            "How the Uma Musume Carat Calculator works out your carats: every income "
            "source and its schedule, a day-by-day worked example, how pulls are paid "
            "for, and why the number can differ from the game."
        ),
    },
}


def load_pages():
    """[{slug, title, meta_description, body}, ...] in PAGES order."""
    rows = []
    for slug, meta in PAGES.items():
        body = (SEED_DIR / f"{slug}.md").read_text(encoding="utf-8").strip() + "\n"
        rows.append({"slug": slug, "body": body, **meta})
    return rows


def load_faq():
    """The categories from faq.yaml, each with its `items` list, in file order.

    Item answers keep their trailing newline from the YAML block scalar; the
    frontend's markdown renderer does not care, and it keeps the seed byte-exact
    with the file.
    """
    with open(SEED_DIR / "faq.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def seed(SitePage, FaqCategory, FaqItem):  # pylint: disable=invalid-name
    """Create any seed row that does not exist yet. Never overwrites.

    Takes the model classes as parameters so the migration can pass its
    historical models and the tests the real ones.
    """
    for row in load_pages():
        SitePage.objects.get_or_create(slug=row["slug"], defaults=row)

    for order, category in enumerate(load_faq()):
        category_row, _ = FaqCategory.objects.get_or_create(
            slug=category["slug"],
            defaults={"title": category["title"], "order": order},
        )
        for item_order, item in enumerate(category["items"]):
            FaqItem.objects.get_or_create(
                slug=item["slug"],
                defaults={
                    "category": category_row,
                    "question": item["question"],
                    "answer": item["answer"],
                    "order": item_order,
                    "show_on_homepage": bool(item.get("show_on_homepage", False)),
                },
            )
