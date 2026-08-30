# Stock Agent Obsidian Prompt Library v2.2

상태: **PROMPT_LIBRARY_READY**

- Library: `2.2.0`
- Rule contract: `Investment Rules v2.0`
- Metadata: `prompt-meta-2.2`
- JSON Schema: Draft 2020-12
- Architecture compatibility: **Stock Agent Architecture v1.1**

이 라이브러리는 Production Stock Agent, workflow engine, gate engine, DB constraint를 구현하지 않는다. LLM은 분석·claim·scenario·audit·비권위 recommendation만 만들고, Python이 gate·산술·position sizing·state·FinalAction·Fresh Money 0..1 allocation을 소유한다.

## v2.2 핵심 수리

- 27개 leaf output schema를 실제 typed JSON Schema로 전환했다.
- `system.analysis_grounding`을 output 없는 `MIXIN`으로 변경했다.
- Prompt Composer 계약에 dedupe·topological order·mixin first·one leaf owner·composition hash를 명시했다.
- Deep Research용 `directional_probability_hypothesis`와 Execution 전용 risk/asymmetry capability를 분리했다.
- Capital Prescreen extraction receipt와 Python Gate receipt를 분리했다.
- EvidenceRequest item을 Python workflow가 소비 가능한 구조로 typed했다.
- STARTER를 full `StarterPlanV2` + ex-ante `PlannedAddV2` 계약으로 잠갔다.
- ADD를 4개 전용 receipt와 subject/trigger/evidence subset lineage로 잠갔다.
- `FailurePathV2`를 Research·Failure Capability·Adversarial Audit·Final Synthesis가 공유하며 독립성을 의미 검증한다.
- Markdown inline JSON Output Contract를 제거하고 formal schema를 유일한 machine Source of Truth로 고정했다.
- Portfolio alternative identity/capital path/rank/EV/R:R/strengthening Evidence를 typed했다.
- Portfolio의 cash/security path, scope, snapshot, rank, preferred row 일관성을 schema+semantic validator로 강제했다.
- MarketContextGate·SectorGate·StageGate·CapitalPrescreenGate·MarketExecutionGate receipt를 전용 타입으로 분리했다.
- Discovery 직후 StageGate를 Prescreen·Deep Research의 선행 prerequisite로 강제했다.
- frontmatter를 metadata Source of Truth로 하고 manifest를 content-hash 포함 생성 projection으로 고정했다.
- 32 positive, 62 negative contract test와 4개 holding-horizon runtime policy case, Prompt-body lint·composition·semantic dependency·HUNT_ONLY 검사를 실행한다.

## 핵심 파일

1. `PROMPT_LIBRARY_ARCHITECTURE_V2_2.md`
2. `PROMPT_LIBRARY_INDEX_V2_2.md`
3. `SCHEMAS/output_schema_registry_v2_2.json`
4. `SCHEMAS/prompt_metadata_schema_v2_2.json`
5. `RUNTIME_CONTRACTS/architecture_runtime_contract_v1_1.json`
6. `prompt_registry_manifest_v2_2.json`
7. `VALIDATION/validate_contracts.py`
8. `V2_1_TO_V2_2_PATCH_REPORT.md`
9. `SELF_AUDIT_V2_2.md`
10. `FINAL_ADVERSARIAL_AUDIT_REMEDIATION.md`
11. `FINAL_CONTRACT_HARDENING_REMEDIATION.md`
12. `FINAL_ACCEPTANCE_AUDIT_REMEDIATION.md`

## 검증 실행

```powershell
python .\VALIDATIONalidate_contracts.py
```

성공 조건은 `overall: PASS`, P0/P1 open 0이다.


