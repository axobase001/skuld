"""S5 pre-registered analysis (PREREG_S5; GATE_S5 §4-§6). Rows are ledger lines; arms are paired by
(family, absence, seed). Every interval is a percentile bootstrap of the mean paired difference,
BOOTSTRAP_REPS resamples from seed BOOTSTRAP_SEED, so the analysis is deterministic.
- power: n = ceil(((Z_ALPHA + Z_POWER) sigma / delta)^2); episodes per arm = max(MIN_EPISODES, MIN_SEEDS x PER_SEED_MIN, n).
  CHANGE_NOTE_12 (d): sigma = sd of the paired C - B differences, delta = DELTA x mean B; n at DELTA_REPORTED is reported too.
- qualify (W0-W4): W0 (CHANGE_NOTE_10) the world is feasible: in every cell ORACLE's mean regret <=
  max(W0_H_FACTOR x ORACLE's mean over the H cells, W0_B_SHARE x B's mean in that cell). Two steps (Claude's GATE_S5 review). Step 1, the template pre-check, has every qualification
  arm and judges W1-W4; step 2 has ORACLE and the real B on the power trial's dev episodes and judges W1 alone.
  A check without its step's arms is NOT_RUN.
- analyze (H1-H6): an arm is not evaluable, and neither is any hypothesis that uses it, when a row is below TRANSPORT_MIN or
  FORMAT_ROW_MIN, or the arm's format validity (rows weighted by their calls) is below FORMAT_ARM_MIN (CHANGE_NOTE_12 (c))."""
import math
import random
import statistics

from skuld.sim import score

M_W, W4_RATIO, DELTA, DELTA_SEC, TVD_FACTOR = 0.4, 0.85, 0.20, 0.05, 1.5
DELTA_REPORTED = 0.10                 # CHANGE_NOTE_12 (d): DELTA was 0.10 until prereg-s5.3; reported alongside
MIN_EPISODES, MIN_SEEDS, PER_SEED_MIN = 60, 5, 12
Z_ALPHA, Z_POWER = 1.96, 0.84
BOOTSTRAP_REPS, BOOTSTRAP_SEED = 10_000, 0
TRANSPORT_MIN, FORMAT_ARM_MIN, FORMAT_ROW_MIN = 0.98, 0.98, 0.95     # CHANGE_NOTE_12 (c): format was 0.99 per row
COST_USD_MAX, WALL_S_MAX = 1.0, 1800
PRE_CHECK_ARMS = ("ORACLE", "TYPE_ORACLE", "COVERAGE", "A", "B")
W0_H_FACTOR, W0_B_SHARE = 3.0, 0.02   # CHANGE_NOTE_10: ORACLE near zero in every cell, H cells as the reference


def n_required(sigma, delta):
    return math.ceil(((Z_ALPHA + Z_POWER) * sigma / delta) ** 2)


def episodes_per_arm(n):
    return max(MIN_EPISODES, MIN_SEEDS * PER_SEED_MIN, n)


def paired_ci(diffs):
    """95% percentile bootstrap interval of the mean of `diffs`."""
    diffs = list(diffs)
    if not diffs:
        return (None, None)
    rng, n = random.Random(BOOTSTRAP_SEED), len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOTSTRAP_REPS))
    return (means[int(0.025 * BOOTSTRAP_REPS)], means[int(0.975 * BOOTSTRAP_REPS) - 1])


def cell(row):
    return (row["family"], row["absence"], row["seed"])


def by_arm(rows):
    out = {}
    for r in rows:
        out.setdefault(r["arm"], {})[cell(r)] = r
    return out


def paired(arms, a, b, f=lambda x, y: x - y, field="regret"):
    """Paired f(a, b) over the cells both arms have, in cell order."""
    keys = sorted(set(arms.get(a, {})) & set(arms.get(b, {})))
    return [f(arms[a][k][field], arms[b][k][field]) for k in keys]


def below_zero(diffs):
    lo, hi = paired_ci(diffs)
    return hi is not None and hi < 0, (lo, hi)


