"""Generate static per-article pages, sitemap.xml and robots.txt.

LEGACY - superseded by the frontend's SvelteKit build.

The website is now a SvelteKit app prerendered to `build/`. It generates
`article/<slug>.html` from `archive-data.json` plus its own `sitemap.xml`,
`robots.txt`, `llms.txt` and 404 page, and the deploy workflow publishes only
`build/`. The files this script writes at the repository root are therefore not
deployed, and keeping two sources for the same URLs would let them drift.

`run_automation.py` no longer calls it. It is kept because the generated files
are still present in the frontend repository, and because `make_slug()` below is
still the thing that assigns the `slug` values the frontend now depends on - the
frontend derives every article URL from `archive.articles[].slug`. It also still
writes the persisted slug back into `archive-data.json`.

New slugs are only ever assigned when missing, so a later title correction
cannot change a published URL.

OUTPUTS (in the frontend repo)
    article/<slug>.html   one page per article
    sitemap.xml           every article page plus the main site pages
    robots.txt            allow all + point at the sitemap
    archive-data.json     gains a persisted `slug` per article

USAGE
    python gjms-automation/build_pages.py --dry-run
    python gjms-automation/build_pages.py
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

console = Console(stderr=True)

FRONTEND = Path("/Users/emumba/Downloads/projects/gjms-frontend")
DEFAULT_ARCHIVE = FRONTEND / "archive-data.json"
DEFAULT_ABSTRACTS = FRONTEND / "abstracts.json"
DEFAULT_OUT = FRONTEND / "article"
SITE = "https://gjmsweb.com"

STATIC_PAGES = [
    ("index.html", "1.0", "weekly"),
    ("archive.html", "0.9", "weekly"),
    ("current-issue.html", "0.8", "monthly"),
    ("editorial-board.html", "0.6", "monthly"),
    ("author-guideline.html", "0.6", "monthly"),
    ("impact-factor.html", "0.5", "monthly"),
    ("vision-mission.html", "0.5", "yearly"),
    ("contact.html", "0.4", "yearly"),
]

# Head / nav / footer mirror archive.html so generated pages look native.
HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="../style.css">
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            navy: { 50:'#f0f4fa', 100:'#dce4f5', 200:'#b8c9eb', 300:'#8aa6d9', 400:'#5c82c7', 500:'#3d66b5', 600:'#2d4f96', 700:'#1e3a6e', 800:'#152a52', 900:'#0f2557', 950:'#060d1b' },
            gold: { 400:'#d4af37', 500:'#c8a94e', 600:'#b8962e' }
          }
        }
      }
    }
  </script>
  <script src="../theme.js"></script>
  <title>@@PAGE_TITLE@@</title>
  <meta name="description" content="@@DESCRIPTION@@" />
  <link rel="canonical" href="@@CANONICAL@@" />
  <meta property="og:type" content="article" />
  <meta property="og:title" content="@@OG_TITLE@@" />
  <meta property="og:description" content="@@DESCRIPTION@@" />
  <meta property="og:url" content="@@CANONICAL@@" />
  <meta name="citation_title" content="@@OG_TITLE@@" />
@@AUTHOR_META@@@@DATE_META@@  <script type="application/ld+json">
@@JSONLD@@
  </script>
</head>

<body class="bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100 min-h-screen flex flex-col">
  <header class="bg-white dark:bg-navy-950 shadow-sm border-b border-gray-100 dark:border-navy-800">
    <div class="container mx-auto flex flex-wrap px-6 py-4 items-center justify-between">
      <a href="../index.html" class="flex items-center gap-3 text-navy-900 dark:text-white">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" stroke="currentColor" stroke-linecap="round"
          stroke-linejoin="round" stroke-width="2" class="w-10 h-10 text-white p-2 bg-navy-800 dark:bg-gold-500 rounded-lg"
          viewBox="0 0 24 24">
          <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"></path>
        </svg>
        <div>
          <span class="text-lg md:text-xl font-heading font-bold leading-tight">GJMSSH</span>
          <span class="hidden sm:block text-xs text-gray-500 dark:text-gray-400">Global Journal of Management, Social Sciences &amp; Humanities</span>
        </div>
      </a>
      <button onclick="toggleTheme()" class="theme-toggle text-gray-600 dark:text-gray-300" aria-label="Toggle theme">
        <svg class="theme-icon-sun" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 3v2.25m6.364.386l-1.591 1.591M21 12h-2.25m-.386 6.364l-1.591-1.591M12 18.75V21m-4.773-4.227l-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z" /></svg>
        <svg class="theme-icon-moon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z" /></svg>
      </button>
    </div>
    <nav class="bg-navy-900 dark:bg-navy-950">
      <div class="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div class="relative flex h-14 items-center justify-between text-sm text-navy-200 overflow-x-auto gap-1">
          <a href="../index.html" class="px-3 py-1.5 hover:text-white whitespace-nowrap rounded-md">Home</a>
          <a href="../impact-factor.html" class="px-3 py-1.5 hover:text-white whitespace-nowrap rounded-md">Impact Factor</a>
          <a href="../editorial-board.html" class="px-3 py-1.5 hover:text-white whitespace-nowrap rounded-md">Editorial Board</a>
          <a href="../author-guideline.html" class="px-3 py-1.5 hover:text-white whitespace-nowrap rounded-md">Author's Guide</a>
          <a href="../Publication Ethics.pdf" class="px-3 py-1.5 hover:text-white whitespace-nowrap rounded-md">Publication Ethics</a>
          <a href="../vision-mission.html" class="px-3 py-1.5 hover:text-white whitespace-nowrap rounded-md">Vision/Mission</a>
          <a href="../archive.html" class="px-3 py-1.5 text-white font-semibold whitespace-nowrap rounded-md">Archive</a>
        </div>
      </div>
    </nav>
  </header>

  <main class="flex-1">
    <div class="container mx-auto px-6 py-10 max-w-4xl">
      <nav class="text-sm text-gray-500 dark:text-gray-400 mb-6" aria-label="Breadcrumb">
        <a href="../index.html" class="hover:text-navy-700 dark:hover:text-gold-500">Home</a>
        <span class="mx-2">/</span>
        <a href="../archive.html" class="hover:text-navy-700 dark:hover:text-gold-500">Archive</a>
        <span class="mx-2">/</span>
        <span>@@VOLUME@@</span>
      </nav>

      <article class="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-100 dark:border-gray-700 p-8">
        <p class="text-xs font-semibold tracking-wide uppercase text-gold-600 dark:text-gold-500 mb-3">@@ISSUE_TITLE@@</p>
        <h1 class="font-heading text-2xl md:text-3xl font-bold leading-snug text-navy-900 dark:text-white mb-4">@@TITLE@@</h1>
        <p class="text-base text-gray-700 dark:text-gray-300 mb-2">@@AUTHORS@@</p>
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-6">Pages @@PAGES@@</p>
@@ABSTRACT_BLOCK@@
        <div class="flex flex-wrap gap-3 pt-6 border-t border-gray-100 dark:border-gray-700">
          <a href="@@PDF@@" target="_blank" rel="noopener"
             class="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg bg-navy-800 dark:bg-gold-500 text-white dark:text-navy-950 text-sm font-semibold hover:bg-navy-700 dark:hover:bg-gold-400 transition">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
            Download PDF
          </a>
          <a href="../archive.html"
             class="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg border border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-200 text-sm font-semibold hover:border-navy-400 dark:hover:border-gold-500 transition">
            Back to archive
          </a>
        </div>
      </article>
    </div>
  </main>

  <footer class="bg-navy-950 text-gray-400 mt-auto">
    <div class="container mx-auto px-6 py-12">
      <div class="grid md:grid-cols-3 gap-8">
        <div>
          <div class="flex items-center gap-2 mb-4">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" stroke="currentColor" stroke-linecap="round"
              stroke-linejoin="round" stroke-width="2" class="w-8 h-8 text-white p-1.5 bg-navy-800 rounded-lg"
              viewBox="0 0 24 24">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"></path>
            </svg>
            <span class="text-white font-heading font-bold">GJMSSH</span>
          </div>
          <p class="text-sm">A blind peer-reviewed quarterly journal since 2015.</p>
        </div>
        <div>
          <h3 class="text-white font-semibold mb-4">Quick Links</h3>
          <ul class="space-y-2 text-sm">
            <li><a href="../current-issue.html" class="hover:text-gold-500 transition">Current Issue</a></li>
            <li><a href="../archive.html" class="hover:text-gold-500 transition">Archive</a></li>
            <li><a href="../author-guideline.html" class="hover:text-gold-500 transition">Author's Guide</a></li>
            <li><a href="../editorial-board.html" class="hover:text-gold-500 transition">Editorial Board</a></li>
          </ul>
        </div>
        <div>
          <h3 class="text-white font-semibold mb-4">Contact</h3>
          <ul class="space-y-2 text-sm">
            <li>Email: gjmsweb@proton.me</li>
            <li>Website: gjmsweb.com</li>
          </ul>
        </div>
      </div>
      <div class="border-t border-navy-800 mt-8 pt-8 text-center text-sm text-gray-500">
        &copy; 2026 Global Journal of Management, Social Sciences and Humanities. All rights reserved.
      </div>
    </div>
  </footer>
</body>
</html>
"""


