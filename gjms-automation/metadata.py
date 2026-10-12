"""Extract article title, authors, abstract and affiliations from PDF page 1.

WHY LINE-BASED, NOT NODE-BASED
    OpenDataLoader's structure tags are inconsistent across this journal's
    volumes, so heading *level* is not a title signal:
      * Khadija 2025 : H1 = title, H3 = author        (clean)
      * Saima   2025 : title/authors are plain paragraphs, H1 = "Abstract."
      * Asia Gul 2018: H3 = journal header, H2 = title, H1 = "ABSTRACT"
    Worse, one node often holds several logical lines, e.g. Tania Manzoor's
    title node is literally
        "Research Article\\nImpact of Renewable and Non-renewable ...\\n
         Environmental Quality: Evidence from Pakistan"
    and Haleema Rashid's author node is
        "Abdul Ghafoor Awan1, Haleema Rasheed2\\nABSTRACT: Males and females ..."

    So extraction walks the node tree for ORDER and FONT SIZE, then splits every
    node into lines and applies layout rules per line. That recovers the title
    and author line even when the PDF's tagging is nonsense.

PUBLIC API
    extract_metadata(pdf_path) -> ArticleMetadata

FALLBACKS
    No usable nodes (e.g. scanned PDF) -> `extract_first_page_text` (OCR via
    ocrmac) + `parse_article` from `extract_articles`; abstract is then empty.
    Right-to-left (Urdu) pages have no usable Latin metadata, so they are
    flagged `rtl=True` and the caller should keep any curated title.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from opendataloader_pdf import convert
from rich.console import Console

from extract_articles import extract_first_page_text, parse_article, titlecase

console = Console(stderr=True)

HONORIFICS = {
    "prof", "prof.", "dr", "dr.", "mr", "mr.", "mrs", "mrs.", "ms", "ms.",
    "miss", "sir", "madam", "phd", "ph.d", "associate", "assistant",
    "professor", "dean", "lecturer", "chairman", "chairperson", "head",
    "engr", "engr.", "adv", "adv.", "syed", "sayed", "hafiz", "qari",
}

AFFILIATION_WORDS = (
    "university", "department", "institute", "college", "school", "faculty",
    "campus", "academy", "@", "orcid", "research scholar", "scholar",
    "email", "tel:", "cell:", "multan", "lahore", "islamabad", "pakistan.",
)

# Superscript author markers glued to names: "Shaheen1", "Awan2", "Gul*".
# The lookbehind requires a lowercase letter directly before the marker so that
# acronym/technical tokens keep their digits: "CO2" must not become "CO".
SUPERSCRIPT = re.compile(r"(?<=[a-z])\s*[\d*†‡§¶#]{1,3}(?=[.,;]|\s|$)")
MARKER_AFTER_NAME = re.compile(
    # Trailing \b fails when the marker is followed by punctuation ("Ayoub*,"),
    # so require whitespace/punctuation/end instead. The lowercase run after the
    # capital keeps technical tokens intact: "CO2" is not a name marker.
    r"\b[A-Z][a-z]+\s*[\d*†‡§¶#]{1,3}(?=[\s,.;:)]|$)"
)

# A title line sometimes swallows the author line because the PDF put both on
# one line, e.g. "PROBLEMS OF SMALL FIRMS OPERATING IN PAKISTAN. Prof.Dr.
# Abdul Ghafoor Awan. Dean, Faculty ...". Splitting must be self-validating:
# an author line is itself full of honorifics ("Amir Hamza1, Prof. Dr. Abdul
# Ghafoor Awan2"), so splitting blindly at "Prof." truncates it into a
# fragment that then gets absorbed into the title.
EMBEDDED_AUTHOR = re.compile(
    # Honorific after a sentence end: "... PROPHET. Prof.Dr. Abdul Ghafoor Awan."
    r"(?<=[.)])\s+(?=(?:Prof|Dr|Mr|Mrs|Ms|Miss|Engr|Adv)\.?\s*[A-Z][a-z])"
)

# Page furniture that is never the title.
BOILERPLATE = re.compile(
    r"global journal|issn|impact factor|www\.|doi\s*[:.]"
    r"|\b(?:research|original|review)\s+article\b"
    r"|open access|creative commons|all rights reserved|this is an open access"
    r"|figshare|orcid|^\s*@\s*\d{4}|^\s*\d{1,4}\s*$|^\s*[_-]{6,}\s*$"
    r"|^\s*\.{4,}\s*$|^\s*(vol\.?|volume)\s*\d+|pp\.\s*\d|https?://"
    r"|^page\s*\d+\s*$|email\s*:|editor@gjmsweb|received\s*:|paper received"
    r"|paper accepted|online pub|type of study|article history|corresponding author"
    r"|^\s*doi\b",
    re.IGNORECASE,
)

# Tolerant of typos found in the corpus: ABSTRACT, ABSTRACT-, ABATRACT-.
ABSTRACT_HEAD = re.compile(
    r"^\s*a[a-z]{0,6}(?:stract|tract|ract)\b[\s:.\-\u2013\u2014]*", re.IGNORECASE
)
ABSTRACT_TAIL = re.compile(r"^\s*(key\s*words?|keywords?)\b", re.IGNORECASE)
SECTION_START = re.compile(
    r"^\s*(?:\d{1,2}\s*[.)]?\s*[A-Z]|introduction\b|literature review\b"
    r"|research methodology\b|methodology\b|data and method|results?\b"
    r"|discussion\b|conclusions?\b|references\b|bibliography\b|appendix\b)",
    re.IGNORECASE,
)
# Arabic / Urdu / Persian script ranges.
RTL_CHARS = re.compile(r"[\u0590-\u05FF\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")

# The journals print many titles ALL CAPS while the archive is title case, so
# replacements are converted for display -- but domain acronyms must survive.
ACRONYMS = {
    "FDI", "GDP", "GNP", "CO2", "ESL", "EFL", "ELT", "GTM", "ARDL", "OLS",
    "SME", "SMEs", "NGO", "NGOs", "HRM", "HR", "CPI", "ICT", "IT", "TV",
    "USA", "UK", "UAE", "UN", "UNESCO", "WHO", "WTO", "IMF", "ADB",
    "SAARC", "COVID", "COVID-19", "SPSS", "AMOS", "MDG", "MDGs", "SDG",
    "SDGs", "R&D", "CEO", "CFO", "MBA", "MSC", "PHD", "FBR", "SBP", "PSX",
    "PKR", "USD", "GST", "FTA", "PPP", "BOT", "CIA", "HEC", "SRO",
    "SLR", "SAR", "ROE", "ROA", "EPS", "IPO", "KSE", "AI",
}
SMALL_WORDS = {
    "a", "an", "and", "the", "of", "in", "on", "at", "for", "to", "is",
    "are", "as", "by", "with", "from", "its", "or", "nor", "but", "so",
    "yet", "per", "via", "vs", "into", "over", "under", "not", "no",
}


VOWELS = frozenset("AEIOUY")


def _is_acronym(core: str) -> bool:
    """Heuristic for tokens to leave in upper case.

    A whitelist alone misses too much, and \"any short all-caps word\" wrongly
    preserves ROLE/ASIA/BANKS. Acronyms here have no vowels (FDI, GDP, SBP,
    ARDL) or contain a digit (CO2), or are whitelisted (ESL, USA, UK, IT).
    Deliberately no \"very short token\" rule: it wrongly keeps names like ABU.
    """
    if core in ACRONYMS:
        return True
    if not core.isupper():
        return False
    # Ordinals like 4TH / 1ST are not acronyms.
    if re.fullmatch(r"\d+(?:ST|ND|RD|TH)", core):
        return False
    letters = [c for c in core if c.isalpha()]
    if not letters:
        return False
    if any(c.isdigit() for c in core):
        return True
    return not (set(letters) & VOWELS)


def display_case(text: str) -> str:
    """Convert an ALL-CAPS title to title case while keeping acronyms intact.

    "IMPACT OF FDI ON GDP IN PAKISTAN" becomes "Impact of FDI on GDP in
    Pakistan" rather than "Impact of Fdi on Gdp in Pakistan".
    """
    from titlecase import titlecase as _titlecase

    words = clean_inline(text).split()
    out: list[str] = []
    for index, raw in enumerate(words):
        core = raw.strip("()[]\"',.;:!?")
        if _is_acronym(core):
            out.append(raw)
        elif re.fullmatch(r"\d+(?:ST|ND|RD|TH)", core):
            out.append(core[: -2] + core[-2:].lower())
        elif core.isupper():
            # Only normalise words that really are ALL CAPS. A correctly cased
            # title must pass through untouched: "Pakistan: The Roles" keeps
            # its capital T, while "PAKISTAN: THE ROLES" becomes "Pakistan:
            # the Roles".
            if core.casefold() in SMALL_WORDS and 0 < index < len(words) - 1:
                out.append(raw.lower())
            else:
                out.append(_titlecase(raw))
        else:
            # Already mixed case: leave the author's own rendering alone.
            out.append(raw)
    return " ".join(out)


@dataclass
class ArticleMetadata:
    """Metadata extracted from an article's first page."""

    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    affiliations: list[str] = field(default_factory=list)
    source: str = "structure"
    rtl: bool = False
    confidence: str = "high"
    notes: list[str] = field(default_factory=list)

    @property
    def authors_text(self) -> str:
        return ", ".join(self.authors)


