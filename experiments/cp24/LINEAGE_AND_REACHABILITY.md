# CP24 source lineage and production reachability

- Source: `source_at_frozen_head.zip` nested in the sealed CP23 formal seed 230101 raw archive. Its bytes equal `CP23_FORMAL_FROZEN_SOURCE_TRANSFER.zip`. The formal runner imports `skuld.app.runtime` and `skuld.loop.tool_subject` from this source family.
- Archive provenance caveat: `SOURCE_PROVENANCE.json` says sealed 2026-10-21 and no Git metadata, despite CP23 code inside. The claimed historical commit `7c2137cc28e8f34fbeeb82cf10e5f85bbe6b285e` is absent from available bundles. `work/latest.bundle` ends at old V4 `85a0cb5`; it is not the CP23 baseline.
- CP24 import commit `6ee91a8f51f5035a43c097bb417b8695c7d46018` is a NEW archive-import commit, not a recovered historical commit. Imported Git tree fingerprint: `81324e62e657f43080174b885739092abfc294b9`.
- Public environment: Prosus Vending Bench v3 `f9a1d7efde193de2242ed5afb1f3a326e362c75d`, 30 days, 22 public MCP tools. Benchmark private engine and future state are evaluator-only.

| Stage | Production writer / reader | CP24 reachability |
|---|---|---|
| Runtime dispatch | Formal runner sets `tool_subject=True`, `business_events=True`; `loop_runtime._round` 鈫� `ProductLoop.steps` 鈫� `tool_subject.steps` | Yes |
| Current decision context | `tool_subject.steps` builds request; `business_events.pack_context` projects inventory and reference dictionary | Yes; context and recovery fix |
| Action and evidence | `tool_subject.steps` invokes public MCP and writes ToolAttempt, ToolObservation, Fact, Belief, ToolExecution; `business_events.ingest` classifies event | Yes; side-effects and state fix |
| SEC and candidate birth | day boundary reflection writes ToolSEC; `business_events.admit` validates proposal, parent and evidence | Yes; variant and target fix |
| Future scoring and selection | `business_events.ingest/issue/settle/rule_state` freeze predictions, score later public observations, publish or retract | Yes; numerical target and candidate selection fix |
| Learned-state consumption | `business_events.scene` retrieves published matching models; `tool_subject.steps` includes them in actual model request | Yes; relevance and value interface fix |
| Old V4 product candidate and `RulePolicy` | Ordinary `product_loop` proposal route | Not reached by Prosus `tool_subject` runner; old fixture remains a regression, not public-task proof |
| V4 delivery, local_directory, and S5 D8 | Distinct product interfaces | Not reached by Prosus public MCP run; transplant only if a corresponding CP24 path actually calls them |

Known production gaps at import: sales reports are readable but not represented as settled sales targets; `ToolExecution.success` relies on MCP `is_error` although event ingestion can find business rejection; a read may have a business side-effect but does not block dependent later actions; SEC grouped history exposes only the first settlement ID of each group; preexisting inventory projection and pending-replan can still exceed context.