def slugify(value: str, limit: int = 80) -> str:
    """URL-safe slug, ASCII only."""
    text = unicodedata.normalize("NFKD", value or "")
    text = text.encode("ascii", "ignore").decode("ascii").casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:limit].strip("-")


def make_slug(article: dict) -> str:
    """Stable slug: readable words plus a hash of the PDF URL.

    The hash keeps slugs unique and stable even if two papers share a title. The
    slug is persisted in archive-data.json so a later title fix cannot change an
    already-published URL.
    """
    key = str(article.get("pdf") or article.get("title") or "")
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:7]
    base = slugify(str(article.get("title") or "article"))
    return f"{base}-{digest}" if base else f"article-{digest}"


def trim_for_meta(text: str, limit: int = 155) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > 60 else cut).rstrip() + "…"


def replace_tokens(template: str, values: dict[str, str]) -> str:
    """Fill @@TOKEN@@ placeholders.

    `str.format` cannot be used here: the head template embeds a Tailwind config
    script full of literal `{}` braces.
    """
    out = template
    for key, value in values.items():
        out = out.replace(f"@@{key}@@", value)
    return out


def render(article: dict, issue: dict, abstract: str, slug: str) -> str:
    title = str(article.get("title") or "")
    authors = ""
    raw_authors = article.get("authors")
    if isinstance(raw_authors, list):
        authors = ", ".join(str(a) for a in raw_authors)
    elif raw_authors:
        authors = str(raw_authors)
    pages = str(article.get("pages") or "")
    pdf = str(article.get("pdf") or "")
    volume = str(issue.get("volume") or "")
    issue_title = str(issue.get("title") or "")
    canonical = f"{SITE}/article/{slug}.html"
    description = trim_for_meta(abstract or title)

    author_meta = "".join(
        f'  <meta name="citation_author" content="{html.escape(name.strip(), quote=True)}" />\n'
        for name in authors.split(",")
        if name.strip()
    )
    date_meta = ""
    year = re.search(r"(19|20)\d{2}", issue_title)
    if year:
        date_meta = f'  <meta name="citation_publication_date" content="{year.group(0)}" />\n'

    jsonld = {
        "@context": "https://schema.org",
        "@type": "ScholarlyArticle",
        "headline": title,
        "name": title,
        "url": canonical,
        "inLanguage": "en",
        "abstract": abstract or None,
        "pagination": pages or None,
        "author": [
            {"@type": "Person", "name": name.strip()}
            for name in authors.split(",")
            if name.strip()
        ],
        "isPartOf": {
            "@type": "PublicationIssue",
            "name": issue_title or None,
            "isPartOf": {"@type": "PublicationVolume", "name": volume or None},
        },
    }
    if pdf:
        jsonld["encoding"] = {
            "@type": "MediaObject",
            "contentUrl": pdf,
            "encodingFormat": "application/pdf",
        }
    jsonld_text = json.dumps(
        {k: v for k, v in jsonld.items() if v is not None},
        indent=2,
        ensure_ascii=False,
    )

    if abstract:
        abstract_block = (
            '        <section aria-labelledby="abstract-heading" class="mb-2">\n'
            '          <h2 id="abstract-heading" class="font-heading text-lg font-semibold text-navy-900 dark:text-white mb-3">Abstract</h2>\n'
            f'          <p class="text-gray-700 dark:text-gray-300 leading-relaxed text-justify">{html.escape(abstract)}</p>\n'
            "        </section>"
        )
    else:
        abstract_block = ""

    return replace_tokens(
        HEAD,
        {
            "PAGE_TITLE": html.escape(f"{title} | GJMSSH", quote=True),
            "DESCRIPTION": html.escape(description, quote=True),
            "CANONICAL": html.escape(canonical, quote=True),
            "OG_TITLE": html.escape(title, quote=True),
            "AUTHOR_META": author_meta,
            "DATE_META": date_meta,
            "JSONLD": jsonld_text,
            "VOLUME": html.escape(volume),
            "ISSUE_TITLE": html.escape(issue_title),
            "TITLE": html.escape(title),
            "AUTHORS": html.escape(authors) if authors else "",
            "PAGES": html.escape(pages),
            "ABSTRACT_BLOCK": abstract_block,
            "PDF": html.escape(pdf, quote=True),
        },
    )


