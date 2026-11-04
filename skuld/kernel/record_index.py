"""Tokens for the record retrieval index (S1, D4: bounded projection plus retrieval).

The index is an FTS5 table holding, per record node, a space-separated token document:
each run of CJK characters becomes its overlapping character bigrams (a lone character
stays as is) and every other run of letters or digits becomes one lower-cased word. A query
is the OR of an utterance's tokens ranked by FTS5's bm25, so rare shared terms dominate and
common ones cost little. Retrieval therefore touches an index, never the whole graph.
"""
from __future__ import annotations

import re

INDEXED_KINDS = frozenset({"Fact", "Preference", "Relationship", "Goal", "Commitment", "Task",
                           "Boundary", "Hypothesis", "Belief"})
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")
_WORD = re.compile(r"[^\W_]+")
MAX_QUERY_TOKENS = 64


def tokens(text):
    out = []
    for piece in _WORD.findall(str(text or "").lower()):
        pos = 0
        for m in _CJK.finditer(piece):
            if m.start() > pos:
                out.append(piece[pos:m.start()])
            run = m.group()
            out.extend([run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)])
            pos = m.end()
        if pos < len(piece):
            out.append(piece[pos:])
    return out


def record_text(content):
    """The text a record is found by: its own words, never ids or hashes."""
    for field in ("text", "quote"):
        if isinstance(content.get(field), str) and content[field].strip():
            return content[field]
    return content["value"] if isinstance(content.get("value"), str) else ""


def document(text):
    return " ".join(tokens(text))


def query(text):
    seen = list(dict.fromkeys(tokens(text)))[:MAX_QUERY_TOKENS]
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in seen)


__all__ = ["INDEXED_KINDS", "document", "query", "record_text", "tokens"]
