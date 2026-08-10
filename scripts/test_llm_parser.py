from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stock_agent.command_parser import CommandInterpreter, hermes_llm_parser
from stock_agent.config import load_config


request = CommandInterpreter(hermes_llm_parser(load_config())).parse(
    "양자컴퓨팅 분야에서 한 종목을 골라 한 달 관점으로 검토해줘"
)
print(json.dumps({
    "intent": request.intent,
    "tickers": request.tickers,
    "parser_type": request.parser_type,
    "confidence": request.parser_confidence,
    "missing_fields": request.missing_fields,
    "status": request.status,
}, ensure_ascii=False, indent=2))