def build_sitemap(slugs: list[str]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, priority, freq in STATIC_PAGES:
        lines.append(
            f"  <url><loc>{SITE}/{path}</loc>"
            f"<changefreq>{freq}</changefreq><priority>{priority}</priority></url>"
        )
    for slug in slugs:
        lines.append(
            f"  <url><loc>{SITE}/article/{slug}.html</loc>"
            f"<changefreq>yearly</changefreq><priority>0.7</priority></url>"
        )
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


def build_robots() -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n\n"
        f"Sitemap: {SITE}/sitemap.xml\n"
    )


def run_build(
    archive: Path = DEFAULT_ARCHIVE,
    abstracts_path: Path = DEFAULT_ABSTRACTS,
    out: Path = DEFAULT_OUT,
    dry_run: bool = False,
) -> dict[str, int]:
    """Build article pages, sitemap.xml and robots.txt; return a summary.

    Also called by `run_automation.py` after new articles are added, so newly
    published papers get a page automatically.
    """
    issues = json.loads(archive.read_text(encoding="utf-8"))
    abstracts = (
        json.loads(abstracts_path.read_text(encoding="utf-8"))
        if abstracts_path.exists()
        else {}
    )

    articles: list[tuple[dict, dict]] = []
    for issue in issues:
        for article in issue.get("articles", []):
            articles.append((issue, article))

    # Assign missing slugs (never overwrite an existing one: URLs must be stable).
    new_slugs = 0
    for _, article in articles:
        if not article.get("slug"):
            article["slug"] = make_slug(article)
            new_slugs += 1

    seen: dict[str, int] = {}
    for _, article in articles:
        slug = str(article["slug"])
        seen[slug] = seen.get(slug, 0) + 1
    duplicates = {s: n for s, n in seen.items() if n > 1}
    if duplicates:
        raise SystemExit(f"Duplicate slugs would overwrite pages: {duplicates}")

    console.print(
        f"[cyan]Writing {len(articles)} article pages to {out} "
        f"({new_slugs} new slugs)...[/cyan]"
    )
    out.mkdir(parents=True, exist_ok=True)

    written = 0
    with_abstract = 0
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TextColumn("[cyan]{task.fields[current]:<44}[/cyan]"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Pages", total=len(articles), current="")
        for issue, article in articles:
            slug = str(article["slug"])
            abstract = abstracts.get(str(article.get("pdf") or ""), "")
            if abstract:
                with_abstract += 1
            if not dry_run:
                (out / f"{slug}.html").write_text(
                    render(article, issue, abstract, slug), encoding="utf-8"
                )
            written += 1
            progress.update(task, current=f"{slug[:44]}")
            progress.advance(task)

    slugs = [str(a["slug"]) for _, a in articles]
    if not dry_run:
        (out.parent / "sitemap.xml").write_text(build_sitemap(slugs), encoding="utf-8")
        (out.parent / "robots.txt").write_text(build_robots(), encoding="utf-8")
        archive.write_text(
            json.dumps(issues, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
        )

    console.print(f"\n[bold]═══ PAGES BUILT ═══[/bold]")
    console.print(f"article pages       : {written}")
    console.print(f"  with abstract     : {with_abstract}")
    console.print(f"  without abstract  : {written - with_abstract}")
    console.print(f"new slugs assigned  : {new_slugs}")
    console.print(f"sitemap urls        : {len(slugs) + len(STATIC_PAGES)}")
    if dry_run:
        console.print("\n[bold yellow]Dry run: nothing written.[/bold yellow]")
    else:
        console.print(f"[bold green]Wrote {out}, sitemap.xml and robots.txt[/bold green]")

    return {
        "pages": written,
        "with_abstract": with_abstract,
        "new_slugs": new_slugs,
        "sitemap_urls": len(slugs) + len(STATIC_PAGES),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--abstracts", type=Path, default=DEFAULT_ABSTRACTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_build(args.archive, args.abstracts, args.out, args.dry_run)


if __name__ == "__main__":
    main()
