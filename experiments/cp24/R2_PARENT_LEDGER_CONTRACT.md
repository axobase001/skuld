# CP24 R2 parent budget failure contract (written before code)

R2 is a new six-life batch. It must not share or resume R1 life state. Original CP24 P1 and R1 are immutable, and their paid requests plus both interrupted pre-send counters count against the same original development and cumulative request ceilings.

Failure modes checked by `r2_budget_e2e.py` before any paid call:
1. R2 OUT still points at R1, or old receipts are reset by new directory: reject.
2. Either parent's frozen path, size, mtime, row count, returned-only state, or usage-estimated cost drifts: reject before a new world and before every send.
3. Either previous runner/matrix is alive in its own source root: reject parallel paid execution; zombies are not active.
4. Unknown/reserved charge in either parent: reject, while retaining one conservative pre-send reserve for each recorded SIGINT discrepancy.
5. Remaining original P1 requests are 1092-(249+1+259+1)=582; cumulative baseline is 8774+510=9284, leaving 716. The tighter P1 cap wins. Reject at 582 actual R2 rows.
6. Internal P1 fee remainder is 28.5-(5.18063792+.114688)-(5.38961984+.114688)=17.70036624 CNY. R2 wallet floor includes two reserved unknowns, 3.25+2*.114688=3.479376 CNY. A stale/failed wallet read must fail closed.
7. Prior diagnostics used old P1 2 + R1 1, leaving at most 9 of the original 12. A single R2 DIAG must not receive 12.
8. Six theoretical 180-call lives would need 1080 calls. The existing 582-call grant is insufficient to guarantee a complete matrix; do not silently expand it or count incomplete lives as valid paired results.

The test is zero API and uses only copied temporary ledgers for mutation cases. Historical ledgers and score files remain untouched.
