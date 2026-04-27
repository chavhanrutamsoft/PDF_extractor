from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pdf_table_pipeline.config import ExtractionConfig


def normalize_text(s: str) -> str:
    """Lowercase, NFKC, collapse whitespace for matching."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def keyword_hits_in_text(normalized_text: str, keywords: list[str]) -> list[str]:
    """Return list of original keywords that appear as substrings in normalized_text."""
    n = normalize_text(normalized_text)
    matched: list[str] = []
    for kw in keywords:
        if normalize_text(kw) in n:
            matched.append(kw)
    return matched


def regex_hits_in_text(text: str, patterns: list[str]) -> list[str]:
    """Return pattern strings that matched anywhere in text."""
    hits: list[str] = []
    for pat in patterns:
        try:
            if re.search(pat, text, re.I):
                hits.append(pat)
        except re.error:
            continue
    return hits


def page_keyword_score(page_text: str, config: ExtractionConfig) -> tuple[int, list[str]]:
    """Cheap page-level score: count distinct keyword + regex hits."""
    n = normalize_text(page_text)
    matched_kw = keyword_hits_in_text(n, config.keywords)
    matched_re = regex_hits_in_text(page_text, config.regex_patterns)
    # distinct labels for logging
    all_hits = list(dict.fromkeys(matched_kw + matched_re))
    score = len(all_hits)
    return score, all_hits


def _header_blob(rows: list[list[str | None]], header_row_count: int) -> str:
    parts: list[str] = []
    for row in rows[:header_row_count]:
        parts.extend(str(c or "") for c in row)
    return " ".join(parts)


def _full_table_blob(rows: list[list[str | None]]) -> str:
    parts: list[str] = []
    for row in rows:
        parts.extend(str(c or "") for c in row)
    return " ".join(parts)


def table_keyword_match(
    rows: list[list[str | None]],
    config: ExtractionConfig,
) -> tuple[bool, list[str], str]:
    """
    Decide if table passes keyword filter.

    Returns (accepted, keywords_matched, explanation_snippet).
    """
    if not rows:
        return False, [], "empty"

    header_blob = _header_blob(rows, config.header_row_count)
    full_blob = _full_table_blob(rows)
    n_header = normalize_text(header_blob)
    n_full = normalize_text(full_blob)

    kw_header = keyword_hits_in_text(n_header, config.keywords)
    kw_full = keyword_hits_in_text(n_full, config.keywords)
    re_header = regex_hits_in_text(header_blob, config.regex_patterns)
    re_full = regex_hits_in_text(full_blob, config.regex_patterns)

    mode = config.match_mode
    min_hits = config.min_keyword_hits

    def uniq(xs: list[str]) -> list[str]:
        return list(dict.fromkeys(xs))

    if mode == "header_any":
        hits = uniq(kw_header + re_header)
        ok = len(hits) >= min_hits
        return ok, hits, "header_any"
    if mode == "header_all":
        if not config.keywords and not config.regex_patterns:
            return True, [], "header_all_no_constraints"
        kw_ok = all(normalize_text(k) in n_header for k in config.keywords) if config.keywords else True
        re_ok = all(bool(re.search(p, header_blob, re.I)) for p in config.regex_patterns) if config.regex_patterns else True
        hits = uniq(kw_header + re_header)
        ok = kw_ok and re_ok and len(hits) >= min_hits
        return ok, hits, "header_all"
    if mode == "any":
        hits = uniq(kw_full + re_full)
        ok = len(hits) >= min_hits
        return ok, hits, "any"
    if mode == "all":
        kw_ok = all(normalize_text(k) in n_full for k in config.keywords) if config.keywords else True
        re_ok = all(re.search(p, full_blob, re.I) for p in config.regex_patterns) if config.regex_patterns else True
        hits = uniq(kw_full + re_full)
        ok = kw_ok and re_ok
        return ok, hits, "all"

    return False, [], "unknown_mode"
