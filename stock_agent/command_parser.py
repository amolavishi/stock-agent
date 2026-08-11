from __future__ import annotations

import re
import uuid
from dataclasses import fields
from datetime import datetime, timezone
from typing import Any, Callable

from .schemas import Intent, RequestStatus, UserRequest
from .validation import validate_ticker


ALIASES = {
    "아이온큐": "IONQ", "아이온 큐": "IONQ", "사운드하운드": "SOUN",
    "사운드 하운드": "SOUN", "듀오링고": "DUOL", "듀오 링고": "DUOL",
    "리게티": "RGTI", "디웨이브": "QBTS", "퀀텀컴퓨팅": "QUBT",
}
ALIASES.update({
    "아이온큐": "IONQ", "사운드하운드": "SOUN", "사운드 하운드": "SOUN",
    "듀오링고": "DUOL", "그로기 듀오링고": "DUOL", "리게티": "RGTI",
})
KNOWN = {"IONQ", "SOUN", "DUOL", "RGTI", "QBTS", "QUBT", "AAPL", "MSFT", "NVDA", "TSLA"}
RESERVED_SYMBOLS = {"ATM", "SEC", "API", "BUY", "SELL", "WAIT", "EXCLUDE", "PRICE",
                    "REPORT", "STATUS", "CANCEL", "HELP", "NORMAL", "MINIMUM", "MAXIMUM"}

INTENSITY_POLICIES = {
    "MINIMUM": {"min_debate_rounds": 2, "max_debate_rounds": 3,
                "reasoning_profile": "low", "evidence_depth": "CORE",
                "max_evidence_refreshes": 1, "consensus_stress_test_required": False},
    "NORMAL": {"min_debate_rounds": 3, "max_debate_rounds": 5,
               "reasoning_profile": "high", "evidence_depth": "STANDARD",
               "max_evidence_refreshes": 2, "consensus_stress_test_required": False},
    "MAXIMUM": {"min_debate_rounds": 5, "max_debate_rounds": 10,
                "reasoning_profile": "max", "evidence_depth": "DEEP",
                "max_evidence_refreshes": 3, "consensus_stress_test_required": True},
}


class CommandParseError(ValueError):
    pass


