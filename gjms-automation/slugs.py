"""Assign the stable `slug` that the website derives article URLs from.

WHY THIS IS SEPARATE FROM `build_pages.py`
    `slug` is load-bearing data, not presentation. The frontend prerenders one
    page per article with

        archive.articles.filter((article) => article.slug)

    so an article without a slug gets no page and no sitemap entry at all.
    `build_pages.py` is legacy (it wrote a parallel set of static pages that are
    no longer deployed), so slug assignment was moved here to survive its
    retirement.

STABILITY
    A slug is only ever assigned when missing and then persisted in
    `archive-data.json`, so correcting a title never changes a published URL.
    Never hand-write or renumber one.

USAGE
    assign_missing(issues) -> list of slugs assigned (empty when nothing to do)
"""

from __future__ import annotations

import hashlib
import re
import unicodedata


def slugify(value: str, limit: int = 80) -> str:
    """URL-safe, ASCII-only slug."""
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii").casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:limit].strip("-")


def make_slug(article: dict) -> str:
    """Readable words plus a hash of the PDF URL.

    The hash keeps slugs unique and stable even when two papers share a title.
    """
    key = str(article.get("pdf") or article.get("title") or "")
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:7]
    base = slugify(str(article.get("title") or "article"))
    return f"{base}-{digest}" if base else f"article-{digest}"


def assign_missing(issues: list[dict]) -> list[str]:
    """Assign a slug to every article lacking one. Returns the slugs assigned."""
    assigned: list[str] = []
    for issue in issues:
        for article in issue.get("articles", []):
            if not article.get("slug"):
                article["slug"] = make_slug(article)
                assigned.append(article["slug"])
    return assigned


def duplicates(issues: list[dict]) -> dict[str, int]:
    """Slugs used by more than one article, which would collide as URLs."""
    counts: dict[str, int] = {}
    for issue in issues:
        for article in issue.get("articles", []):
            slug = str(article.get("slug") or "")
            if slug:
                counts[slug] = counts.get(slug, 0) + 1
    return {slug: n for slug, n in counts.items() if n > 1}