@dataclass
class Line:
    """One logical line of page-1 text, with the font it was drawn in."""

    text: str
    size: float = 0.0
    kind: str = "paragraph"
    font: str = ""

    @property
    def is_heading_node(self) -> bool:
        return self.kind == "heading"


def clean_inline(value: str) -> str:
    """Collapse whitespace, join wrapped lines, de-hyphenate."""
    text = re.sub(r"-\n(?=[a-z])", "", value or "")
    text = re.sub(r"[\r\n\u2028\u2029]+", " ", text)
    text = text.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


HONORIFIC_SET = frozenset(h.rstrip(".").casefold() for h in HONORIFICS)


def rejoin_spaced_honorifics(text: str) -> str:
    """Rejoin honorifics/names the PDF split as "D r." / "P rof." / "H afiz".

    Without this, "D r. H afiz" contributes the lowercase fragments "r" and
    "afiz" as if they were function words, which makes looks_like_author_line
    reject a real author line. Only rejoins when the result IS a known
    honorific, so "A study" is left alone.
    """

    def repl(match: re.Match) -> str:
        first, rest, dot = match.group(1), match.group(2), match.group(3)
        if (first + rest).casefold() in HONORIFIC_SET:
            return first + rest + dot
        return match.group(0)

    return re.sub(r"\b([A-Z])\s+([A-Za-z]{1,6})(\.?)", repl, text)


