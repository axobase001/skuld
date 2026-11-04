"""Persistent, goal-scoped proactive loop on the existing subject graph.

No second fact store, semantic parser, model route, action grant, or oracle exists
here. Snapshots are projections of admitted state. Usefulness calibration is a
small observational online learner, not causal credit or proof of improvement.
"""
from __future__ import annotations

from copy import deepcopy
import math

from skuld.kernel.schemas import AuthorityClass, ModalState, canonical_hash
from skuld.kernel.primitives import instant


class ProactiveWorldModel:
    VERSION = 'proactive-world-loop/2'
    KINDS = ('PREPARE', 'ASK', 'INVESTIGATE', 'WAIT', 'EXECUTE')
    PRIOR_STRENGTH = 4.0  # identity-calibration prior; damp updates, not a calibrated confidence interval
    MAX_LOG_ODDS_SHIFT = 1.0

    def __init__(self, runtime):
        self.j = runtime

    def _source(self, user_id, source):
        if source.user_id != user_id or self.j.graph.get_source_event(user_id, source.source_event_id) != source:
            raise ValueError('persisted same-user source required')

    def observe(self, user_id, episode_id, source, state):
        """Freeze a typed projection and its delta before asking for a decision."""
        self._source(user_id, source)
        if not episode_id or state.user_id != user_id:
            raise ValueError('same-user state and episode required')
        rows = []
        for category in ('facts', 'goals', 'closed_goals', 'preferences', 'relationships', 'boundaries', 'hypotheses', 'predictions'):
            for row in getattr(state, category):
                node = self.j.graph.get_node(user_id, row['node_id'])
                if node is None or node.content_hash != row['content_hash'] or node.content != row['content']:
                    raise ValueError('snapshot needs actual admitted graph records')
                epistemic = ('PREDICTION' if category == 'predictions' else
                             'HYPOTHESIS' if category == 'hypotheses' else
                             'COMMITMENT' if category in ('goals', 'closed_goals') else
                             'SOURCE_REPORT' if row['content'].get('epistemic_status') == 'SOURCE_REPORT' else
                             'ADMITTED_RECORD')
                identity = [row['node_type'], row['semantic_identity_key']]
                if row.get('projection') == 'FUTURE_SOURCE_REPORT':
                    identity += ['FUTURE_SOURCE_REPORT', row['valid_from']]
                rows.append({'identity': identity,
                    'node_id': row['node_id'], 'content_hash': row['content_hash'],
                    'category': category, 'epistemic_kind': epistemic,
                    'content': deepcopy(row['content']), 'source_event_id': row['source_event_id'],
                    'known_at': row['created_at'], 'effective_at': row['valid_from'],
                    'is_permission': False})
                if category == 'predictions':
                    settled = [s for s in self.j.nodes(user_id, 'ForecastSettlement')
                               if s.content.get('prediction_id') == node.node_id and s.content.get('status') == 'RESOLVED'
                               and instant(s.created_at) <= instant(state.known_at)]
                    result = min(settled, key=lambda s: (s.created_at, s.node_id), default=None)
                    rows[-1]['resolution'] = ({'status': 'OBSERVED', 'settlement_id': result.node_id,
                        'outcome': result.content['outcome'], 'observation_id': result.content['observation_id'],
                        'known_at': result.content['known_at']} if result else {'status': 'UNKNOWN', 'outcome': None})
        if any(instant(row['known_at']) > instant(source.received_at) for row in rows):
            raise ValueError('world snapshot cannot predate its evidence')
        rows.sort(key=lambda row: row['identity'])
        state_hash = canonical_hash({'records': rows, 'unknowns': state.missing_evidence})
        previous = [n for n in self.j.nodes(user_id, 'ProactiveWorldState')]
        existing = next((n for n in previous if n.semantic_identity_key == episode_id), None)
        if existing:
            if existing.content['state_hash'] != state_hash:
                raise ValueError('episode state already frozen with different evidence')
            return existing
        if any(instant(n.created_at) > instant(source.received_at) for n in previous):
            raise ValueError('world snapshot knowledge time cannot move backwards')
        prior = max((n for n in previous if instant(n.created_at) <= instant(source.received_at)),
                    key=lambda n: (n.content['sequence'], n.created_at, n.node_id), default=None)
        old = {tuple(r['identity']): r for r in prior.content['records']} if prior else {}
        new = {tuple(r['identity']): r for r in rows}
        changes = []
        for key in sorted(old.keys() | new.keys()):
            before, after = old.get(key), new.get(key)
            if before and after and before['content_hash'] == after['content_hash'] and before['category'] == after['category'] and before.get('resolution') == after.get('resolution'):
                continue
            changes.append({'kind': 'ADDED' if before is None else 'NO_LONGER_CURRENT' if after is None else 'UPDATED',
                            'before': before, 'after': after})
        with self.j.store.transaction():
            snapshot = self.j.node(user_id, 'ProactiveWorldState', episode_id,
                {'schema': self.VERSION, 'episode_id': episode_id, 'state_hash': state_hash,
                 'sequence': max((n.content['sequence'] for n in previous), default=0) + 1,
                 'previous_state_id': prior.node_id if prior else None,
                 'effective_at': state.effective_at, 'known_at': state.known_at,
                 'records': rows, 'unknowns': list(state.missing_evidence), 'delta': changes,
                 'permissions_inferred': False}, source)
            if prior:
                self.j.edge(user_id, 'CONTINUES_WORLD_STATE', snapshot, prior, source)
            return snapshot

    def context(self, user_id, snapshot):
        """Current processing consent applies even to historical delta entries."""
        if snapshot.user_id != user_id or snapshot.node_type != 'ProactiveWorldState':
            raise ValueError('same-user world snapshot required')
        def visible(row):
            if row is None:
                return None
            node = self.j.graph.get_node(user_id, row['node_id'])
            if not node or not self.j.provider_eligible(user_id, node):
                return None
            basis = node.content.get('belief_id') if node.node_type == 'Prediction' else None
            if basis:
                evidence = self.j.graph.get_node(user_id, basis)
                if not evidence or not self.j.provider_eligible(user_id, evidence):
                    return None
            visible_row = deepcopy(row)
            resolution = visible_row.get('resolution', {})
            if resolution.get('observation_id'):
                evidence = self.j.graph.get_node(user_id, resolution['observation_id'])
                if not evidence or not self.j.provider_eligible(user_id, evidence):
                    visible_row['resolution'] = {'status': 'UNKNOWN', 'outcome': None, 'redacted': True}
            return visible_row
        rows = [item for row in snapshot.content['records'] if (item := visible(row)) is not None]
        delta = []
        for change in snapshot.content['delta']:
            before, after = visible(change['before']), visible(change['after'])
            if before is not None or after is not None:
                # Redaction must not falsely assert a fact disappeared or appeared.
                delta.append({'kind': change['kind'], 'before': before, 'after': after,
                              'redacted': (change['before'] is not None and before is None) or
                                          (change['after'] is not None and after is None)})
        unknowns = list(snapshot.content['unknowns'])
        for row in rows:
            if row['epistemic_kind'] == 'HYPOTHESIS':
                unknowns.extend(row['content'].get('missing_evidence', []))
        return {'schema': self.VERSION, 'episode_id': snapshot.content['episode_id'],
                'state_id': snapshot.node_id, 'previous_state_id': snapshot.content['previous_state_id'],
                'delta': delta, 'records': rows, 'unknowns': sorted(set(unknowns)),
                'unknowns_are_not_false': True, 'permissions_inferred': False}

    def resource_risks(self, user_id, context, board):
        """Consume scoped executable hypotheses from an actual world projection, not a side pool."""
        from skuld.learning.sec_evo import features,leaf
        if self.j.config.get('cp22'):
            snaps=context['resource_recall']
            return {'risks':{k:v['p_final'] for k,v in snaps.items()},'disagreement':{k:max(v['candidate_votes'].values(),default=0)-min(v['candidate_votes'].values(),default=0) for k,v in snaps.items()},'candidate_votes':{k:v['candidate_votes'] for k,v in snaps.items()},'hypothesis_node_ids':sorted({t['model_node_id'] for v in snaps.values() for t in v['contributors']}),'model_versions':sorted({t['version'] for v in snaps.values() for t in v['contributors']},key=str),'activation_snapshots':snaps,'world_state_id':context['state_id']}
        models=[row for row in context['records'] if row['category']=='hypotheses' and row['content'].get('kind')=='SUPPLIER_EXPERIENCE_MODEL' and row['content']['scope']['source_id']==self.j.config['subject_resource_channel']]
        active=[row for row in models if row['content']['status']=='ACTIVE']
        if not active:raise ValueError('WORLD_MODEL_HAS_NO_ACTIVE_SUPPLIER_HYPOTHESIS')
        z=sum(row['content']['weight'] for row in active)
        if abs(z-1)>1e-8:raise ValueError('WORLD_MODEL_WEIGHT_MISMATCH')
        prior=(1+sum(t['supplier_result']=='delayed' for t in board['results']))/(2+len(board['results']))
        risks={};disagreement={};votes={}
        for task in board['open_responsibilities']+board['future_commitments']:
            oid=task['object_id'];x=features(task);ps={}
            for row in models:
                h=row['content']
                if h['status'] not in ('ACTIVE','PROBATION'):continue
                a,b=h['counts'].get(leaf(h['tree'],x),[1,1]);ps[h['candidate_id']]=a/(a+b) if len(x)==3 else prior
            chosen=context.get('resource_recall',{}).get(oid,{}).get('activated_candidate_ids')
            applicable=[row for row in active if not chosen or row['content']['candidate_id'] in chosen]
            norm=sum(row['content']['weight'] for row in applicable)
            risks[oid]=sum(row['content']['weight']*ps[row['content']['candidate_id']] for row in applicable)/norm
            disagreement[oid]=max(ps.values())-min(ps.values());votes[oid]=ps
        return {'risks':risks,'disagreement':disagreement,'candidate_votes':votes,'hypothesis_node_ids':[row['node_id'] for row in models],'model_versions':sorted(set(row['content']['publication_version'] for row in models)),'world_state_id':context['state_id']}

    def feedback_origin(self, user_id):
        simulated = self.j.config.get('simulation_only', True) or self.j.owner(user_id).content.get('simulation', True)
        return 'SIMULATION_FIXTURE' if simulated else 'AUTHENTICATED_OWNER'

    def scope(self, user_id, kind, goal_ids, decision_basis, risk_class=0):
        """D7 AGENCY_CALIBRATION bucket: (kind, risk_class) of one user and feedback origin. The goals
        are still checked to be the user's own, but they no longer split the bucket (S3)."""
        if kind not in self.KINDS:
            raise ValueError('unknown bounded candidate kind')
        goals = []
        for goal_id in goal_ids:
            node = self.j.graph.get_node(user_id, goal_id)
            if not node or node.node_type not in ('Goal', 'Task', 'Commitment') or node.authority_class != AuthorityClass.USER_FIRST_PERSON:
                raise ValueError('explicit same-user goals required')
            goals.append([node.node_type, node.semantic_identity_key])
        return {'user_id': user_id, 'kind': kind, 'risk_class': risk_class,
                'feedback_origin': self.feedback_origin(user_id), 'decision_basis': decision_basis, 'target': 'owner_reports_work_helpful',
                'schema': self.VERSION}

    def policy(self, user_id, scope, *, known_at=None):
        rows = [n for n in self.j.nodes(user_id, 'ProactivePolicySnapshot') if n.content['scope'] == scope
                and (known_at is None or instant(n.created_at) <= instant(known_at))]
        latest = max(rows, key=lambda n: (n.content['update_count'], n.created_at, n.node_id), default=None)
        return deepcopy(latest.content) if latest else {'scope': scope, 'offset': 0.0, 'update_count': 0,
            'version': 'cold-start', 'feedback_ids': [], 'quality_improvement_established': False}

    @staticmethod
    def probability(raw, offset):
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or not 0 <= raw <= 1:
            raise ValueError('usefulness probability must be finite and between zero and one')
        if offset == 0:
            return float(raw)
        bounded = min(1 - 1e-6, max(1e-6, raw))
        return 1 / (1 + math.exp(-(math.log(bounded / (1 - bounded)) + offset)))

    def assess_candidate(self, user_id, candidate, decision_basis, *, known_at=None):
        operation = getattr(candidate, 'operation', None)
        risk = self.j.channel(user_id, operation.channel_id).operations().get(operation.operation, 0) if candidate.kind == 'EXECUTE' and operation else 0
        scope = self.scope(user_id, candidate.kind, candidate.goal_ids, decision_basis, risk)
        policy = self.policy(user_id, scope, known_at=known_at)
        raw = candidate.expected_usefulness_probability
        learned = policy['offset'] if self.j.config.get('calibration_learning', True) else 0.0  # S5 arm B: no calibration learning
        if self.j.sec is None:
            calibrated = self.probability(raw, learned)
        else:  # D12: user feedback and the bounded SEC offset both move the calibrator
            bucket = f"{candidate.kind}:{risk}:{scope['feedback_origin']}"
            calibrated = self.probability(raw, max(-1.0, min(1.0, learned + self.j.sec.offset(user_id, bucket))))
        utility = candidate.expected_benefit if calibrated is None else calibrated
        return {'scope': scope, 'raw_probability': raw, 'frozen_probability': calibrated,
                'policy_version': policy['version'], 'policy_update_count': policy['update_count'],
                'policy_feedback_ids': policy['feedback_ids'],
                'score': utility + candidate.uncertainty_reduction - candidate.cost - candidate.risk,
                'score_provenance': 'SUBJECTIVE_BENEFIT_UNSCORED' if calibrated is None else 'OBSERVATIONAL_USEFULNESS_CALIBRATION',
                'causal_credit': False}

    def decide(self, user_id, episode_id, source, snapshot, candidate=None):
        self._source(user_id, source)
        if snapshot.user_id != user_id or snapshot.content['episode_id'] != episode_id:
            raise ValueError('decision episode must match frozen world state')
        if candidate is not None and (candidate.user_id != user_id or candidate.node_type != 'ProductCandidate'
                                      or candidate.content['cycle_id'] != episode_id):
            raise ValueError('same-episode candidate required')
        body = {'episode_id': episode_id, 'world_state_id': snapshot.node_id,
                'candidate_id': candidate.node_id if candidate else None,
                'kind': candidate.content['kind'] if candidate else 'WAIT',
                'assessment': deepcopy(candidate.content['adaptive_assessment']) if candidate else None,
                'goal_ids': candidate.content['goal_ids'] if candidate else [],
                'evidence_ids': candidate.content['evidence_ids'] if candidate else [],
                'outcome_status': 'UNKNOWN', 'external_effect': False, 'permissions_inferred': False}
        existing = next((n for n in self.j.nodes(user_id, 'ProactiveDecision') if n.semantic_identity_key == episode_id), None)
        if existing:
            if existing.content != body:
                raise ValueError('episode decision already frozen')
            return existing
        decision = self.j.node(user_id, 'ProactiveDecision', episode_id, body, source, modal=ModalState.INTENDED)
        self.j.edge(user_id, 'DECIDES_FROM', decision, snapshot, source)
        return decision

    def feedback(self, user_id, decision_id, helpful, text, idempotency_key):
        """Called only by the authenticated owner API, not a provider output.

        Missing feedback is not failure. An explicit null response is recorded as
        UNKNOWN and never updates the policy. Simulation runs remain labeled.
        """
        if type(helpful) not in (bool, type(None)) or not idempotency_key or not isinstance(text, str):
            raise ValueError('explicit boolean/null feedback and idempotency key required')
        decision = self.j.graph.get_node(user_id, decision_id)
        if not decision or decision.node_type != 'ProactiveDecision':
            raise ValueError('same-user proactive decision required')
        works = [n for n in self.j.nodes(user_id, 'ProductWork') if n.content.get('decision_id') == decision_id]
        delivered = any((artifact := self.j.graph.get_node(user_id, work.content.get('artifact_node_id', '')))
                        and artifact.node_type == 'PreparedArtifact'
                        and artifact.content.get('candidate_id') == decision.content['candidate_id']
                        for work in works)
        if not delivered and decision.content['kind'] != 'WAIT':
            raise ValueError('feedback requires actual delivered product work')
        if decision.content['kind'] == 'WAIT':
            raise ValueError('WAIT has no delivered-work usefulness outcome')
        with self.j.store.transaction():
            prior = [n for n in self.j.nodes(user_id, 'ProactiveFeedback') if n.content['decision_id'] == decision_id]
            existing = next((n for n in self.j.nodes(user_id, 'ProactiveFeedback') if n.semantic_identity_key == idempotency_key), None)
            if existing:
                if (existing.content['decision_id'], existing.content['helpful'], existing.content['text']) != (decision_id, helpful, text):
                    raise ValueError('feedback idempotency key reused with different content')
                return existing.to_dict()
            if any(n.content['helpful'] is not None for n in prior):
                raise ValueError('decision already has an observed usefulness outcome')
            at = self.j.clock()
            if instant(at) < instant(decision.created_at):
                raise ValueError('feedback cannot precede its decision')
            origin = self.feedback_origin(user_id)
            if decision.content['assessment']['scope']['feedback_origin'] != origin:
                raise ValueError('decision feedback population changed')
            source = self.j.source(user_id, 'ProactiveOwnerFeedback', idempotency_key,
                {'decision_id': decision_id, 'helpful': helpful, 'text': text, 'origin': origin,
                 'provider_eligible': False}, at=at)
            assessment = decision.content['assessment']
            probability = assessment['frozen_probability'] if assessment else None
            eligible = helpful is not None and probability is not None
            feedback = self.j.node(user_id, 'ProactiveFeedback', idempotency_key,
                {'decision_id': decision_id, 'helpful': helpful, 'text': text, 'origin': origin,
                 'frozen_probability': probability, 'status': 'OBSERVED' if helpful is not None else 'UNKNOWN',
                 'learning_status': 'UPDATED' if eligible else 'UNSCORED' if helpful is not None else 'UNKNOWN',
                 'brier': (probability - int(helpful)) ** 2 if eligible else None,
                 'prediction_error': int(helpful) - probability if eligible else None,
                 'causal_credit': False, 'provider_eligible': False}, source,
                authority=AuthorityClass.USER_FIRST_PERSON, observed=True)
            self.j.edge(user_id, 'EVALUATES', feedback, decision, source)
            if eligible:
                scope = assessment['scope']
                future_policy = [n for n in self.j.nodes(user_id, 'ProactivePolicySnapshot')
                                 if n.content['scope'] == scope and instant(n.created_at) > instant(at)]
                if future_policy:
                    raise ValueError('feedback knowledge time cannot move backwards')
                policy = self.policy(user_id, scope, known_at=at)
                count = policy['update_count'] + 1
                step = 1.0 / (self.PRIOR_STRENGTH + policy['update_count'])
                offset = max(-self.MAX_LOG_ODDS_SHIFT, min(self.MAX_LOG_ODDS_SHIFT, policy['offset'] + step * (int(helpful) - probability)))
                self.j.node(user_id, 'ProactivePolicySnapshot', canonical_hash(scope) + ':' + str(count),
                    {'scope': scope, 'offset': offset, 'update_count': count, 'version': 'utility-' + str(count),
                     'feedback_ids': policy['feedback_ids'] + [feedback.node_id], 'feedback_origin': origin,
                     'prior_version': policy['version'], 'decision_id': decision_id, 'learning_rate': step,
                     'max_log_odds_shift': self.MAX_LOG_ODDS_SHIFT, 'target': 'owner_reports_work_helpful',
                     'does_not_update_goal_value_or_completion': True,
                     'prediction_error': int(helpful) - probability,
                     'quality_improvement_established': False, 'causal_credit': False}, source)
            return feedback.to_dict()
