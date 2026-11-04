# CP24 P1-R1 parent-ledger contract (fixed before runner edits)

The original frozen P1 remains evidence, not an R1 prefix. Its persisted `calls.jsonl` has exactly 249 actual rows and estimated spend CNY 5.18063792. The B-RB SIGINT crossed `Budget.save` before the HTTP send could be resolved: reserve one additional possible request and CNY 0.114688 without appending a fabricated old receipt. Prior pre-CP24 cumulative reserve is 8,774 requests. Thus R1 starts at 9,024 effective cumulative requests, has 842 of the original P1 1,092 requests left, and has CNY 23.20467408 of the original internal CNY 28.5 guard left. A fresh wallet read controls the actual balance. Keep at least CNY 3.364688 (old R4 reserve and cushion plus this one unknown CP24 reservation), and more if earlier deductions have not posted.

## Failure modes to reject before a new world and before each send

1. The old P1 matrix or runner is active again, including under a new PID. Zombies do not count as active.
2. The old ledger path is missing, changes size or modification time, gains/losses rows, changes its recorded cost, or contains `RESERVED`/`UNKNOWN_CHARGE`. R1 must not silently treat it as an empty predecessor.
3. The R1 output directory is reused from the original P1, or a reset new ledger starts with the original 8,774 baseline and 1,092-call cap.
4. The 250th possible old call is forgotten or written as a fake returned receipt; the unknown reservation must remain explicit.
5. A fresh R1 request would cross its 842-call remaining P1 cap, 10,000 cumulative cap, CNY 23.20467408 internal guard, per-life 180 calls, or live wallet reserve. All actual retries count.
6. The original P1 used 2 of 12 diagnostic calls. R1 may use at most 10 diagnostic calls in total and in its single diagnostic life; the per-life cap must read the reduced manifest value rather than a stale literal 12.
7. A frozen manifest or product HEAD changes during the R1 matrix, or a new life is made after a global block. An incomplete life keeps the official zero result; no arm inherits another arm's remaining calls.

Start and transport guards read the old ledger's frozen file metadata on every check and read its full rows once at R1 startup. No repeated full-file hashing is used. All original old receipts, errors, scores, and SQLite files remain unmodified.
