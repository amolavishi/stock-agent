# First Live HUNT Repair Test Results

## Baseline

- Before repair: `81 PASS`.
- After repair: `87 PASS`.
- Existing tests were retained; new adversarial/reporting tests were added.

## Commands

```text
python -m unittest discover -s tests -q       PASS (87)
python -m compileall -q stock_agent tests     PASS
python outputs/STOCK_AGENT_OBSIDIAN_PROMPT_LIBRARY_V2_2/VALIDATION/validate_contracts.py --library outputs/STOCK_AGENT_OBSIDIAN_PROMPT_LIBRARY_V2_2
                                                PASS / failure_count=0
```

## SQLite acceptance

### Strict HUNT_ONLY

- Database: `work/emergency_repair_hunt_20260820_v2.db`
- Run: `run-e2d042ed0e2c4cca98e5054b49b85fc6`
- WorkItems: 11, all `SUCCEEDED`
- StageGate receipt: present
- CapitalPrescreenGate receipt: present
- FinalActions: 0
- Report: `work/emergency_repair_hunt_20260820_v2.md`
- Report contains the same `run_id` and SQLite terminal outcome.

### Strict HUNT_AND_EXECUTION_REVIEW

- Database: `work/emergency_repair_execution_20260820_v2.db`
- Run: `run-a7b35f5f430f47ca9e42dd9cd1c91a34`
- WorkItems: 18, all `SUCCEEDED`
- FinalAction: one `WATCH` (zero positive commitment)
- Writer: Python `FinalAllocationGate`
- Model calls/cost reservations: 18/18
- PortfolioSnapshot RawArtifact and Evidence receipt: present
- Broker writes: 0
- Report: `work/emergency_repair_execution_20260820_v2.md`

## Live / NOT_RUN

The following were not executed in this repair run and are not represented as
PASS: live Toss market, live Toss portfolio, live SEC/EDGAR, live non-SEC
research, live Luna/Codex provider, and live broad-universe HUNT.