def clean_line(value: str) -> str:
    """Collapse whitespace inside a single line (keeps lines separate)."""
    text = (value or "").replace("\u00a0", " ")
    return rejoin_spaced_honorifics(re.sub(r"\s+", " ", text).strip())


def walk(node, out):
    """Depth-first, document-order walk over ODL `kids` / `list items`."""
    if isinstance(node, dict):
        if node.get("type") in ("heading", "paragraph", "list item", "title"):
            out.append(node)
        for key in ("kids", "list items"):
            for child in node.get(key) or []:
                walk(child, out)
    elif isinstance(node, list):
        for item in node:
            walk(item, out)
    return out


def is_boilerplate(text: str) -> bool:
    return bool(BOILERPLATE.search(text))


def has_rtl(text: str) -> bool:
    return bool(RTL_CHARS.search(text))


def looks_like_affiliation(text: str) -> bool:
    return any(word in text.casefold() for word in AFFILIATION_WORDS)


def _font_size(node: dict) -> float:
    try:
        return float(node.get("font size") or 0)
    except (TypeError, ValueError):
        return 0.0


def _authorish(text: str) -> bool:
    """Looser author test used only to validate an embedded split.

    Deliberately ignores the sentence-ending guard, because the prefix of a
    split can end in "Prof." and is still an author line.
    """
    if not text:
        return False
    bare = [w.strip(".,;:()[]*†‡§¶") for w in text.split()]
    function_words = [
        w for w in bare if w and w[0].islower() and w.casefold() not in HONORIFICS
    ]
    if len(function_words) > 2:
        return False
    if MARKER_AFTER_NAME.search(text):
        return True
    return bool({t.casefold() for t in _tokens(text)} & HONORIFICS)


def split_embedded_author(text: str) -> list[str]:
    """Split one physical line into (title part, author part) when safe.

    Only splits when the text before the author run does NOT already look like
    an author line, so a normal author line is returned untouched.
    """
    match = EMBEDDED_AUTHOR.search(text)
    if not match or match.start() == 0:
        return [text]
    prefix = clean_line(text[: match.start()])
    suffix = clean_line(text[match.start():])
    if not prefix or not suffix:
        return [text]
    if _authorish(prefix) or not _authorish(suffix):
        return [text]
    return [prefix, suffix]


