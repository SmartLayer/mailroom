"""Gmail-style query parser for IMAP search.

Parses queries like ``from:alice subject:invoice is:unread after:2025-03-01``
into imapclient-compatible search criteria.

Supported syntax:

    Prefixes:   from: to: cc: subject: body:
    Flags:      is:unread  is:read  is:flagged  is:starred  is:answered ...
    Dates:      after:YYYY-MM-DD  before:YYYY-MM-DD  on:YYYY-MM-DD
    Relative:   newer:3d  older:7d  newer:2w  older:1m
    Bare words: searched as TEXT
    Boolean:    or (between terms), not / - (prefix negation)
    Escape:     imap:RAW IMAP EXPRESSION
    Keywords:   all  today  yesterday  week  month
"""

import logging
import re
import shlex
from datetime import date, datetime, timedelta
from typing import List, Union

logger = logging.getLogger(__name__)

# Prefixes that map directly to IMAP search keys (prefix → IMAP key).
_PREFIX_MAP = {
    "from": "FROM",
    "to": "TO",
    "cc": "CC",
    "subject": "SUBJECT",
    "body": "BODY",
}

# is:keyword → IMAP flag string.
_IS_MAP = {
    "unread": "UNSEEN",
    "read": "SEEN",
    "flagged": "FLAGGED",
    "starred": "FLAGGED",
    "unflagged": "UNFLAGGED",
    "unstarred": "UNFLAGGED",
    "answered": "ANSWERED",
    "unanswered": "UNANSWERED",
}

# Single-word queries that have special meaning.
_STANDALONE_KEYWORDS = {
    "all": lambda: "ALL",
    "today": lambda: ["SINCE", date.today()],
    "yesterday": lambda: [
        "SINCE",
        (datetime.now() - timedelta(days=1)).date(),
        "BEFORE",
        date.today(),
    ],
    "week": lambda: ["SINCE", (datetime.now() - timedelta(days=7)).date()],
    "month": lambda: ["SINCE", (datetime.now() - timedelta(days=30)).date()],
}

_RELATIVE_DATE_RE = re.compile(r"^(\d+)([dwm])$")


def parse_query(query: str) -> Union[str, List]:
    """Parse a Gmail-style query string into imapclient-compatible criteria.

    Args:
        query: The search query. Examples:
            ``"from:alice subject:invoice"``
            ``"is:unread after:2025-03-01"``
            ``"meeting notes"`` (bare words → TEXT search)
            ``"imap:OR TEXT foo SUBJECT bar"`` (raw IMAP passthrough)

    Returns:
        A string (e.g. ``"ALL"``, ``"UNSEEN"``) or a list
        (e.g. ``["FROM", "alice", "SUBJECT", "invoice"]``) suitable for
        ``imapclient.IMAPClient.search()``.

    Raises:
        ValueError: On malformed queries (dangling ``or``/``not``, bad dates,
            unknown ``is:`` keywords).
    """
    stripped = query.strip()
    if not stripped:
        return "ALL"

    # imap: escape hatch — pass through raw IMAP expression.
    if stripped.lower().startswith("imap:"):
        return _parse_raw_imap(stripped[5:])

    # Standalone keyword (entire query is one word).
    if stripped.lower() in _STANDALONE_KEYWORDS:
        result = _STANDALONE_KEYWORDS[stripped.lower()]()
        if isinstance(result, str):
            return result
        return list(result)

    tokens = _tokenize(stripped)
    return _build_criteria(tokens)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _parse_raw_imap(raw: str) -> Union[str, List]:
    """Tokenize a raw IMAP search expression."""
    try:
        tokens = shlex.split(raw)
    except ValueError:
        logger.warning("shlex failed on raw IMAP query, falling back to split")
        tokens = raw.split()
    if len(tokens) == 1:
        return tokens[0]
    return tokens


def _tokenize(query: str) -> List[str]:
    """Split query respecting quotes, preserving prefix:value as one token."""
    try:
        return shlex.split(query)
    except ValueError:
        logger.warning("shlex failed, falling back to simple split")
        return query.split()


def _parse_date(value: str) -> date:
    """Parse YYYY-MM-DD or YYYY/MM/DD into a date object."""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date format: {value!r}. Use YYYY-MM-DD or YYYY/MM/DD.")


def _parse_relative_date(value: str) -> date:
    """Parse relative offset like 3d, 2w, 1m into a date."""
    m = _RELATIVE_DATE_RE.match(value)
    if not m:
        raise ValueError(
            f"Invalid relative date: {value!r}. Use <number><d|w|m> (e.g. 3d, 2w, 1m)."
        )
    n, unit = int(m.group(1)), m.group(2)
    if unit == "d":
        delta = timedelta(days=n)
    elif unit == "w":
        delta = timedelta(weeks=n)
    else:  # "m"
        delta = timedelta(days=n * 30)
    return (datetime.now() - delta).date()


