"""V8 NEXT certification engine V1.1 hardening.

This patch keeps the original V8 NEXT writer source intact and tightens the
Python authority around grade thresholds and robustness.  It is installed
before production runtime wiring.
"""
from __future__ import annotations

from typing import Any

from . import v8_next_certification as cert

PATCH_VERSION = "V8_NEXT_CERTIFICATION_ENGINE_V1.1"
_INSTALLED = False


def _cap_grade(grade: str, maximum: str) -> str:
    order = {"EXCLUDE": -1, "B": 0, "B+": 1, "A-": 2, "A": 3}
    return maximum if order.get(grade, -99) > order.get(maximum, -99) else grade


def install_v8_next_certification_v11() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    base_finalize = cert.finalize_certification

    def finalize_certification_v11(
        draft: dict[str, Any],
        assumption_audit: dict[str, Any],
        atomic_audit: dict[str, Any],
        fd_bridge: dict[str, Any],
        packet: dict[str, Any],
        current_price: float | None,
        evidence_ids: list[str],
        policy_version: str,
        policy_hash: str,
    ) -> dict[str, Any]:
        result = base_finalize(
            draft, assumption_audit, atomic_audit, fd_bridge, packet,
            current_price, evidence_ids, policy_version, policy_hash,
        )
        normalized = float(result.get("normalized_score") or 0.0)
        grade = str(result.get("research_grade") or "EXCLUDE")
        caps = list(result.get("active_grade_caps") or [])

        # Canonical V8 threshold: <65 is EXCLUDE, not B.
        if normalized < 65.0:
            grade = "EXCLUDE"
            caps.append("NORMALIZED_SCORE_LT65_EXCLUDE")

        assumption_map = {
            str(item.get("assumption_id")): str(item.get("status") or "")
            for item in (assumption_audit.get("assumptions") or [])
            if isinstance(item, dict)
        }
        non_robust = [item for item in cert.ASSUMPTIONS if assumption_map.get(item) != "ROBUST"]
        if non_robust and grade in {"A", "A-"}:
            grade = "B+"
            caps.append("CRITICAL_ASSUMPTION_NOT_ROBUST_MAX_B_PLUS")

        claims = [item for item in (atomic_audit.get("atomic_claims") or []) if isinstance(item, dict)]
        if any(str(item.get("verification_status") or "") != "VERIFIED" for item in claims) and grade in {"A", "A-"}:
            grade = "B+"
            caps.append("ATOMIC_CLAIM_NOT_VERIFIED_MAX_B_PLUS")
        realization = atomic_audit.get("value_realization_bridge_1_8w") if isinstance(atomic_audit.get("value_realization_bridge_1_8w"), dict) else {}
        if str(realization.get("status") or "") != "ROBUST" and grade in {"A", "A-"}:
            grade = "B+"
            caps.append("REALIZATION_BRIDGE_NOT_ROBUST_MAX_B_PLUS")
        if str(atomic_audit.get("probability_provenance") or "") not in {"DATA_BACKED", "CALIBRATED_RANGE"} and grade in {"A", "A-"}:
            grade = "B+"
            caps.append("ATOMIC_PROBABILITY_PROVENANCE_MAX_B_PLUS")

        # A/A- requires a completely validated FD bridge, not merely a model
        # claim that dilution-adjusted economics pass.
        if (str(fd_bridge.get("status") or "") != "COMPLETE" or fd_bridge.get("validation_failures")) and grade in {"A", "A-"}:
            grade = "B+"
            caps.append("FD_SHARE_BRIDGE_NOT_ROBUST_MAX_B_PLUS")

        # Preserve stronger exclusions produced by the base engine.
        if str(result.get("research_grade")) == "EXCLUDE":
            grade = "EXCLUDE"

        result["research_grade"] = grade
        result["active_grade_caps"] = sorted(set(str(item) for item in caps))
        result["python_grade_engine"] = PATCH_VERSION
        if str(result.get("certification_status")) != "NOT_CERTIFIABLE":
            result["certification_status"] = {
                "A": "A_CERTIFIED",
                "A-": "A_MINUS_CERTIFIED",
                "B+": "B_PLUS_ONLY",
                "B": "B_ONLY",
                "EXCLUDE": "EXCLUDE",
            }[grade]
        if grade != "A" and not result.get("why_not_one_grade_higher"):
            result["why_not_one_grade_higher"] = list(result["active_grade_caps"]) or ["Certification robustness is insufficient."]
        return result

    cert.finalize_certification = finalize_certification_v11
    _INSTALLED = True