def to_lines(nodes: list[dict]) -> list[Line]:
    """Split every node into logical lines, inheriting font and order."""
    lines: list[Line] = []
    for node in nodes:
        size = _font_size(node)
        kind = str(node.get("type") or "paragraph")
        font = str(node.get("font") or "")
        for raw in (node.get("content") or "").splitlines():
            text = clean_line(raw)
            if not text:
                continue
            # A single line may still hold the title and the author run.
            for piece in split_embedded_author(text):
                if piece:
                    lines.append(Line(piece, size, kind, font))
    return lines


def _tokens(text: str) -> list[str]:
    """Split on whitespace and periods so `Prof.Dr.Abdul` yields three tokens."""
    return [t for t in re.split(r"[.\s]+", text) if t]


# A line often holds the name AND its affiliation: "Allah Nawaz M.Phil English
# Scholar, Department of English, ...". Cut at the first such marker to
# recover the bare name list.
AFFILIATION_TAIL = re.compile(
    r"\b(?:M\.?\s?Phil|M\.?\s?Sc|M\.?\s?A\b|M\.?\s?B\.?\s?A|Ph\.?\s?D|Dean"
    r"|Faculty|Department|Institute|University|College|School|Scholar|Supervisor"
    r"|Roll\s*No|Assistant|Associate|Lecturer|Chairman|Email|E-mail|Cell|Tel"
    r"|ORCID|Research\s+Scholar)\b",
    re.IGNORECASE,
)


def strip_affiliation_tail(text: str) -> str:
    """Return the part of a line before its first affiliation/degree marker."""
    match = AFFILIATION_TAIL.search(text)
    return (text[: match.start()] if match else text).strip(" ,;:-")


def looks_like_names(text: str) -> bool:
    """True when text is a bare list of personal names (not affiliation/contact)."""
    if not text or len(text) > 90:
        return False
    if "@" in text or "http" in text.casefold():
        return False
    words = text.split()
    if not 1 <= len(words) <= 10:
        return False
    # Reject list markers left behind by affiliation lines ("1.", "2.").
    if re.match(r"^\s*[\d*†‡§¶]+\s*[.)]?\s*$", text):
        return False
    for word in words:
        core = word.strip(".,;:*†‡§¶()- ")
        if not core:
            continue
        if core[0].islower():
            return False
    return bool(re.search(r"[A-Za-z]", text))


def _is_name_fragment(text: str) -> bool:
    """True for a bare name fragment, e.g. an author name the PDF wrapped.

    Sobia Suleman's author block extracts as three lines: "Muhammad" /
    "Tariq Ayoub*, Sobia Suleman**, Anees-ul Hassan***," / ... so the first
    fragment must be absorbed into the author region instead of the title.
    ALL CAPS is title style, not a name fragment.
    """
    if not text or len(text) > 40:
        return False
    if has_rtl(text) or is_boilerplate(text) or looks_like_affiliation(text):
        return False
    if text.isupper():
        return False
    words = text.split()
    if not 1 <= len(words) <= 3:
        return False
    bare = [w.strip(".,;:()[]*†‡§¶") for w in words]
    return not any(w and w[0].islower() for w in bare)


