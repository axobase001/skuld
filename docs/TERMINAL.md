# Skuld V4 × Pi Terminal

[Home](../README.md) · [简体中文](../README.zh-CN.md)

This repository contains the V4 subject runtime, a Pi SDK sidecar, and the desktop Terminal. Skuld owns persistent state, decisions, approval, and feedback. Pi carries out the model turn or a guarded tool operation and returns receipts. The Terminal displays persisted state and actual execution results.

```text
User → Skuld V4 → decision / capability → Pi → tool or model result
                                                ↓
                         Terminal ← Skuld evidence / outcome
```

## Run locally on Windows

Requirements: Python 3.12, Node.js 22.13 or later, and a DeepSeek API key. Install Python dependencies from `requirements.txt` and `terminal_app/requirements-ui.txt`, then build the Pi sidecar:

```powershell
python -m pip install -r requirements.txt -r terminal_app/requirements-ui.txt
cd terminal_app/harness_pi
npm ci --ignore-scripts --no-audit --no-fund
npm run build
cd ../..
$env:DEEPSEEK_API_KEY = '<your key for this shell>'
./start_terminal.ps1 -Workspace (Get-Location).Path
```

The launcher reads the key from the current shell or a Windows DPAPI credential at `.skuld-terminal-secrets/deepseek.dpapi`. It does not commit credentials. The default subject graph is created in `.terminal-runtime/world.db` on first run. The UI starts in **PAUSED**. Switch to **OBSERVE** for chat and `/read path`; **ACT** can propose `/write path | content`, followed by `/approve action_id` for that exact new file. Writes are create-only and use a time-limited capability. The interface starts in English; use the language control to switch to 简体中文.

## Local profiles

The `default` profile keeps its own graph and workspace. Named profiles use separate `.terminal-accounts/<name>/runtime`, `workspace`, and DPAPI credentials:

```powershell
./start_terminal.ps1 -ListProfiles
./start_terminal.ps1 -Profile alice -SetProfileKey
./start_terminal.ps1 -Profile alice
```

Named profiles do not inherit the default credential. The Pi allowed root for a named profile is that profile's workspace. These are application-level boundaries for one Windows user.

## Historical evidence

An optional Claude export can be imported in resumable batches. **The export is not in this repository.** Pass your own local export path:

```powershell
./start_terminal.ps1 -ImportHistory -ExportPath 'C:\path\to\your-export.zip' -MaxBatches 1
./start_terminal.ps1 -VerifyHistory
./start_terminal.ps1 -SeedWorld
```

Historical episodes stay distinct from present facts and authority. `-SeedWorld` uses the existing V4 digest mechanism, checks the official DeepSeek balance, and limits each invocation to three model attempts. The runtime also enforces twelve digest batches per hour. Digestion stops at its balance, call, or provisional-inference review limit. Nothing from the local history or graph is included in the public repository.

## Check the integration

```powershell
./start_terminal.ps1 -E2E
./start_terminal.ps1 -E2EAccounts
```

The E2E checks cover a Pi read, a failed read returned to Skuld, OBSERVE write denial, exact ACT approval, a real create-only write, model conversation, and state recovery after restart. They create local evidence under ignored `.terminal-e2e*` directories. The model turn requires a configured credential.

## Connection points

| Layer | Source |
| --- | --- |
| Skuld-owned state and permissions | [`terminal_app/v4_wiring.py`](../terminal_app/v4_wiring.py) |
| Pi process and receipt transport | [`terminal_app/pi_bridge.py`](../terminal_app/pi_bridge.py) |
| Guarded Pi operations | [`terminal_app/harness_pi/src/guarded_tools.ts`](../terminal_app/harness_pi/src/guarded_tools.ts) |
| Desktop UI | [`terminal_app/qml/MainV4.qml`](../terminal_app/qml/MainV4.qml) |
| Persistent V4 core | [`skuld/app/runtime.py`](../skuld/app/runtime.py) |

The Terminal currently exposes a narrow read and create-only write path. Shell execution and autonomous multi-tool planning are outside this local integration.
