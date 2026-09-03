"""V8 MAIN Discovery Coach.

This module does NOT replace V8 discovery with Python heuristics.
It makes the existing MAIN `workflow.stock_scout` remain the sole final
Discovery output owner while forcing actual model-executed V8 02..14 scanner
passes before that MAIN call.

The forensic recall layer is therefore a coach/compliance/observability layer:
- Python owns universe/data integrity and execution receipts.
- LLM scanner passes own scanner-specific discovery reasoning.
- MAIN stock_scout owns final DiscoveryCandidateSetV2.
- Step18 remains the only Research Grade authority.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import Any

from . import runtime as runtime_module
from .models import RunMode, RunOutcome, canonical_hash

V8_MAIN_DISCOVERY_COACH_VERSION = "V8_MAIN_DISCOVERY_COACH_V1.0"
V8_MAIN_FORENSIC_AUDIT_SHA256 = "47494df8fd0464c3fb63c6f2a5facd7dd6296616bec635b6faebe15e4ddab616"
V8_MAIN_MIN_UNIQUE = 150
V8_MAIN_PREFERRED_UNIQUE = 250

# Scanner identity is preserved from the canonical V8 source manifest.  These
# hashes are source identity receipts, not executable Python strategy logic.
V8_SCANNERS: dict[str, dict[str, str]] = {
    "02": {"name": "비AI·비반도체 광역 블라인드", "sha256": "e06984b98687218bd691e4ac5b274610ea58a954e3f6da76610b2721ba3cc317", "role": "Quantamental Discovery Scanner", "goal": "기존 티커에 앵커링하지 않고 비AI·비반도체 중소형주에서 Stage 0·1 Good Lag와 가까운 촉매 후보를 광역 탐색한다."},
    "03": {"name": "최근 IPO / Busted IPO 재평가", "sha256": "c2974f5ee3bbed13e112cd7a49053b714b63c8492da1fca6b369a9b67dbe5a0d", "role": "IPO Revaluation Scanner", "goal": "최근 IPO·busted IPO에서 가격 실망과 달리 실적·마진·수요가 개선되는 재평가 후보를 찾는다."},
    "04": {"name": "턴어라운드 실적주", "sha256": "1a1927802fe33b2c90bdd51803770ac7c152bf52dc46b9178d937aa06a58e121", "role": "Turnaround Earnings Scanner", "goal": "비용절감 착시와 구조적 영업레버리지를 분리하여 매출·GM·EBITDA·FCF inflection 후보를 찾는다."},
    "05": {"name": "정책·국방·원전·우라늄·핵심광물·에너지안보", "sha256": "383bce853fe0505bfa1f8480b78c8a14aacdaae3552277580b17557edd599026", "role": "Public-Policy Event Discovery Analyst", "goal": "헤드라인이 아니라 실제 예산·계약·의무부담·매출로 전환되는 1~8주 정책/안보 재평가 후보를 찾는다."},
    "06": {"name": "우주·방산 ISR·항공우주 부품", "sha256": "f1a49ce245d115ec6a3f8758eb8e38ce0cc17ad2a9e3bfe79a459a747cabfdbb", "role": "Defense/Space Components Scanner", "goal": "발사체 베타 추격이 아니라 funded backlog, book-to-bill, ISR, 방산전자, 고신뢰 부품의 매출 전환 후보를 찾는다."},
    "07": {"name": "덜 알려진 수익성 개선 소형주", "sha256": "ff76c4cafae2e6d204c019c2f59bbe90bc37b4713b4476a5c7feb8b0f434476e", "role": "Underfollowed Profitability Scanner", "goal": "과열 테마 밖에서 revenue surprise와 GM/EBITDA/FCF 개선이 나타나는 덜 알려진 소형주를 찾는다."},
    "08": {"name": "공모·블록딜·Secondary 소화 후 회복", "sha256": "a1c713679274209b99b7c1e165a2cb2b350d25fd002b70038da1b1aedf9408c", "role": "Equity Supply Event Scanner", "goal": "일시적 공급 충격 이후 fundamental thesis가 유지되고 실제 매물 흡수 증거가 나타나는 후보를 찾는다."},
    "09": {"name": "내부자 매수·자사주 방어형 턴어라운드", "sha256": "a7cbbb941afe54cbbd16aeef103ebe4537126197b182ac09dd1dfa921fb2b956", "role": "Capital-Return Turnaround Scanner", "goal": "실적 개선과 진짜 open-market insider buy 또는 실제 buyback이 결합된 후보를 찾는다."},
    "10": {"name": "부채 리파이낸싱·파산위험 제거", "sha256": "8c42424b1357578be028e9ec14e412e339ece2255bae572768d1d2da113b6898", "role": "Credit-to-Equity Refinancing Scanner", "goal": "refinancing으로 파산·유동성 할인율이 실제 낮아지고 영업현금흐름이 받쳐주는 distressed-to-normal 후보를 찾는다."},
    "11": {"name": "실적 후 추정치 상향·지연반응", "sha256": "91781d893a4ac5f65e6d23f0296dc14067c736311bf7082928896d26aa47a418", "role": "Earnings Revision Scanner", "goal": "실적·가이던스·선행지표는 상향됐지만 벤치마크 대비 가격반응이 제한적인 1~8주 PEAD/revision-lag 후보를 찾는다."},
    "12": {"name": "고객집중 해소·두 번째 대형고객", "sha256": "5301b0971d19e57236f69cfefcae5d25122df3b425da4625d29e5e1711a9d502", "role": "Customer Diversification Event Scanner", "goal": "단일 고객 의존 할인에서 벗어날 경제적으로 의미 있는 두 번째 고객/채널 후보를 찾는다."},
    "13": {"name": "핀테크·헬스케어·비반도체 소프트웨어 로테이션", "sha256": "8aad11ea0e9e09578400538c7ca18b3349cea6337411c0f2acdc48dde95fc910", "role": "Rotation Discovery Scanner", "goal": "비반도체 성장 영역에서 KPI 개선과 상대강도가 함께 나타나는 company-specific 후보를 찾는다."},
    "14": {"name": "AI 병목 확장 예외 후보", "sha256": "1f1c6077be350289166adffc794f10045c2c74bf004175e1bd814ce0352609e6", "role": "AI Infrastructure Bottleneck Scanner", "goal": "GPU 직접 베타가 아니라 전력·냉각·광·테스트·랙·스토리지·전력변환·운영SW로 수요가 전이되는 2·3차 병목 후보를 찾는다."},
}

COMMON_GUARDRAIL = """
# V8 MAIN Discovery Recall Guardrail
You are executing one canonical V8 Discovery scanner inside MAIN.
Discovery Recall is broad; Certification Precision is narrow.

