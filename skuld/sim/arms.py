"""S5 arms (GATE_S5 §3; ARM_B_PROPOSAL v2): same runtime, same model, same budget, same tau and mandates.
- A ReactiveNull: memory and conversation, no proactive action.
- B proactive-memory: RulePolicy like C, reminders at due - 24 h / - 2 h, change-triggered preparation;
  no predictive modelling (no forecasts or inferences, SELECT in fixed rotation), no calibration learning, no SEC.
- C: the full V4.0 loop, SEC off (the learner module is not imported).
- C+: C with the SEC post-hoc learner on.
No V34Policy arm in the first S5 (Claude's review)."""

ARMS = ("A", "B", "C", "C+")
CONFIG = {
    "A": {"policy": "reactive_null", "sec_learner": False, "predictive_modeling": True, "reminders": False, "change_prepare": False},
    "B": {"policy": "rule", "sec_learner": False, "predictive_modeling": False, "reminders": True, "change_prepare": True},
    "C": {"policy": "rule", "sec_learner": False, "predictive_modeling": True, "reminders": False, "change_prepare": False},
    "C+": {"policy": "rule", "sec_learner": True, "predictive_modeling": True, "reminders": False, "change_prepare": False},
}
REMINDER_HOURS = [24, 2]


def config(arm):
    return dict(CONFIG[arm])


def runtime_config(arm):
    """The runtime config keys that make an arm (skuld.app.loop_runtime / product_loop / calibration switches)."""
    c, out = CONFIG[arm], {"policy": CONFIG[arm]["policy"]}
    if c["sec_learner"]:
        out["sec_learner"] = True
    if not c["predictive_modeling"]:
        out.update(predictive_modeling=False, calibration_learning=False)
    if c["reminders"]:
        out["reminder_hours"] = list(REMINDER_HOURS)
    return out


__all__ = ["ARMS", "config", "runtime_config"]
