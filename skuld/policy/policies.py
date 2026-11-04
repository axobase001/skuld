"""D12 decision interface: ACT chooses through Policy.choose(candidates, context) -> index or None
(None = WAIT). Candidates arrive already validated and scored by the calibrator; each is a dict
with kind, score, probability (calibrated usefulness probability or None) and cites_inferred.
The level the choice may take is the ladder's business (skuld.loop.ladder), never the policy's."""

COMMIT_KINDS = ("PREPARE", "ASK", "EXECUTE")


class Policy:
    name = ""

    def choose(self, candidates, context):
        raise NotImplementedError


def _best(candidates, kinds):
    best = None
    for i, c in enumerate(candidates):
        if c["kind"] in kinds and c["score"] > 0 and (best is None or c["score"] > candidates[best]["score"]):
            best = i
    return best


class RulePolicy(Policy):
    """Verify before committing (V3.4 planner.py: on an unconfirmed object, act without checking only
    when V_now >= S_check + MARGIN; this rule version keeps that core): when the best commit
    candidate's calibrated probability is below verify_below, or it cites a system-inferred record,
    and a read-only check is on offer, check first. An unscored probability (None) is judged by the
    inferred-record test alone."""
    name = "rule"

    def __init__(self, verify_below=0.5):
        self.verify_below = verify_below

    def choose(self, candidates, context):
        commit, check = _best(candidates, COMMIT_KINDS), _best(candidates, ("INVESTIGATE",))
        if commit is not None:
            c = candidates[commit]
            unconfirmed = c.get("cites_inferred") or (c.get("probability") is not None and c["probability"] < self.verify_below)
            return check if unconfirmed and check is not None else commit
        return check


class ReactiveNull(Policy):
    """Baseline: no proactive action at all (its REASON makes the PREDICT call only)."""
    name = "reactive_null"

    def choose(self, candidates, context):
        return None


POLICIES = {RulePolicy.name: RulePolicy, ReactiveNull.name: ReactiveNull}

__all__ = ["Policy", "RulePolicy", "ReactiveNull", "POLICIES", "COMMIT_KINDS"]
