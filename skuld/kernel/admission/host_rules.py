"""Host-side admission rules for conversation statements (scope §4 S1, GATE_S1).

Whatever the model proposes, the host admits a statement only if its quote is an exact
substring of the utterance, it does not come from a question clause, and a fact-like
statement stored as actual or intended does not come from a hedged clause. Conditional
markers hedge their whole sentence (antecedent and consequent alike); uncertainty markers
hedge only their own clause, the same unit as questions. Uncertain or hypothetical
statements may enter the graph as such. tests/s1/admission_rules.py freezes the same rules.
"""
from __future__ import annotations

import re

FACT_LIKE = frozenset({"Fact", "Commitment", "Goal", "Task", "Boundary"})
ASSERTED_MODALS = frozenset({"ACTUAL", "INTENDED"})
CLAUSE_END = "，,。.!?！？;；\n"
SENTENCE_END = "。.!?！？;；\n"
QUESTION_PARTICLES = ("吗", "么")
CONDITIONALS_ZH = ("如果", "假如", "要是", "万一", "假设", "倘若")
CONDITIONALS_EN = re.compile(r"\bif\b", re.IGNORECASE)
UNCERTAIN_ZH = ("可能", "也许", "大概", "或许", "说不定", "不确定")
UNCERTAIN_EN = re.compile(r"\b(maybe|perhaps|might|probably)\b", re.IGNORECASE)


def _spans(text, ends):
    out, start = [], 0
    for i, ch in enumerate(text):
        if ch in ends:
            out.append((start, i + 1))
            start = i + 1
    if start < len(text):
        out.append((start, len(text)))
    return out


def _occurrences(text, quote):
    i = text.find(quote)
    while i >= 0:
        yield i, i + len(quote)
        i = text.find(quote, i + 1)


def _covered(text, spans, a, b):
    return [text[s:e] for s, e in spans if s < b and e > a]


def _question(clause):
    body = clause.rstrip()
    if body.endswith(("?", "？")):
        return True
    return body.rstrip(CLAUSE_END + " ").endswith(QUESTION_PARTICLES)


def _conditional(text):
    return any(m in text for m in CONDITIONALS_ZH) or bool(CONDITIONALS_EN.search(text))


def _uncertain(text):
    return any(m in text for m in UNCERTAIN_ZH) or bool(UNCERTAIN_EN.search(text))


def quote_in_utterance(utterance, quote):
    return isinstance(quote, str) and bool(quote.strip()) and quote in utterance


def quote_is_question(utterance, quote):
    clauses = _spans(utterance, CLAUSE_END)
    return all(any(_question(c) for c in _covered(utterance, clauses, a, b)) for a, b in _occurrences(utterance, quote))


def quote_is_hedged(utterance, quote):
    sentences, clauses = _spans(utterance, SENTENCE_END), _spans(utterance, CLAUSE_END)
    found = False
    for a, b in _occurrences(utterance, quote):
        found = True
        if not (any(_conditional(s) for s in _covered(utterance, sentences, a, b))
                or any(_uncertain(c) for c in _covered(utterance, clauses, a, b))):
            return False
    return found


def violations(utterance, kind, quote, modal):
    """-> list of rule names the statement breaks (empty = admissible). `modal` may be the
    contract modality or the stored modal state; case does not matter."""
    if not quote_in_utterance(utterance, quote):
        return ["QUOTE_NOT_IN_UTTERANCE"]
    out = []
    if quote_is_question(utterance, quote):
        out.append("QUESTION_ADMITTED")
    if kind in FACT_LIKE and str(modal).upper() in ASSERTED_MODALS and quote_is_hedged(utterance, quote):
        out.append("HEDGED_FACT_ADMITTED")
    return out


__all__ = ["FACT_LIKE", "quote_in_utterance", "quote_is_hedged", "quote_is_question", "violations"]
