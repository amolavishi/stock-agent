# Stock Agent Architecture vNext Design Package

이 디렉터리는 2026-08-18 redesign 단계의 산출물이다. 실제 코드 수정이나
Big Bang rewrite는 수행하지 않았고, 현재 repository/package를 근거로 다음
구현 단계의 경계를 확정했다.

1. [Current Reconstruction](CURRENT_ARCHITECTURE_RECONSTRUCTION.md)
2. [Gap Analysis](ARCHITECTURE_GAP_ANALYSIS.md)
3. [Target Architecture](TARGET_ARCHITECTURE_VNEXT.md)
4. [Component Matrix](COMPONENT_RESPONSIBILITY_MATRIX.md)
5. [Data Flow/State Machine](DATA_FLOW_AND_STATE_MACHINE.md)
6. [SQLite/Obsidian Contract](SQLITE_OBSIDIAN_CONTRACT.md)
7. [Provider Contracts](PROVIDER_CONTRACTS.md)
8. [Prompt Integration Contract](PROMPT_LIBRARY_INTEGRATION_CONTRACT.md)
9. [Migration Matrix](MIGRATION_MATRIX.md)
10. [Implementation Plan](IMPLEMENTATION_PLAN.md)
11. [Regression/E2E Test Plan](REGRESSION_E2E_TEST_PLAN.md)
12. [Architecture Decisions](ARCHITECTURE_DECISIONS.md)

## Verdict

`ARCHITECTURE_READY_FOR_IMPLEMENTATION`

이 판정은 **설계 문서와 dependency order가 구현을 시작할 만큼 확정됐다**는
뜻이다. 현재 runtime이 Toss/SEC/Obsidian/live DeepSeek까지 production
complete라는 뜻은 아니다. 해당 구현 P0/P1 gap은
`ARCHITECTURE_GAP_ANALYSIS.md`에 남겨 두었다.