def looks_like_author_line(text: str, following: str | None = None) -> bool:
    """True when a line is a list of author names rather than prose.

    Author lines carry superscript markers ("Awan1", "Gul*") or honorifics
    ("Prof.", "Dr."), or are immediately followed by the author affiliations
    (which list items usually are).

    A bare comma-separated list of capitalised words is NOT enough on its own:
    Khadija Shaheen's title wraps as"... the Roles of / Technology Exports,
    Renewable Energy, FDI, Inflation / and Population", and a loose comma rule
    truncates the title at that second line.
    """
    if not text or len(text) > 220 or has_rtl(text):
        return False
    if is_boilerplate(text) or looks_like_affiliation(text):
        return False
    if ABSTRACT_HEAD.match(text):
        return False

    stripped = text.rstrip()
    words = stripped.split()
    if len(words) < 2 or len(words) > 18:
        return False

    # Lowercase-ness must be tested on the ORIGINAL words, not casefolded ones.
    bare = [w.strip(".,;:()[]*†‡§¶") for w in words]
    tokens = _tokens(stripped)
    function_words = [
        w for w in bare if w and w[0].islower() and w.casefold() not in HONORIFICS
    ]
    lowered = {t.casefold() for t in tokens}
    has_marker = bool(MARKER_AFTER_NAME.search(stripped))
    has_honorific = bool(lowered & HONORIFICS)

    if len(function_words) > 2:
        return False

    # A trailing period means prose ("... Operating in Pakistan.") -- but only
    # when there is no author signal, since real author lines do end with one
    # ("Prof.Dr. Abdul Ghafoor Awan.").
    if not (has_marker or has_honorific) and stripped.endswith((".", "!", "?")):
        return False

    if has_marker:
        return True
    if has_honorific:
        # An ALL-CAPS line with no mixed-case names is a title that merely
        # mentions an honorific, e.g. "ANALYTICAL STUDY OF PROF. ABU SUFIYAN'S
        # BOOK", not an author list.
        return bool(re.findall(r"\b[A-Z][a-z]{1,}", stripped))

    # No markers or honorifics: only trust it when an affiliation list follows,
    # which is what a name line is normally followed by.
    if following is None or len(words) > 8:
        return False
    capitalised = re.findall(r"\b[A-Z][a-z]{1,}", stripped)
    return (
        len(capitalised) >= 2
        and len(function_words) == 0
        and (looks_like_affiliation(following) or following.startswith(("1.", "1 ", "*")))
    )


def split_authors(line: str) -> list[str]:
    """Split an author line into individual names, dropping markers."""
    text = clean_inline(line)
    text = re.sub(r"^\s*authors?\s*:?\s*", "", text, flags=re.IGNORECASE)
    if has_rtl(text):
        return []
    parts = [p.strip() for p in re.split(r",|;|\s+and\s+|\s+&\s+", text) if p.strip()]
    if len(parts) <= 1:
        # No comma: split on superscript markers between names, e.g.
        # "Khadija Shaheen1. Prof. Dr. Abdul Ghafoor Awan2".
        parts = [
            p.strip()
            for p in re.split(r"(?<=[a-z])\s*\d{1,2}\s*[.,]?\s*(?=[A-Z])", text)
            if p.strip()
        ]
    out: list[str] = []
    for part in parts:
        name = SUPERSCRIPT.sub("", part).strip(" .,;:*-†‡§¶")
        name = re.sub(r"\s+", " ", name)
        if len(name) < 3 or not re.search(r"[A-Za-z]", name):
            continue
        if name.casefold() in HONORIFICS:
            continue
        if re.fullmatch(r"[\d\W]+", name):
            continue
        out.append(name)
    return out


def _title_candidates(
    lines: list[Line], author_index: int | None, abstract_index: int | None
) -> tuple[list[Line], bool]:
    """Return (candidate title lines, whether the title block was non-Latin).

    Candidates are the non-boilerplate, non-RTL lines before the author line
    (or before the abstract when no author line was found), stopping at the
    first affiliation line. The second value reports whether RTL text was seen
    in that region, which is how a pure-Urdu title page is recognised.
    """
    limit = author_index if author_index is not None else abstract_index
    pool = lines if limit is None else lines[:limit]
    page_has_rtl = any(has_rtl(line.text) for line in pool)

    candidates: list[Line] = []
    for line in pool:
        if is_boilerplate(line.text):
            continue
        # Bilingual pages carry the English title then the Urdu one: the
        # expected title is the Latin one.
        if has_rtl(line.text):
            continue
        if author_index is None and (
            looks_like_affiliation(line.text) or ABSTRACT_HEAD.match(line.text)
        ):
            break
        candidates.append(line)

    if author_index is None and candidates:
        # No author line was found, so the author block may be glued to the title
        # (Ismail: title at 14.04pt, then "Muhammad Ismail," / "MS/M.Phil in
        # Business Administration" at 12.0pt). Titles use the largest font on the
        # page, so cut at the first significant drop. Scoped to this path so the
        # 290-odd papers with a detected author line are untouched.
        peak = max(candidate.size for candidate in candidates)
        trimmed: list[Line] = []
        for candidate in candidates:
            if trimmed and peak and candidate.size < peak * 0.9:
                break
            trimmed.append(candidate)
        candidates = trimmed

    return candidates, page_has_rtl


