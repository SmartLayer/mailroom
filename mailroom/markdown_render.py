"""Auto-render plain-text email body to HTML when it contains markdown structure.

Used by :func:`mailroom.smtp_client.create_mime` to decide whether a
``text/plain``-only message should be upgraded to ``multipart/alternative``
with an HTML part. The trigger is narrow: only markdown tables and ATX
headings, since those are the structures that collapse in proportional-font
mail clients. Other markdown (bullets, links, emphasis, code blocks) reads
fine as plain text and is not a trigger.
"""

import re

from markdown_it import MarkdownIt

# A single ATX heading line (``# title`` through ``###### title``). The
# trailing ``\s`` distinguishes a heading from a hashtag (``#tag``).
_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)

# A markdown table: a pipe row immediately followed by a separator row.
# The separator is what disambiguates a real table from prose containing
# pipes. False positives inside fenced code blocks are accepted as the
# price of a parser-free trigger.
_TABLE_RE = re.compile(
    r"^\|.+\|[ \t]*\r?\n\|[\s:|-]+\|[ \t]*$",
    re.MULTILINE,
)

# CommonMark base plus GFM tables. We do not enable linkify (which would
# pull in optional linkify-it-py), since mail clients turn bare URLs into
# links on display regardless of the source HTML.
_renderer = MarkdownIt("commonmark").enable("table")


def needs_html(body: str) -> bool:
    """Return True if *body* contains a markdown table or ATX heading."""
    return bool(_HEADING_RE.search(body) or _TABLE_RE.search(body))


def render_html(body: str) -> str:
    """Render *body* as an HTML fragment using a GFM-flavoured markdown parser.

    Returns:
        The HTML fragment from ``markdown-it-py`` with no surrounding
        ``<html>``/``<body>`` wrapper and no inline styles. Tables are
        emitted with the legacy ``<table border="1">`` attribute so
        that mail clients without default table-cell borders still show
        column boundaries; everything else relies on client defaults.
    """
    return _renderer.render(body).replace("<table>", '<table border="1">')
