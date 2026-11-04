# CP24 review and merge record

## Source and reachability

The working tree began from the byte-preserved `source_at_frozen_head.zip` nested in the CP23 formal delivery. Its original Git object was unavailable in the recovered bundles. The archive import is `6ee91a8f51f5035a43c097bb417b8695c7d46018`; this is a new import commit, not a claim that the historical CP23 commit was recovered. See `LINEAGE_AND_REACHABILITY.md` for the runtime call graph. The active Prosus path is `Runtime.loop_round -> ProductLoop.steps -> tool_subject.steps -> public MCP`; the older `RulePolicy`/channel SELECT path is not used by this benchmark.

## Targeted zero-model-call qualification

- Official public Prosus environment at `f9a1d7efde193de2242ed5afb1f3a326e362c75d`: 22 MCP tools; scripted local calibration completed 30 days and 506 public calls. Scripted calibration is not model performance.
- Native Runtime public-tool E2E: `/content/cp24_review/e2e_public_02/checks.json`, 22 tools, persistent SQLite restart, zero paid calls.
- CP23 R3 Runtime regression: `/content/cp24_review/cp23_r3_aligned/`, `R3_RUNTIME_PASS` after correcting the fixture's artificial day jump to advance its public call-before clock.
- Original B-MP seed230103 day30 and S seed230102 day23 stopped-state wire replays: `/content/cp24_review/replay_BMP_day30_v4/` and `/content/cp24_review/replay_S_day23_v3/`; 31,377 and 29,263 of 32,768 tokens, 72 current slots retained, zero public calls and zero paid calls. The old raw errors remain unchanged. These replayed requests establish path viability for those two states only.
- Prewritten tool Runtime E2E: `/content/cp24_tool_green8/`, inventory, paid side-effect dependency, and unchanged pending-replan cases. Scripted provider only, zero paid calls.
- Prewritten sales and evolution Runtime E2E: `/content/cp24_business_e2e_compat/`, S-EVO, S-FRESH and missing date-certificate cases. Each retains SQLite and request evidence; zero paid calls. Missing public coverage remains UNKNOWN.
- Targeted V4 review E2E: six directory/forecast cases plus two delivery/cap cases passed; old Prosus product code was not replaced with the older V4 source.

## Product changes and limits

The current inventory table has 72 source-bound rows and retains original observations; old repeated serialization is reduced. The actual action outcome follows structured decisions and public tool receipts. Transaction side effects are typed and dependent actions pause after cash changes or contradictory preconditions. New sales learning predicts public realized units, not hidden demand. A label requires two ordinary, same-pre-call-day public sales reports with matching totals, alias and location; missing coverage is UNKNOWN. S-EVO offspring require a distinct executable witness and post-birth comparisons; S-FRESH accepts independent roots. B-RB keeps separate ReasoningBank-style memory from the fixed public source commit.

No P1 model performance claim is made by these fixtures. The P1 dev matrix and official score require real calls and six fresh full trajectories. P3 new-seed confirmation is not authorized under the prior 10,000-request ceiling.
