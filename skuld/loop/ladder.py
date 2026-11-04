"""D6 action ladder with D7(b): the level a chosen candidate may take. A host function of the
candidate's kind, the operation's declared risk class, the user's tau and whether a live mandate
covers the operation; nothing learned enters it, so neither calibration nor SEC can raise a level.

0 nothing, 1 observe / read-only check, 2 prepare a private artifact, 3 propose -> exact one-time
approval -> execute, 4 execute under a mandate and report afterwards."""
import math

KIND_LEVEL = {"WAIT": 0, "INVESTIGATE": 1, "PREPARE": 2, "ASK": 2}


def risk_ceiling(tau):
    """Highest risk class tau lets run without asking (D7(b)): floor(3 tau)."""
    return math.floor(3 * tau + 1e-9)


def level(kind, risk_class, tau, mandated):
    if tau <= 0 or kind == "WAIT":
        return 0
    if kind != "EXECUTE":
        return KIND_LEVEL[kind]
    return 4 if mandated and risk_class <= risk_ceiling(tau) else 3


__all__ = ["level", "risk_ceiling", "KIND_LEVEL"]
