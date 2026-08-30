# Stock Agent v1.1 Production Runtime

This repository contains the Python modular-monolith runtime that consumes the
v2.2 Prompt Library. Python owns gates, workflow state, dependency freshness,
risk arithmetic, position sizing, and the single authoritative final action.

## Run tests

```powershell
python -m unittest discover -s tests -v
python outputs/STOCK_AGENT_OBSIDIAN_PROMPT_LIBRARY_V2_2/VALIDATION/validate_contracts.py
```

Provider-backed acceptance path:

```powershell
python -m stock_agent --strict --mode HUNT_ONLY --input tests/fixtures/strict_provider_recorded_input.json
```

`--strict` requires `provider_recordings` containing raw market context,
universe, execution, and portfolio observations. It does not accept fixture
conclusions as authoritative input.

DeepSeek connection check:

```powershell
python -m stock_agent --smoke-deepseek
```

GPT-5.6 Luna routing uses `LUNA_MODEL=gpt-5.6-luna`; the runtime sends
`reasoning_effort=high` for `LUNA_HIGH` and `reasoning_effort=xhigh` for
`LUNA_EXTRA_HIGH`. The endpoint must be independently verified before live use.

Temporary ChatGPT-authenticated Codex reasoning transport:

```powershell
codex --version
codex login status
python -m stock_agent --smoke-codex
```

`CodexExecProvider` uses an isolated empty temporary workspace, stdin Prompt
transport, `--sandbox read-only`, no approval escalation, disabled web/shell/
subagent surfaces, and `forced_login_method="chatgpt"`. It strips API-key and
access-token environment variables from the child process. A missing or
unlaunchable CLI is a fail-closed `NOT_RUN` condition, not a Fake/Recorded PASS.

The optional Obsidian reference subsystem is projection-only and source-backed:
`ReferenceRequirement → ReferenceResolver/Builder → ACTIVE registry →
ReferencePackCompiler`. Dynamic company/date-specific Evidence cannot be
promoted into a reusable reference.

Strict execution also requires an evidence-linked `EconomicAssessmentReceiptV2`;
numeric caller-supplied `risk_inputs` alone cannot authorize sizing.

The runtime has no production market-data or LLM credentials by default. Those
are explicit adapters at the pipeline input boundary; deterministic tests use
fixtures and never grant authority to an LLM response.
