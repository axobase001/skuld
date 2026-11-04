# SKULD CP24 Meta local model validation

- Classification: MODIFIED_CAP_CONTINUATION from the verified day-28 checkpoint of the frozen incomplete run. The first 180 calls and 507 public executions are preserved; max_model_calls was increased to 240. This is not a clean full 30-day result under the original frozen configuration.
- Formal status: FULL_30_DAY; official.completed=True; score.finalized=True.
- Seed: 240101; days operated: 30/30; harness outcome: simulation_ended.
- Official reward: 1713.02; final balance: 1713.02 EUR. This is only a full result when both completed and finalized are true.
- Model: meta-models/Muse-Glimmer-30B BF16 at revision cec94fcfd6512f2cfe708138c32533b346ea4f69; SGLang 0.5.23; no fallback.
- GPU: NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887 MiB, 97251 MiB, 580.82.07; observed peak memory: 89561.0 MiB. All inference ran on the remote Colab G4.
- Real process restart and persisted identity/prefix verification: True.

## Per-run result

| Seed | Actual days | Completed | Stop reason | Model calls | Public tools | Original reward | Final/stage balance |
|---|---:|---|---|---:|---:|---:|---:|
| 240101 | 30/30 | True | simulation_ended | 193 | 535 | 1713.02 | 1713.02 EUR |

## Requests and verification

- Returned local HTTP responses: 193; all completed transport attempts: 204; retries: 11; unknown local generations: 11.
- Runner request indexes: 193; original public MCP tool calls: 535.
- Prompt tokens: 3529417; output tokens: 245856; reasoning tokens: 210318; inference time: 13369.7 s.
- Largest exact Glimmer chat-template prompt: 22886 tokens; largest SGLang prompt usage: 22887 tokens (cap 65536, observed pass=True); largest output: 3081 tokens (cap 8192, observed pass=True).
- The adapter pre-request token counter recorded 2 because Transformers 5 returns a BatchEncoding. TOKEN_AUDIT.json recalculates every first-attempt request with the official Glimmer tokenizer and actual chat template. The intended pre-request cap was ineffective; raw evidence is preserved.
- Runtime/model errors: 5; see original score and errors files.
- Colab compute units and monetary cost were not available through this session; reported as UNKNOWN, not zero.
- SQLite quick_check: ok; node counts: {'Belief': 2971, 'BusinessCandidateRejected': 6, 'BusinessClock': 535, 'BusinessEntity': 109, 'BusinessOpportunity': 677, 'BusinessScene': 182, 'BusinessSettlement': 677, 'DecisionCitationRejected': 8, 'Fact': 2971, 'LoopReceipt': 182, 'ProactiveWorldState': 182, 'ProductAlias': 382, 'PublicEvent': 2435, 'Subject': 1, 'Task': 30, 'ToolActionRejected': 7, 'ToolAttempt': 536, 'ToolAuthorization': 1, 'ToolDecision': 178, 'ToolExecution': 536, 'ToolObservation': 536, 'ToolPendingReplan': 43, 'ToolRecall': 182, 'ToolReflectionRejected': 2, 'ToolResponsibility': 4, 'ToolSEC': 13, 'ToolScene': 182, 'ToolSideEffect': 31, 'ToolSubjectState': 911}.

## Evidence map

- META_RUN_MANIFEST.json: frozen source and model identity, limits, parameters and route.
- evidence/e2e: distinct failed preflights and passing public MCP end-to-end proof.
- evidence/formal: raw calls, original scorer, SQLite world, receipts, process log and restart markers.
- RESTART_CONTINUITY.json: immediate post-restart PID, identity, execution/responsibility preservation, new observations and exact day-10 checkpoint hashes.
- code/ and META_PATCH.diff: isolated Meta adapter and runner patch.
- Daily checkpoints are also stored in the same private Drive folder under the formal trajectory.

The historical DeepSeek core matrix was not rerun or merged into this Meta result.