def qualify(rows):
    arms, out = by_arm(rows), {}

    def need(*names):
        return all(arms.get(n) for n in names)

    pre_check = need(*PRE_CHECK_ARMS)        # W2-W4 belong to step 1, the template pre-check with every qualification arm

    if pre_check:                            # W0 (CHANGE_NOTE_10): the world is feasible
        cells = {}
        for arm in ("ORACLE", "B"):
            for k, r in arms[arm].items():
                cells.setdefault((k[0], k[1]), {}).setdefault(arm, []).append(r["regret"])
        ref = statistics.mean(r["regret"] for k, r in arms["ORACLE"].items() if k[1] == "H")
        per = {f"{f}/{a}": {"oracle": statistics.mean(v["ORACLE"]), "b": statistics.mean(v["B"]),
                            "limit": max(W0_H_FACTOR * ref, W0_B_SHARE * statistics.mean(v["B"]))} for (f, a), v in sorted(cells.items())}
        out["W0"] = {"verdict": "PASS" if all(c["oracle"] <= c["limit"] for c in per.values()) else "FAIL", "reference_h": ref, "cells": per}
    else:
        out["W0"] = {"verdict": "NOT_RUN"}
    if need("ORACLE", "B"):
        d = paired(arms, "ORACLE", "B", lambda o, b: o - (1 - M_W) * b)
        ok, ci = below_zero(d)
        out["W1"] = {"verdict": "PASS" if ok else "FAIL", "mean_oracle": statistics.mean(r["regret"] for r in arms["ORACLE"].values()),
                     "mean_b": statistics.mean(r["regret"] for r in arms["B"].values()), "ci": ci, "pairs": len(d)}
    else:
        out["W1"] = {"verdict": "NOT_RUN"}
    if pre_check:
        d = paired(arms, "B", "A")
        ok, ci = below_zero(d)
        out["W2"] = {"verdict": "PASS" if ok else "FAIL", "mean_b_minus_a": statistics.mean(d) if d else None, "ci": ci, "pairs": len(d)}
    else:
        out["W2"] = {"verdict": "NOT_RUN"}
    if pre_check:
        keys = sorted(set(arms["ORACLE"]) & set(arms["B"]))
        adv = {lvl: [arms["B"][k]["regret"] - arms["ORACLE"][k]["regret"] for k in keys if k[1] == lvl] for lvl in ("D", "H")}
        if adv["D"] and adv["H"]:
            out["W3"] = {"verdict": "PASS" if statistics.mean(adv["D"]) > statistics.mean(adv["H"]) else "FAIL",
                         "advantage_D": statistics.mean(adv["D"]), "advantage_H": statistics.mean(adv["H"])}
        else:
            out["W3"] = {"verdict": "NOT_RUN"}
    else:
        out["W3"] = {"verdict": "NOT_RUN"}
    if pre_check:
        d = paired(arms, "TYPE_ORACLE", "COVERAGE", lambda t, c: t - W4_RATIO * c)
        ok, ci = below_zero(d)
        out["W4"] = {"verdict": "PASS" if ok else "FAIL", "ci": ci, "pairs": len(d),
                     "mean_type": statistics.mean(r["regret"] for r in arms["TYPE_ORACLE"].values()),
                     "mean_coverage": statistics.mean(r["regret"] for r in arms["COVERAGE"].values())}
    else:
        out["W4"] = {"verdict": "NOT_RUN"}
    return out


def evaluable(rows):
    out, formats = {}, {}
    for r in rows:
        ok = (r.get("transport_ok") is None or r["transport_ok"] >= TRANSPORT_MIN) and (r.get("format_ok") is None or r["format_ok"] >= FORMAT_ROW_MIN)
        out[r["arm"]] = out.get(r["arm"], True) and ok
        if r.get("format_ok") is not None:
            formats.setdefault(r["arm"], []).append((r["format_ok"], r.get("calls") or 0))
    for arm, xs in formats.items():          # the arm's rate: rows weighted by their calls (unweighted when no row has calls)
        weight = sum(c for _, c in xs)
        rate = sum(f * c for f, c in xs) / weight if weight else statistics.mean(f for f, _ in xs)
        out[arm] = out[arm] and rate >= FORMAT_ARM_MIN
    return out


