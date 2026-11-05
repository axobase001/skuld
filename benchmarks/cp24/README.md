# Skuld · Arrival | CP24 experiment code and results

[Home](../../README.md) · [Meta Glimmer 30B validation](../meta-glimmer-30b/README.md)

The [`experiments/cp24`](../../experiments/cp24/) directory contains the CP24 runner, matrix orchestration, contracts, and Meta local-model adapter. This directory contains the available public result data and frozen-identity excerpts from the paused P3 confirmation scope.

These are historical experiment records included with Hackathon Release 2026. The separate [Meta Glimmer 30B run](../meta-glimmer-30b/README.md) used a locally served model; the desktop Terminal uses DeepSeek API for conversation.

| Arm | Complete 30-day lives | Incomplete | Not started / not run |
| --- | ---: | ---: | ---: |
| S-EVO | 8 / 10 | 1 | 1 |
| S-FRESH | 8 / 10 | 0 | 2 |
| S-NOEVO | 8 / 10 | 0 | 2 |
| B-RB | 8 / 10 | 0 | 2 |
| F1 | 0 / 10 | 0 | 10 |
| F2 | 2 / 10 | 3 | 5 |

The public [`P3_RESULT_MATRIX.csv`](results/P3_RESULT_MATRIX.csv) lists all 60 planned lives and their recorded status. [`P3_EVIDENCE_CHECKS.json`](results/P3_EVIDENCE_CHECKS.json) records checks on the 38 archived lives. [`SCOPE_STATUS.md`](results/SCOPE_STATUS.md) explains the paused state and descriptive results. Only 34 lives have complete 30-day outcomes. The planned ten-seed six-arm panel and preregistered statistical claim were not completed.

The matrix contains references to separately archived original trajectories. Those archives were stored privately during the experiment and are not republished here. The Meta single-life package in this repository includes its raw model-call and receipt data for direct inspection.
