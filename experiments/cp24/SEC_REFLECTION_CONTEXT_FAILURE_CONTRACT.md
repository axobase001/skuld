# CP24 R2 SEC reflection context failure contract (written before product edit)

Frozen R1 parent: `d762c7c0826338f8f631a59fec97eaf6c9857997`. The five original, unsent `PRODUCT_TOOL_REFLECT` preflight receipts are in `/content/cp24_dev_r1/CP24_DEV_S-FRESH_seed1/runtime/receipts.jsonl`, logical turns 99鈥�103. They are actual attempted requests, not model responses. Original life stays incomplete and unchanged.

## Failure modes to disprove

1. A decision action succeeds but the same subject's day-boundary SEC reflection exceeds the fixed 32,768 input-token limit, receives five `INPUT_TOKEN_LIMIT_REQUIRED_CONTEXT` preflight failures and cannot continue. No third call or higher limit is permitted.
2. A shorter wire silently omits complete current source observations, responsibility content, group identities, all-observed counts/sums/means, or provenance IDs. Full raw evidence must remain in the SQLite graph and the view must declare what is represented by aggregates versus exact examples.
3. Example selection hides a later contradictory observed outcome or uses an unborn/future settlement. The view is deterministic, arm-independent, based only on already resolved examples, with the newest example and one newest different outcome when present.
4. The reflection/candidate admission whitelist allows a settlement ID that was omitted from the actual model view, or rejects a visible original ID. The visible example IDs and candidate admission IDs must be identical; unrelated experience IDs retain their existing contract.
5. A columnar representation loses a field or changes its value or row order when decoded. It may remove repeated JSON keys only, never mutate a public observation or responsibility.
6. The saved later-stage S-EVO reflection request must remain sendable, and every rejected S-FRESH turn 99鈥�103 must pass the actual Runtime final-wire fitter. The fixed product input/output caps remain 32,768/6,144; provider transport is never invoked during this E2E.

Run: `python -m experiments.cp24.sec_reflection_context_e2e /content/cp24_review/r2_sec_context_e2e`. The script fails under the R1 parent on the first true token overrun and writes `failure.json`, then under the repair must write per-request final-wire payloads and `checks.json` with actual token counts. The R1 source, DB and raw receipts are read only.