def _largest_font_title(lines: list[Line]) -> str:
    """Last resort: the largest non-boilerplate, non-RTL line on the page."""
    ranked = [
        line
        for line in lines
        if not is_boilerplate(line.text)
        and not has_rtl(line.text)
        and not ABSTRACT_HEAD.match(line.text)
        and not looks_like_affiliation(line.text)
    ]
    if not ranked:
        return ""
    best = max(ranked, key=lambda line: line.size)
    return best.text if best.size > 0 else ""


def _abstract_from_lines(lines: list[Line], abstract_index: int) -> str:
    """Collect inline + following text after an abstract heading."""
    head = lines[abstract_index]
    parts: list[str] = []
    inline = ABSTRACT_HEAD.sub("", head.text).strip()
    if inline:
        parts.append(inline)

    for line in lines[abstract_index + 1:]:
        text = line.text
        if ABSTRACT_TAIL.match(text):
            break
        if is_boilerplate(text):
            # Boilerplate (e.g. the copyright block) marks the end of page 1.
            if parts:
                break
            continue
        if line.is_heading_node and not ABSTRACT_HEAD.match(text):
            break
        if parts and SECTION_START.match(text):
            break
        parts.append(text)
        if sum(len(p) for p in parts) > 4000:
            break
    return " ".join(parts).strip()


def from_nodes(nodes: list[dict]) -> ArticleMetadata | None:
    """Build metadata from OpenDataLoader nodes, or None when unusable."""
    if not nodes:
        return None
    lines = to_lines(nodes)
    if not lines:
        return None

    # A page is only "RTL" if it has no usable Latin title (pure Urdu papers).
    head = lines[:30]
    rtl_lines = sum(1 for line in head if has_rtl(line.text))
    page_rtl = bool(head) and rtl_lines / len(head) > 0.5

    def following_text(index: int) -> str | None:
        for line in lines[index + 1:]:
            if line.text:
                return line.text
        return None

    abstract_index = next(
        (i for i, line in enumerate(lines) if ABSTRACT_HEAD.match(line.text)), None
    )
    author_index = next(
        (
            i
            for i, line in enumerate(lines)
            if looks_like_author_line(line.text, following_text(i))
            and (abstract_index is None or i < abstract_index)
        ),
        None,
    )

    # Absorb preceding wrapped name fragments into the author region so they are
    # not swallowed by the title (e.g. Sobia Suleman's lone "Muhammad" line).
    # Gated on font size: the fragment must be set in the author's smaller font,
    # otherwise a genuine one-word title line such as "Analysis" is stolen.
    author_start = author_index
    if author_start is not None:
        author_size = lines[author_start].size
        title_size = max(
            (
                line.size
                for line in lines[:author_start]
                if not is_boilerplate(line.text) and not has_rtl(line.text)
            ),
            default=0.0,
        )
        while (
            author_start > 0
            and _is_name_fragment(lines[author_start - 1].text)
            and lines[author_start - 1].size <= author_size
            and lines[author_start - 1].size < title_size
        ):
            author_start -= 1

    candidates, block_has_rtl = _title_candidates(lines, author_start, abstract_index)
    if candidates:
        title, confidence = " ".join(c.text for c in candidates), "high"
    elif block_has_rtl or page_rtl:
        # The title block is not in Latin script (pure Urdu paper): there is no
        # usable title on this page, so leave it empty for the caller to keep
        # whatever the archive already has.
        title, confidence = "", "low"
    else:
        title, confidence = _largest_font_title(lines), "low"

    if ABSTRACT_HEAD.match(title) or is_boilerplate(title):
        # Never publish boilerplate as a title.
        title, confidence = "", "low"
    rtl = not title and (block_has_rtl or page_rtl)

    authors: list[str] = []
    if author_start is not None:
        # The detected author line is trusted as-is. Additional lines in the
        # author region may hold more names (this journal often prints the Dean
        # first and the lead author on the following line), but they are gated
        # because they usually carry affiliation text.
        authors = split_authors(lines[author_start].text)
        region_end = abstract_index if abstract_index is not None else len(lines)
        for line in lines[author_start + 1: region_end]:
            name_text = strip_affiliation_tail(line.text)
            if not looks_like_names(name_text):
                continue
            for name in split_authors(name_text):
                if name not in authors:
                    authors.append(name)
    if not authors and not rtl:
        # Affiliation list items sometimes double as the author line.
        for line in lines:
            if line.kind != "list item" or looks_like_affiliation(line.text):
                continue
            if re.match(r"^\s*[\d*†‡§¶]+\s*[.)]?\s*[A-Z]", line.text):
                continue
            if looks_like_author_line(line.text):
                authors = split_authors(line.text)
                if authors:
                    break

    affiliations = [
        line.text for line in lines if line.kind == "list item" and line.text
    ]

    abstract = ""
    if abstract_index is not None and not rtl:
        abstract = _abstract_from_lines(lines, abstract_index)

    meta = ArticleMetadata(
        title=title,
        authors=authors,
        abstract=abstract,
        affiliations=affiliations,
        source="structure",
        rtl=rtl,
        confidence=confidence,
    )
    if rtl:
        meta.notes.append("right-to-left page: no Latin metadata")
    return meta