def analyze(rows, dev):
    """-> {H1..H6: {"verdict", ...}, "evaluable": {arm: bool}}. dev: {"B": mean regret, "C": mean regret} of the dev runs."""
    arms, ev, out = by_arm(rows), evaluable(rows), {}
    out["evaluable"] = ev

    def usable(*names):
        if not all(arms.get(n) for n in names):
            return "NOT_RUN"
        return None if all(ev.get(n) for n in names) else "NOT_EVALUABLE"

    v = usable("A", "B", "C")
    if v:
        out["H1"] = {"verdict": v}
    else:
        (okb, cib), (oka, cia) = below_zero(paired(arms, "C", "B")), below_zero(paired(arms, "C", "A"))
        out["H1"] = {"verdict": "PASS" if okb and oka else "FAIL", "ci_c_minus_b": cib, "ci_c_minus_a": cia,
                     "mean_c_minus_b": statistics.mean(paired(arms, "C", "B")), "mean_c_minus_a": statistics.mean(paired(arms, "C", "A"))}
    v = usable("B", "C")
    if v:
        out["H2"] = {"verdict": v}
    else:
        ib = statistics.mean(r["interruption"] for r in arms["B"].values())
        ic = statistics.mean(r["interruption"] for r in arms["C"].values())
        out["H2"] = {"verdict": "PASS" if ic <= ib else "FAIL", "interruption_c": ic, "interruption_b": ib}
    v = usable("C")
    if v:
        out["H3"] = {"verdict": v}
    else:
        d = [r["brier_second"] - r["brier_first"] for r in arms["C"].values() if r.get("brier_first") is not None and r.get("brier_second") is not None]
        if d:
            ok, ci = below_zero(d)
            out["H3"] = {"verdict": "PASS" if ok else "FAIL", "ci": ci, "pairs": len(d)}
        else:
            out["H3"] = {"verdict": "NOT_RUN", "reason": "no settled forecasts in both halves"}
    v = usable("C", "C_FROZEN")
    if v:
        out["H4"] = {"verdict": v}
    else:
        ok, ci = below_zero(paired(arms, "C", "C_FROZEN"))
        out["H4"] = {"verdict": "PASS" if ok else "FAIL", "ci": ci}
    v = usable("C", "C+", "C'")
    if v:
        out["H5"] = {"verdict": v}
    else:
        keys = sorted(set(arms["C+"]) & set(arms["C"]))
        noise_keys = sorted(set(arms["C'"]) & set(arms["C"]))
        tvd_plus = statistics.mean(score.tvd(arms["C+"][k]["actions"], arms["C"][k]["actions"]) for k in keys)
        tvd_noise = statistics.mean(score.tvd(arms["C"][k]["actions"], arms["C'"][k]["actions"]) for k in noise_keys)
        d = paired(arms, "C+", "C")
        ok, ci = below_zero(d)
        effect = statistics.mean(d)
        decisions, outcome = tvd_plus > TVD_FACTOR * tvd_noise, ok and effect <= -DELTA_SEC * dev["C"]
        out["H5"] = {"verdict": "PASS" if decisions and outcome else "DIAGNOSTIC" if decisions else "FAIL", "tvd_plus": tvd_plus,
                     "tvd_noise": tvd_noise, "noise_pairs": len(noise_keys), "effect": effect, "ci": ci, "threshold": -DELTA_SEC * dev["C"]}
    costs = [r["cost_usd"] for r in rows if r["arm"] in ("A", "B", "C", "C+") and r.get("cost_usd") is not None]
    walls = [r["wall_s"] for r in rows if r["arm"] in ("A", "B", "C", "C+") and r.get("wall_s") is not None]
    out["H6"] = {"verdict": "PASS" if costs and max(costs) <= COST_USD_MAX and max(walls) <= WALL_S_MAX else ("NOT_RUN" if not costs else "FAIL"),
                 "cost_usd_max": max(costs) if costs else None, "wall_s_max": max(walls) if walls else None}
    return out


def power(rows):
    """Power check on the dev rows (CHANGE_NOTE_12 (d)): sigma_d = the sd of the paired C - B differences, delta = DELTA x
    mean B; the n at DELTA_REPORTED and the sd of C - A are reported alongside."""
    arms = by_arm(rows)
    d_cb, d_ca = paired(arms, "C", "B"), paired(arms, "C", "A")
    sigma = statistics.stdev(d_cb) if len(d_cb) > 1 else 0.0
    mean_b = statistics.mean(r["regret"] for r in arms["B"].values())
    delta, delta_reported = DELTA * mean_b, DELTA_REPORTED * mean_b
    n = n_required(sigma, delta) if delta > 0 else None
    n_reported = n_required(sigma, delta_reported) if delta_reported > 0 else None
    return {"sigma_d": sigma, "delta": delta, "mean_b": mean_b, "mean_c": statistics.mean(r["regret"] for r in arms["C"].values()),
            "mean_a": statistics.mean(r["regret"] for r in arms["A"].values()) if arms.get("A") else None, "n_required": n,
            "episodes_per_arm": episodes_per_arm(n) if n is not None else None, "pairs": len(d_cb),
            "mean_c_minus_b": statistics.mean(d_cb) if d_cb else None, "mean_c_minus_a": statistics.mean(d_ca) if d_ca else None,
            "sd_c_minus_a": statistics.stdev(d_ca) if len(d_ca) > 1 else None, "c_better_than_b_worlds": sum(d < 0 for d in d_cb),
            "reported": {"delta_share": DELTA_REPORTED, "delta": delta_reported, "n_required": n_reported,
                         "episodes_per_arm": episodes_per_arm(n_reported) if n_reported is not None else None}}


__all__ = ["n_required", "episodes_per_arm", "paired_ci", "qualify", "analyze", "power", "evaluable"]