Hard boundaries:
- RUN_MODE = HUNT_ONLY_RECALL_FIRST.
- You do NOT create Research Grade A/A-/B+/B.
- DISCOVERY_PRIORITY_SCORE is research ordering only, never a grade.
- DEEP_DIVE_NOW/DEEP_DIVE_SECONDARY are research routes, never buy actions.
- UNKNOWN is not PASS and is not FAIL. Keep decision-relevant UNKNOWN as a verification question.
- Only verified cheap structural hard gates justify early EXCLUDE. Missing consensus, final valuation, PW-EV, Full SEC, exact catalyst surprise, or portfolio fit do not.
- Market regime changes research priority; it must not auto-reject a company-specific event.
- Conference/IR calendar alone is not a strong catalyst. Post-event price collapse alone is not Good Lag.
- Do not double-count multiple KPIs caused by one economic event as independent fuel.
- Do not reverse-engineer a target from a desired return.
- No target price, stop, quantity, position sizing, execution action, or broker instruction.
- State weaknesses (>=3 when a candidate is retained), UNKNOWNs, and exact next verification questions.
- If cheap data is insufficient but the information value of resolving it is high, prefer DEEP_DIVE_SECONDARY over silent rejection.
""".strip()

SCANNER_SCHEMA_ID = "V8MainDiscoveryScannerResultV1"
SENTINEL_SCHEMA_ID = "V8MainDiscoverySentinelV1"


def _scanner_schema() -> dict[str, Any]:
    candidate = {
        "type": "object",
        "properties": {
            "security_id": {"type": "string", "minLength": 1},
            "discovery_priority_score": {"type": "number", "minimum": 0, "maximum": 100},
            "signal_strength": {"type": "string", "enum": ["STRONG", "MODERATE", "WEAK", "NONE", "UNKNOWN"]},
            "research_value": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW", "UNKNOWN"]},
            "recommended_discovery_action": {"type": "string", "enum": ["DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY", "WATCH_STAGE0", "WATCH_RESET", "EXCLUDE"]},
            "rationale": {"type": "string", "minLength": 1},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "weaknesses": {"type": "array", "items": {"type": "string"}},
            "unknowns": {"type": "array", "items": {"type": "string"}},
            "verification_questions": {"type": "array", "items": {"type": "string"}},
            "cheap_hard_gate_status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
        },
        "required": ["security_id", "discovery_priority_score", "signal_strength", "research_value", "recommended_discovery_action", "rationale", "strengths", "weaknesses", "unknowns", "verification_questions", "cheap_hard_gate_status"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "scanner_id": {"type": "string", "enum": sorted(V8_SCANNERS)},
            "scanner_source_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "execution_status": {"type": "string", "enum": ["COMPLETE", "PARTIAL", "DATA_BLOCKED"]},
            "screened_count": {"type": "integer", "minimum": 0},
            "candidates": {"type": "array", "items": candidate},
            "systemic_unknowns": {"type": "array", "items": {"type": "string"}},
            "search_expansion_questions": {"type": "array", "items": {"type": "string"}},
            "grade_authority": {"const": False},
        },
        "required": ["scanner_id", "scanner_source_sha256", "execution_status", "screened_count", "candidates", "systemic_unknowns", "search_expansion_questions", "grade_authority"],
        "additionalProperties": False,
    }


def _sentinel_schema() -> dict[str, Any]:
    audit = {
        "type": "object",
        "properties": {
            "security_id": {"type": "string", "minLength": 1},
            "scanner_id": {"type": "string", "enum": sorted(V8_SCANNERS)},
            "finding": {"type": "string", "enum": ["KEEP", "UPGRADE_SECONDARY", "DOWNGRADE_WATCH", "MISCLASSIFIED_EXCLUDE", "DATA_BLOCK"]},
            "rationale": {"type": "string", "minLength": 1},
        },
        "required": ["security_id", "scanner_id", "finding", "rationale"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["COMPLETE", "INCOMPLETE"]},
            "audits": {"type": "array", "items": audit},
            "systematic_false_negative_risk": {"type": "boolean"},
            "grade_authority": {"const": False},
        },
        "required": ["status", "audits", "systematic_false_negative_risk", "grade_authority"],
        "additionalProperties": False,
    }


def _register_prompt(runtime: Any, prompt_id: str, schema_id: str, body: str) -> None:
    runtime.registry.setdefault("schemas", {})[schema_id] = _scanner_schema() if schema_id == SCANNER_SCHEMA_ID else _sentinel_schema()
    runtime.prompts[prompt_id] = {
        "prompt_id": prompt_id,
        "version": "1.0",
        "prompt_kind": "LEAF",
        "output_schema": schema_id,
        "required_inputs": ["effective_rule_pack"],
        "optional_inputs": [],
        "compose_with": [],
        "requires_results": [],
        "requires_capabilities": [],
        "allowed_run_modes": ["HUNT_ONLY", "HUNT_AND_EXECUTION_REVIEW"],
        "_body": body,
    }
    existing = next((item for item in runtime.manifest.get("prompts", []) if isinstance(item, dict) and item.get("prompt_id") == prompt_id), None)
    entry = {"prompt_id": prompt_id, "content_hash": canonical_hash(body), "file": f"RUNTIME:{prompt_id}"}
    if existing is None:
        runtime.manifest.setdefault("prompts", []).append(entry)
    else:
        existing.update(entry)


def _install_prompts(runtime: Any) -> None:
    for scanner_id, spec in V8_SCANNERS.items():
        body = f"""{COMMON_GUARDRAIL}\n\n# Scanner {scanner_id}: {spec['name']}\nRole: {spec['role']}\nCanonical strategy goal: {spec['goal']}\nCanonical source SHA256: {spec['sha256']}\n\nExecution requirement:\nScreen the entire candidate_universe_packet supplied in RUNTIME_INPUT. Do not merely name a theme. For every retained name, distinguish Signal Strength from Research Value. Preserve information-rich but unresolved cases as SECONDARY. `screened_count` must equal the number of eligible securities you actually assessed. `scanner_id` and `scanner_source_sha256` must exactly match this scanner. Return grade_authority=false."""
        _register_prompt(runtime, f"v8_main.discovery_{scanner_id}", SCANNER_SCHEMA_ID, body)
    sentinel_body = f"""{COMMON_GUARDRAIL}\n\n# V8 MAIN Rejection Sentinel\nAudit the supplied stratified low/unknown/watch/exclude sample for false negatives. You are not allowed to create Research Grade. Prefer UPGRADE_SECONDARY when a missing fact is decision-critical, resolvable, and high-information-value. A verified structural fatality stays excluded. `grade_authority=false`."""
    _register_prompt(runtime, "v8_main.discovery_rejection_sentinel", SENTINEL_SCHEMA_ID, sentinel_body)

    # MAIN stock_scout remains the only final DiscoveryCandidateSetV2 owner.
    # Append an in-memory coach contract rather than replacing the repository
    # prompt file or changing its output authority.
    meta = runtime.prompts.get("workflow.stock_scout")
    if isinstance(meta, dict) and "V8 MAIN COACHED DISCOVERY" not in str(meta.get("_body") or ""):
        meta["_body"] = str(meta.get("_body") or "") + "\n\n# V8 MAIN COACHED DISCOVERY\nThe RUNTIME_INPUT may include `v8_main_scanner_results`, `v8_main_scanner_receipts`, and `v8_main_rejection_sentinel`. These are mandatory upstream Discovery analyses. Synthesize them without treating scanner score/rank/research_value as Research Grade. Preserve all high-information-value unresolved cases as DEEP_DIVE_SECONDARY unless a verified cheap hard gate justifies EXCLUDE. Do not silently discard a ticker supported by multiple independent scanner passes; if you do not retain it, state the unresolved reason in data_gaps/excluded according to the canonical schema. MAIN `workflow.stock_scout` remains the sole final DiscoveryCandidateSetV2 output owner."
        for entry in runtime.manifest.get("prompts", []):
            if isinstance(entry, dict) and entry.get("prompt_id") == "workflow.stock_scout":
                entry["content_hash"] = canonical_hash(meta["_body"])
                break


