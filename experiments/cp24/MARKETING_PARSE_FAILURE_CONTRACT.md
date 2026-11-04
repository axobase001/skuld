# CP24 R1 marketing receipt parser failure contract (pre-code)

The original S-FRESH seed 1, turn 75, action 5 returned a successful public `run_marketing` response ending `traffic multiplier 1.15.`. The parser greedily captured the sentence period and raised `ValueError`; `ToolAttempt` and `ToolObservation` persisted, while `ToolExecution` did not. The next round correctly refused an uncertain re-execution, ending the life on day 20. Original score and receipts remain unchanged.

Before changing production code, verify through the native Runtime loop:
1. A successful public response with a sentence-ending period produces `MARKETING_CONFIRMED` with numeric 1.15, a `ToolExecution` linked to the actual observation, and no dangling attempt.
2. A malformed numerical field such as `1.15..` must not silently become a confident numeric marketing event or crash the loop. Preserve the raw observation and record an execution receipt.
3. The fix must not alter the model-selected action, tool arguments, public response, model-request count, or restart policy. No paid calls or hidden simulator state enter this fixture.

The fixture below uses the exact successful public response preserved in the stopped-state SQLite. It is a targeted E2E contract, not a claim about model performance or official business score.