class CommandInterpreter:
    """Intent-only parser. It never makes an investment judgment."""

    def __init__(self, llm_parser: Callable[[str], dict[str, Any]] | None = None):
        self.llm_parser = llm_parser

    def parse(self, text: str, message_id: str = "", user_id: str = "",
              prior_text: str = "") -> UserRequest:
        request_id = str(uuid.uuid4())
        original = text.strip()
        if not original:
            raise CommandParseError("empty command")
        combined = f"{prior_text}\n{original}".strip() if prior_text else original
        lightweight = self._lightweight(combined)
        if lightweight is None and self.llm_parser:
            setter = getattr(self.llm_parser, "set_context", None)
            if setter:
                setter(request_id=request_id, repair_attempt=False)
            try:
                lightweight = self._validated_llm(self.llm_parser(combined))
            except (CommandParseError, TypeError, ValueError):
                if setter:
                    setter(request_id=request_id, repair_attempt=True)
                lightweight = self._validated_llm(self.llm_parser(
                    combined + "\n\nJSON_REPAIR: Return one complete valid UserRequest JSON object only."))
        if lightweight is None:
            lightweight = {"intent": Intent.HELP.value, "tickers": [], "confidence": 0.4,
                           "missing_fields": ["intent"]}
        intent = lightweight["intent"]
        tickers = lightweight.get("tickers", [])
        required_ticker = intent in {Intent.ANALYZE.value, Intent.COMPARE.value,
            Intent.REANALYZE.value, Intent.PRICE.value, Intent.REPORT.value,
            Intent.CANCEL.value, Intent.PAPER_BUY.value, Intent.PAPER_SELL.value,
            Intent.PAPER_TRIM.value}
        missing = list(lightweight.get("missing_fields", []))
        if required_ticker and not tickers:
            missing.append("tickers")
        if intent == Intent.COMPARE.value and len(tickers) < 2:
            missing.append("comparison_tickers")
        intensity = str(lightweight.get("analysis_intensity") or "NORMAL")
        intensity_explicit = bool(lightweight.get("intensity_explicit", False))
        if intent in {Intent.ANALYZE.value, Intent.COMPARE.value, Intent.REANALYZE.value} and not intensity_explicit:
            missing.append("analysis_intensity")
        policy = INTENSITY_POLICIES.get(intensity, INTENSITY_POLICIES["NORMAL"])
        confidence = float(lightweight.get("confidence", 1.0))
        status = (RequestStatus.WAITING_CLARIFICATION.value
                  if confidence < 0.85 or missing else RequestStatus.PARSED.value)
        return UserRequest(
            request_id=request_id, discord_message_id=message_id,
            discord_user_id=user_id, received_at=datetime.now(timezone.utc).isoformat(),
            original_text=combined, intent=intent, tickers=tickers,
            time_horizon=lightweight.get("time_horizon", "1-2M"),
            focus=lightweight.get("focus", []),
            comparison_mode=lightweight.get("comparison_mode", "NONE"),
            use_prior_analysis=bool(lightweight.get("use_prior_analysis", intent == "REANALYZE")),
            need_debate=intent in {"ANALYZE", "COMPARE", "REANALYZE"},
            need_report=intent in {"ANALYZE", "COMPARE", "REANALYZE", "REPORT"},
            parser_type=lightweight.get("parser_type", "LIGHTWEIGHT"),
            parser_confidence=confidence, missing_fields=sorted(set(missing)), status=status,
            analysis_intensity=intensity, intensity_explicit=intensity_explicit,
            paper_action_enabled=intent in {"PAPER_BUY", "PAPER_SELL", "PAPER_TRIM"},
            requested_sector=lightweight.get("requested_sector", ""),
            discovery_mode=lightweight.get("discovery_mode", ""),
            shadow=bool(lightweight.get("shadow", True)),
            discovery_run_id=str(lightweight.get("discovery_run_id", "") or ""),
            promotion_limit=int(lightweight.get("promotion_limit", 0) or 0), **policy)

    def _lightweight(self, text: str) -> dict[str, Any] | None:
        explicit_symbols = [value for value in re.findall(
            r"(?<![A-Za-z0-9])[A-Z]{1,5}(?![A-Za-z0-9])", text)
            if value not in RESERVED_SYMBOLS]
        normalized = text.upper()
        for alias, ticker in ALIASES.items():
            normalized = normalized.replace(alias.upper(), f" {ticker} ")
        tickers = []
        for candidate in re.findall(r"(?<![A-Z0-9])[A-Z]{1,5}(?![A-Z0-9])", normalized):
            if (candidate in KNOWN or candidate in explicit_symbols) and candidate not in tickers:
                tickers.append(validate_ticker(candidate))
        requested_sector = ""
        if any(term in normalized for term in ("원전", "우라늄", "NUCLEAR", "URANIUM")):
            requested_sector = "Nuclear & Uranium"
        elif any(term in normalized for term in ("방산", "DEFENSE", "AEROSPACE")):
            requested_sector = "Defense"
        elif any(term in normalized for term in ("AI 데이터센터", "AI 병목", "AI BOTTLENECK", "DATACENTER")):
            requested_sector = "AI Bottleneck"
        discovery_report = any(term in normalized for term in ("DISCOVERY 결과", "DISCOVERY REPORT", "디스커버리 결과"))
        discovery_status = any(term in normalized for term in ("DISCOVERY 진행", "DISCOVERY STATUS", "디스커버리 진행"))
        discovery_cancel = any(term in normalized for term in ("DISCOVERY 취소", "DISCOVERY CANCEL", "디스커버리 취소"))
        discovery_deep = ("DISCOVERY DEEP" in normalized or "DISCOVERY_DEEP_HANDOFF" in normalized or
                          "DISCOVERY_PROMOTE" in normalized or
                          "방금 디스커버리 상위" in normalized or "디스커버리 상위" in normalized)
        discovery_run_id_match = re.search(r"DISC_[0-9]{8}_[0-9]{6}_[A-F0-9]{8}", normalized)
        discovery_run_id = discovery_run_id_match.group(0) if discovery_run_id_match else ""
        promotion_match = re.search(r"(?:TOP|상위)\s*([0-9]+)", normalized)
        promotion_limit = int(promotion_match.group(1)) if promotion_match else 0
        discovery_request = (requested_sector or any(term in normalized for term in
                            ("시장 전체", "미국 시장", "전체 훑", "유망주 찾아", "종목 찾아")))
        focus = []
        for keyword, label in (("실적", "earnings"), ("계약", "contracts"),
                               ("정부", "government_contracts"), ("ATM", "atm"),
                               ("희석", "dilution"), ("밸류", "valuation")):
            if keyword.upper() in normalized:
                focus.append(label)
        horizon = "1-2M"
        if re.search(r"한\s*달|1\s*개월|1M", normalized):
            horizon = "1M"
        elif re.search(r"1\s*[~-]\s*2\s*개월|1~2개월|두\s*달|2\s*개월", normalized):
            horizon = "1-2M"
        intensity = "NORMAL"
        intensity_explicit = False
        if any(value in normalized for value in ("최대 강도", "최대로", "최대", "MAXIMUM", "심층")):
            intensity, intensity_explicit = "MAXIMUM", True
        elif any(value in normalized for value in ("최소 강도", "최소로", "최소", "MINIMUM", "빠르게")):
            intensity, intensity_explicit = "MINIMUM", True
        elif any(value in normalized for value in ("보통 강도", "보통으로", "보통", "NORMAL", "표준")):
            intensity, intensity_explicit = "NORMAL", True
        intent = None
        paper_command = "PAPER" in normalized or "페이퍼" in normalized or "모의" in normalized
        if discovery_deep:
            intent = Intent.DISCOVERY_DEEP_HANDOFF.value
        elif discovery_report:
            intent = Intent.DISCOVERY_REPORT.value
        elif discovery_status:
            intent = Intent.DISCOVERY_STATUS.value
        elif discovery_cancel:
            intent = Intent.DISCOVERY_CANCEL.value
        elif discovery_request and requested_sector:
            intent = Intent.DISCOVER_SECTOR.value
        elif discovery_request:
            intent = Intent.DISCOVER_MARKET.value
        elif paper_command and "매수" in normalized:
            intent = "PAPER_BUY"
        elif paper_command and ("일부 매도" in normalized or "트림" in normalized):
            intent = "PAPER_TRIM"
        elif paper_command and "매도" in normalized:
            intent = "PAPER_SELL"
        elif tickers and "분석" in normalized:
            intent = "ANALYZE"
        if any(word in normalized for word in ("도움", "사용법", "HELP")):
            intent = "HELP"
        elif any(word in normalized for word in ("비용", "토큰", "COST", "USAGE")):
            intent = "COST"
        elif any(word in normalized for word in ("뭐 돌", "진행", "상태", "STATUS")):
            intent = "STATUS"
        elif any(word in normalized for word in ("취소", "중단", "CANCEL")):
            intent = "CANCEL"
        elif any(word in normalized for word in ("포트", "보유", "PORTFOLIO")):
            intent = "PORTFOLIO"
        elif any(word in normalized for word in ("보고서", "리포트", "REPORT")) and any(
                word in normalized for word in ("보여", "다시", "지난", "최신")):
            intent = "REPORT"
        elif any(word in normalized for word in ("가격", "얼마", "현재가", "PRICE")):
            intent = "PRICE"
        elif len(tickers) >= 2 and any(word in normalized for word in ("비교", "VS", "중", "하나만")):
            intent = "COMPARE"
        elif any(word in normalized for word in ("지난번", "이후", "달라진", "재분석", "다시 검토")) and tickers:
            intent = "REANALYZE"
        elif tickers and any(word in normalized for word in ("분석", "조사", "살", "들어가", "판단", "봐")):
            intent = "ANALYZE"
        # Discovery-specific intents are terminal and must not be shadowed by
        # the generic STATUS/REPORT/CANCEL lexicon above.
        if discovery_deep:
            intent = Intent.DISCOVERY_DEEP_HANDOFF.value
        elif discovery_report:
            intent = Intent.DISCOVERY_REPORT.value
        elif discovery_status:
            intent = Intent.DISCOVERY_STATUS.value
        elif discovery_cancel:
            intent = Intent.DISCOVERY_CANCEL.value
        elif discovery_request and requested_sector:
            intent = Intent.DISCOVER_SECTOR.value
        elif discovery_request:
            intent = Intent.DISCOVER_MARKET.value
        if not intent:
            return None
        return {"intent": intent, "tickers": tickers, "time_horizon": horizon,
                "focus": focus, "comparison_mode": "PICK_ONE" if intent == "COMPARE" else "NONE",
                "requested_sector": requested_sector,
                "discovery_mode": "SECTOR" if intent == Intent.DISCOVER_SECTOR.value else ("MARKET" if intent == Intent.DISCOVER_MARKET.value else ""),
                "confidence": 0.98 if (tickers or intent in {"STATUS", "COST", "PORTFOLIO", "HELP", Intent.DISCOVER_MARKET.value, Intent.DISCOVER_SECTOR.value, Intent.DISCOVERY_REPORT.value, Intent.DISCOVERY_STATUS.value, Intent.DISCOVERY_CANCEL.value, Intent.DISCOVERY_DEEP_HANDOFF.value}) else 0.7,
                "missing_fields": [], "parser_type": "LIGHTWEIGHT",
                "analysis_intensity": intensity, "intensity_explicit": intensity_explicit,
                "discovery_run_id": discovery_run_id, "promotion_limit": promotion_limit}

    @staticmethod
    def _validated_llm(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise CommandParseError("LLM parser returned non-object")
        intent = str(payload.get("intent", ""))
        if intent not in {item.value for item in Intent}:
            raise CommandParseError("LLM parser returned unsupported intent")
        tickers = [validate_ticker(value) for value in payload.get("tickers", [])]
        result = dict(payload)
        result["tickers"] = tickers
        result["parser_type"] = "LLM"
        return result


def hermes_llm_parser(config: dict[str, Any], database=None) -> Callable[[str], dict[str, Any]]:
    """Build a bounded Hermes/DeepSeek command-only parser."""
    from pathlib import Path
    from .hermes import HermesCLIAdapter, HermesHTTPAdapter, default_hermes_executable

    if config.get("hermes_transport") == "http":
        adapter = HermesHTTPAdapter(config["hermes_endpoint"], config["hermes_model"], timeout=60,
                                    usage_recorder=database.record_llm_call if database else None)
    else:
        adapter = HermesCLIAdapter(default_hermes_executable(), config["hermes_model"],
                                   timeout=config.get("hermes_parser_timeout_seconds", 90),
                                   usage_recorder=database.record_llm_call if database else None)
    template = (Path(__file__).resolve().parents[1] / "prompts" / "command_parser_v001.md").read_text(
        encoding="utf-8")

    class Parser:
        def __init__(self):
            self.context = {"request_id": "", "repair_attempt": False}

        def set_context(self, request_id: str, repair_attempt: bool = False) -> None:
            self.context = {"request_id": request_id, "repair_attempt": repair_attempt}

        def __call__(self, text: str) -> dict[str, Any]:
            request_id = self.context["request_id"]
            adapter.set_call_context(
                run_id=f"REQUEST_{request_id}", request_id=request_id, ticker="", round_no=0,
                phase="COMMAND_PARSER_REPAIR" if self.context["repair_attempt"] else "COMMAND_PARSER",
                reasoning_effort="minimal", repair_attempt=self.context["repair_attempt"])
            response = adapter.invoke_json(template + "\n\nUSER_TEXT:\n" + text, "command_parser")
            return response.data

    return Parser()