def _default_scanner(scanner_id: str, screened_count: int) -> dict[str, Any]:
    # A default/fallback payload is schema-shaped absence of model evidence.
    # It can never represent actual scanner execution.
    return {
        "scanner_id": scanner_id,
        "scanner_source_sha256": V8_SCANNERS[scanner_id]["sha256"],
        "execution_status": "PARTIAL",
        "screened_count": screened_count,
        "candidates": [],
        "systemic_unknowns": [],
        "search_expansion_questions": [],
        "grade_authority": False,
    }


def _sentinel_sample(results: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        sid = str(result.get("scanner_id") or "")
        for item in result.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            if item.get("recommended_discovery_action") in {"WATCH_STAGE0", "WATCH_RESET", "EXCLUDE", "DEEP_DIVE_SECONDARY"} or item.get("signal_strength") in {"WEAK", "UNKNOWN"}:
                rows.append({"scanner_id": sid, **item})
    rows.sort(key=lambda x: canonical_hash({"sid": x.get("security_id"), "scanner": x.get("scanner_id"), "action": x.get("recommended_discovery_action")}))
    return rows[:limit]


def _latest_funnel(store: Any, run_id: str, stage: str) -> dict[str, Any] | None:
    for row in reversed(store.list_funnel(run_id)):
        if str(row.get("funnel_stage") or "") != stage:
            continue
        try:
            value = json.loads(row.get("details_json") or "{}")
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    return None


def _write_terminal(store: Any, run_id: str, outcome: str) -> None:
    with store.transaction() as db:
        db.execute("UPDATE runs SET status='FAILED', outcome=? WHERE run_id=?", (outcome, run_id))


_INSTALLED = False


def install_v8_main_discovery_coach() -> type:
    global _INSTALLED
    current = runtime_module.ProductionStockAgent
    if _INSTALLED or getattr(current, "v8_main_discovery_coach_version", None) == V8_MAIN_DISCOVERY_COACH_VERSION:
        return current

    class V8MainDiscoveryCoachProductionStockAgent(current):  # type: ignore[misc,valid-type]
        v8_main_discovery_coach_version = V8_MAIN_DISCOVERY_COACH_VERSION
        v8_main_forensic_audit_sha256 = V8_MAIN_FORENSIC_AUDIT_SHA256

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            _install_prompts(self.prompts)
            self._v8_main_discovery_state: dict[str, dict[str, Any]] = {}

        def _profile_for_stage(self, stage: str) -> str:
            if stage.startswith("V8_MAIN_SCANNER_") or stage == "V8_MAIN_REJECTION_SENTINEL":
                if "DEEP_REASONING" in self.router.profiles:
                    return "DEEP_REASONING"
            return super()._profile_for_stage(stage)

        def _work_stage(self, run, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
            if stage != "STOCK_DISCOVERY" or prompt_id != "workflow.stock_scout":
                return super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)

            raw = copy.deepcopy(payload.get("raw_input") or {}) if isinstance(payload, dict) else {}
            universe = [row for row in (raw.get("universe") or []) if isinstance(row, dict)]
            eligible_ids = sorted({str(row.get("security_id") or row.get("ticker") or "").upper() for row in universe if str(row.get("security_id") or row.get("ticker") or "").strip()})
            screened_count = len(eligible_ids)
            scanner_results: list[dict[str, Any]] = []
            receipts: dict[str, dict[str, Any]] = {}

            for scanner_id in sorted(V8_SCANNERS):
                stage_id = f"V8_MAIN_SCANNER_{scanner_id}"
                result = super()._work_stage(
                    run,
                    stage_id,
                    f"v8_main.discovery_{scanner_id}",
                    {
                        "raw_input": {
                            "scanner_id": scanner_id,
                            "candidate_universe_packet": universe,
                            "technical_feature_snapshot": raw.get("technical_features") or {},
                            "approved_sector_context": raw.get("sector_analysis") or raw.get("approved_sector_context") or {},
                            "industry_driver_snapshot": raw.get("industry_driver_snapshot") or {},
                            "market_context": raw.get("market_analysis") or {},
                            "recall_contract": {"minimum_unique": V8_MAIN_MIN_UNIQUE, "preferred_unique": V8_MAIN_PREFERRED_UNIQUE},
                        },
                        "default_payload": _default_scanner(scanner_id, screened_count),
                    },
                    None,
                    dependency_ids,
                    {},
                )
                result = dict(result)
                contract_complete = (
                    result.get("scanner_id") == scanner_id
                    and result.get("scanner_source_sha256") == V8_SCANNERS[scanner_id]["sha256"]
                    and result.get("execution_status") == "COMPLETE"
                    and int(result.get("screened_count") or 0) == screened_count
                    and result.get("grade_authority") is False
                )
                integrity_state = getattr(self, "_v8_integrity_state", {}).get(run.run_id) or {}
                authoritative = (integrity_state.get("scanners") or {}).get(scanner_id) or {}
                actual_execution = bool(
                    authoritative.get("execution_status") == "SIGNAL_SCAN_COMPLETE"
                    and authoritative.get("output_validated") is True
                )
                complete = bool(contract_complete and actual_execution)
                receipt = {
                    "scanner_id": scanner_id,
                    "scanner_name": V8_SCANNERS[scanner_id]["name"],
                    "scanner_source_sha256": V8_SCANNERS[scanner_id]["sha256"],
                    "model_call_executed": actual_execution,
                    "execution_complete": complete,
                    "universe_seen": screened_count,
                    "model_screened_count": int(result.get("screened_count") or 0),
                    "candidate_count": len(result.get("candidates") or []),
                    "secondary_count": sum(1 for item in (result.get("candidates") or []) if isinstance(item, dict) and item.get("recommended_discovery_action") == "DEEP_DIVE_SECONDARY"),
                    "unknown_retained_count": sum(1 for item in (result.get("candidates") or []) if isinstance(item, dict) and item.get("unknowns")),
                    "lane_touched_is_scanner_executed": False,
                    "grade_authority": False,
                }
                receipts[scanner_id] = receipt
                scanner_results.append(result)
                self.store.record_funnel(run.run_id, f"V8_MAIN_SCANNER_{scanner_id}_RECEIPT", screened_count, receipt)

            sample = _sentinel_sample(scanner_results)
            sentinel = {"status": "COMPLETE", "audits": [], "systematic_false_negative_risk": False, "grade_authority": False}
            if sample:
                sentinel = super()._work_stage(
                    run,
                    "V8_MAIN_REJECTION_SENTINEL",
                    "v8_main.discovery_rejection_sentinel",
                    {"raw_input": {"sample": sample, "scanner_receipts": receipts}, "default_payload": sentinel},
                    None,
                    dependency_ids,
                    {},
                )
            sentinel_complete = str(sentinel.get("status") or "") == "COMPLETE" and sentinel.get("grade_authority") is False
            self.store.record_funnel(run.run_id, "V8_MAIN_REJECTION_SENTINEL", len(sample), {
                "sample_size": len(sample),
                "complete": sentinel_complete,
                "systematic_false_negative_risk": bool(sentinel.get("systematic_false_negative_risk")),
                "grade_authority": False,
            })

            enriched = copy.deepcopy(payload)
            enriched_raw = copy.deepcopy(raw)
            enriched_raw["v8_main_scanner_results"] = scanner_results
            enriched_raw["v8_main_scanner_receipts"] = receipts
            enriched_raw["v8_main_rejection_sentinel"] = sentinel
            enriched_raw["v8_main_common_contract"] = {
                "main_is_sole_discovery_owner": True,
                "python_scanner_routing_authority": False,
                "scanner_model_calls_required": sorted(V8_SCANNERS),
                "minimum_unique": V8_MAIN_MIN_UNIQUE,
                "preferred_unique": V8_MAIN_PREFERRED_UNIQUE,
                "unknown_is_fail": False,
                "discovery_grade_authority": False,
            }
            enriched["raw_input"] = enriched_raw
            final = super()._work_stage(run, stage, prompt_id, enriched, subject_id, dependency_ids, context_inputs)

            final_ids = {str(item.get("security_id") or "").upper() for item in (final.get("candidates") or []) if isinstance(item, dict)} if isinstance(final, dict) else set()
            high_value = set()
            for result in scanner_results:
                for item in result.get("candidates") or []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("research_value") == "HIGH" and item.get("recommended_discovery_action") in {"DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY"}:
                        high_value.add(str(item.get("security_id") or "").upper())
            unresolved_high = sorted(sid for sid in high_value if sid and sid not in final_ids)
            scanner_complete = len(receipts) == len(V8_SCANNERS) and all(bool(x.get("execution_complete")) for x in receipts.values())
            coverage = {
                "version": V8_MAIN_DISCOVERY_COACH_VERSION,
                "main_is_sole_discovery_owner": True,
                "python_scanner_routing_authority": False,
                "strategy_eligible_unique": screened_count,
                "mandatory_scanners": sorted(V8_SCANNERS),
                "scanner_execution_complete": scanner_complete,
                "sentinel_complete": sentinel_complete,
                "systematic_false_negative_risk": bool(sentinel.get("systematic_false_negative_risk")),
                "high_research_value_names": len(high_value),
                "unresolved_high_research_value_near_miss": unresolved_high[:100],
                "minimum_unique": V8_MAIN_MIN_UNIQUE,
                "preferred_unique": V8_MAIN_PREFERRED_UNIQUE,
                "grade_authority": False,
            }
            self.store.record_funnel(run.run_id, "V8_MAIN_DISCOVERY_COVERAGE", screened_count, coverage)
            self._v8_main_discovery_state[run.run_id] = coverage
            return final

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            outcome = super()._run_strict(mode, data)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if not run_id or run_id == "unstarted":
                return outcome
            state = self._v8_main_discovery_state.get(run_id)
            if not isinstance(state, dict):
                # If a broad run never reached MAIN discovery, preserve the
                # upstream failure. This coach never manufactures NO_TRADE.
                return outcome
            unresolved = list(state.get("unresolved_high_research_value_near_miss") or [])
            stop_allowed = (
                int(state.get("strategy_eligible_unique") or 0) >= V8_MAIN_MIN_UNIQUE
                and state.get("scanner_execution_complete") is True
                and state.get("sentinel_complete") is True
                and state.get("systematic_false_negative_risk") is False
                and not unresolved
            )
            audit = {
                "search_stop_allowed": stop_allowed,
                "reason": "MAIN_V8_RECALL_COMPLETE" if stop_allowed else "MAIN_V8_SEARCH_DEBT_REMAINS",
                "scanner_execution_complete": state.get("scanner_execution_complete"),
                "sentinel_complete": state.get("sentinel_complete"),
                "high_research_value_near_miss": len(unresolved),
                "strategy_eligible_unique": state.get("strategy_eligible_unique"),
                "deep_dive_yield_zero_alone_proves_exhaustion": False,
                "grade_authority": False,
            }
            self.store.record_funnel(run_id, "DISCOVERY_SEARCH_STOP_AUDIT", int(state.get("strategy_eligible_unique") or 0), audit)
            current_outcome = str(getattr(outcome, "outcome", "") or "")
            if current_outcome in {"NO_QUALIFIED_CANDIDATE", "NOT_EVALUABLE_DISCOVERY_COVERAGE", "NO_TRADE"} and not stop_allowed:
                terminal = "NOT_EVALUABLE_MAIN_V8_SEARCH_DEBT"
                reason = f"MAIN V8 Discovery incomplete: coverage={state.get('strategy_eligible_unique')}, scanners={state.get('scanner_execution_complete')}, sentinel={state.get('sentinel_complete')}, unresolved_high={len(unresolved)}"
                _write_terminal(self.store, run_id, terminal)
                return replace(outcome, outcome=terminal, blocked_reason=reason)
            return outcome

    runtime_module.ProductionStockAgent = V8MainDiscoveryCoachProductionStockAgent
    _INSTALLED = True
    return V8MainDiscoveryCoachProductionStockAgent