def _expand_term(token: str) -> List:
    """Expand a single token into its IMAP criteria components.

    Returns a list of IMAP criteria items.  For flag-only results
    (e.g. ``is:unread`` → ``UNSEEN``) returns a single-element list.
    """
    # Negation with dash prefix: -from:alice
    if token.startswith("-") and ":" in token[1:]:
        inner = _expand_term(token[1:])
        return ["NOT"] + inner

    if ":" not in token:
        # Bare word — will be collected by the caller.
        return []

    prefix, value = token.split(":", 1)
    prefix_lower = prefix.lower()

    # Direct prefix mapping (from, to, cc, subject, body).
    if prefix_lower in _PREFIX_MAP:
        return [_PREFIX_MAP[prefix_lower], value]

    # is: flag keywords.
    if prefix_lower == "is":
        val_lower = value.lower()
        if val_lower not in _IS_MAP:
            raise ValueError(
                f"Unknown is: keyword: {value!r}. "
                f"Valid: {', '.join(sorted(_IS_MAP))}"
            )
        return [_IS_MAP[val_lower]]

    # Date operators.
    if prefix_lower == "after":
        return ["SINCE", _parse_date(value)]
    if prefix_lower == "before":
        return ["BEFORE", _parse_date(value)]
    if prefix_lower == "on":
        return ["ON", _parse_date(value)]

    # Relative date operators.
    if prefix_lower in ("newer", "newer_than"):
        return ["SINCE", _parse_relative_date(value)]
    if prefix_lower in ("older", "older_than"):
        return ["BEFORE", _parse_relative_date(value)]

    # Unknown prefix — treat the whole token as a bare word.
    return []


def _build_criteria(tokens: List[str]) -> Union[str, List]:
    """Walk tokens and assemble a flat IMAP criteria list.

    Handles ``or``, ``not``, prefix:value terms, and bare words.
    """
    # Phase 1: classify each token into a "clause" (an IMAP criteria fragment).
    clauses: List[Union[str, List]] = []  # each entry is "OR" or a list of IMAP items
    bare_words: List[str] = []
    i = 0

    def _flush_bare_words() -> None:
        if bare_words:
            clauses.append(["TEXT", " ".join(bare_words)])
            bare_words.clear()

    while i < len(tokens):
        tok = tokens[i]
        tok_lower = tok.lower()

        if tok_lower == "or":
            _flush_bare_words()
            clauses.append("OR")
            i += 1
            continue

        if tok_lower == "not":
            _flush_bare_words()
            # Next token must exist.
            if i + 1 >= len(tokens):
                raise ValueError("'not' at end of query with nothing to negate.")
            next_tok = tokens[i + 1]
            expanded = _expand_term(next_tok)
            if not expanded:
                # Bare word after not.
                clauses.append(["NOT", "TEXT", next_tok])
            else:
                clauses.append(["NOT"] + expanded)
            i += 2
            continue

        # Regular token.
        expanded = _expand_term(tok)
        if expanded:
            _flush_bare_words()
            clauses.append(expanded)
        else:
            # Bare word (or unknown prefix treated as bare word).
            bare_words.append(tok)
        i += 1

    _flush_bare_words()

    if not clauses:
        return "ALL"

    # Phase 2: resolve OR operators.
    # OR binds two adjacent clauses in Polish notation: OR <left> <right>.
    # Chained ORs right-associate: a or b or c → OR a OR b c.
    result = _resolve_or(clauses)

    # Flatten single-element results.
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], str):
        return result[0]
    return result


def _resolve_or(clauses: List) -> Union[str, List]:
    """Resolve OR markers in the clause list into IMAP Polish notation."""
    # Validate: OR must not be first, last, or consecutive.
    if not clauses:
        return "ALL"

    if clauses[0] == "OR":
        raise ValueError("Query cannot start with 'or'.")
    if clauses[-1] == "OR":
        raise ValueError("'or' at end of query with no right operand.")
    for j in range(len(clauses) - 1):
        if clauses[j] == "OR" and clauses[j + 1] == "OR":
            raise ValueError("Consecutive 'or' operators.")

    # Split into groups separated by OR.
    groups: List[List] = [[]]
    for c in clauses:
        if c == "OR":
            groups.append([])
        else:
            groups[-1].append(c)

    if len(groups) == 1:
        # No OR — just flatten all clauses.
        return _flatten(groups[0])

    # Right-associate: OR g[0] OR g[1] ... g[n]
    # Start from the right.
    right = _flatten(groups[-1])
    for g in reversed(groups[:-1]):
        left = _flatten(g)
        right = ["OR"] + left + right
    return right


def _flatten(clause_list: List[List]) -> List:
    """Flatten a list of clause lists into a single IMAP criteria list."""
    result = []
    for clause in clause_list:
        if isinstance(clause, list):
            result.extend(clause)
        else:
            result.append(clause)
    return result
