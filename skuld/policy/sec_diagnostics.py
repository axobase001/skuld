"""Posterior-mean estimators of the V3.4 CONSEQUENCE head.

Explicit port of the SFV-6 S_RULE estimator form (sfv1 commit 8600fc8803b6ca8e0c9ca689fe8db8ad42f5ca44,
research/sec_v6/k_arms.py sha256 823cc1eca14dfbf1ac4760e9a9f28fb58189e727e472f5f65609d3da9835d089, SRule.est / SRule.settle):
    estimate = (n0 * design_prior + sum of observed values) / (n0 + n)
with the harm side H and the cost side C kept apart.  Generalization required by FROZEN_DEFAULTS_V34 item 5 (卓冉's
correction): the harm side is conditional on the object having had an unhandled event at decision time (U), revealed after the
fact from visible information; rows whose U is not revealed never enter the harm posterior.  The cost side uses every row.
    C_hat(a | U) = H(NOOP | U) - H(a | U)        cost(a) = C(a)
See docs/sec_v34/CHANGE_NOTE_V34_S1P1_PORTS.md.
"""
N0 = 3


class ConditionalHarmCost:
    def __init__(self, harm_prior, cost_prior, n0=N0):
        # harm_prior(action) -> design harm given U; cost_prior(action) -> design cost; both in L_obs units
        self.harm_prior = harm_prior; self.cost_prior = cost_prior; self.n0 = n0
        self.harm = {}   # (context, action) -> [n, sum]   rows with revealed U = 1
        self.cost = {}   # (context, action) -> [n, sum]   every settled row

    def add(self, context, action, u, harm, cost):
        """u: True / False / None (not revealed).  harm is used only when u is True."""
        c = self.cost.setdefault((context, action), [0, 0.]); c[0] += 1; c[1] += cost
        if u is True:
            h = self.harm.setdefault((context, action), [0, 0.]); h[0] += 1; h[1] += harm

    def harm_given_u(self, context, action):
        n, s = self.harm.get((context, action), (0, 0.))
        return (self.n0 * self.harm_prior(action) + s) / (self.n0 + n), n

    def expected_cost(self, context, action):
        n, s = self.cost.get((context, action), (0, 0.))
        return (self.n0 * self.cost_prior(action) + s) / (self.n0 + n), n

    def c_hat(self, context, action, noop='WAIT'):
        h_noop, n_noop = self.harm_given_u(context, noop); h_a, n_a = self.harm_given_u(context, action)
        return h_noop - h_a, {'n_noop_given_u': n_noop, 'n_action_given_u': n_a}

    def snapshot(self):
        return {'n0': self.n0, 'harm': {repr(k): list(v) for k, v in self.harm.items()}, 'cost': {repr(k): list(v) for k, v in self.cost.items()}}