def _legacy(pdf_path: Path, workdir: Path) -> ArticleMetadata:
    """Fallback for PDFs OpenDataLoader cannot give usable nodes for."""
    text = extract_first_page_text(pdf_path, workdir)
    fallback_author = pdf_path.stem.split(" paper")[0].strip()
    rtl = has_rtl(text[:2000])
    try:
        title, authors = parse_article(text, fallback_author=fallback_author)
    except Exception:  # noqa: BLE001 - never crash on a single bad PDF
        return ArticleMetadata(
            title=titlecase(fallback_author) if not rtl else "",
            authors=[] if rtl else ([fallback_author] if fallback_author else []),
            source="filename",
            rtl=rtl,
            confidence="low",
            notes=["legacy text parsing failed"],
        )
    return ArticleMetadata(
        title=titlecase(title),
        authors=[] if rtl else authors,
        abstract="",
        source="legacy",
        rtl=rtl,
        confidence="low",
        notes=["no OpenDataLoader nodes; abstract unavailable"],
    )


def extract_metadata(pdf_path: Path) -> ArticleMetadata:
    """Extract title, authors, abstract and affiliations from page 1."""
    pdf_path = Path(pdf_path)
    with tempfile.TemporaryDirectory(prefix="gjms-metadata-") as tmp:
        workdir = Path(tmp)
        try:
            convert(
                str(pdf_path),
                output_dir=str(workdir),
                format="json",
                pages="1",
                image_output="off",
                keep_line_breaks=True,
                quiet=True,
            )
            json_path = workdir / f"{pdf_path.stem}.json"
            nodes = []
            if json_path.exists():
                data = json.loads(json_path.read_text(encoding="utf-8"))
                nodes = walk(data.get("kids") or [], [])
        except Exception as error:  # noqa: BLE001 - fall back, never crash
            nodes = []
            console.print(
                f"[yellow]{pdf_path.name}: conversion failed ({error})[/yellow]"
            )

        meta = None
        if nodes:
            try:
                meta = from_nodes(nodes)
            except Exception as error:  # noqa: BLE001
                console.print(
                    f"[yellow]{pdf_path.name}: parsing failed ({error})[/yellow]"
                )

        if meta is None:
            meta = _legacy(pdf_path, workdir)
        elif not meta.title and not meta.rtl:
            # Structure gave no usable title (boilerplate only, odd reading
            # order). Keep its abstract, but recover the title from the
            # plain-text parser.
            legacy = _legacy(pdf_path, workdir)
            meta.title = legacy.title
            meta.authors = meta.authors or legacy.authors
            meta.source = f"{meta.source}+{legacy.source}"
            meta.confidence = "low"
            meta.notes.append("title recovered by legacy text parser")

    meta.title = clean_inline(meta.title)
    meta.abstract = clean_inline(meta.abstract)
    meta.authors = [a for a in (clean_inline(a) for a in meta.authors) if a]
    meta.affiliations = [
        a for a in (clean_inline(a) for a in meta.affiliations) if a
    ]

    if not meta.authors:
        meta.notes.append("no authors detected")
        if meta.confidence == "high":
            meta.confidence = "medium"
    if not meta.abstract and not meta.rtl:
        meta.notes.append("no abstract detected")
    if ABSTRACT_HEAD.match(meta.title) or is_boilerplate(meta.title):
        meta.notes.append(f"suspicious title: {meta.title[:60]!r}")
        meta.confidence = "low"
    if len(meta.title) > 400:
        meta.notes.append("title unusually long")
        meta.confidence = "low"
    return meta
