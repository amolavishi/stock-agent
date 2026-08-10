from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .command_parser import CommandInterpreter, hermes_llm_parser
from .dispatcher import ClarificationManager, RequestDispatcher, TriggerPolicy
from .security import redact_secrets
from .schemas import UserRequest


class DiscordRuntimeError(RuntimeError):
    pass


def should_process_user(is_bot: bool) -> bool:
    return not is_bot


class DiscordRESTBot:
    """Output-only identity with bounded publish-only retries."""

    def __init__(self, token: str, channel_id: str, timeout: float = 20):
        if not token or not channel_id:
            raise DiscordRuntimeError("Discord bot token/channel ID missing")
        self.token, self.channel_id, self.timeout = token, channel_id, timeout

    @property
    def url(self) -> str:
        return f"https://discord.com/api/v10/channels/{self.channel_id}/messages"

    def send(self, content: str) -> None:
        for chunk in [content[i:i + 1900] for i in range(0, len(content), 1900)] or [""]:
            self._post(json_payload={"content": chunk})

    def send_file(self, path: str | Path, content: str = "") -> None:
        source = Path(path)
        if not source.is_file():
            raise DiscordRuntimeError("report attachment does not exist")
        self._post(data={"payload_json": json.dumps({"content": content}, ensure_ascii=False)},
                   files={"files[0]": (source.name, source.read_bytes(), "text/markdown")})

    def _post(self, json_payload=None, data=None, files=None) -> None:
        headers = {"Authorization": f"Bot {self.token}", "User-Agent": "stock-agent/0.6"}
        error = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(self.url, headers=headers, json=json_payload, data=data, files=files)
                if response.status_code in (200, 201):
                    return
                if response.status_code not in (429, 500, 502, 503, 504):
                    raise DiscordRuntimeError(f"Discord HTTP {response.status_code}")
                error = f"Discord HTTP {response.status_code}"
            except (httpx.HTTPError, TimeoutError) as exc:
                error = redact_secrets(exc)
            if attempt < 2:
                time.sleep(2 ** attempt)
        raise DiscordRuntimeError(f"Discord publish failed after retries: {error}")


class DiscordPresenters:
    def __init__(self, research: DiscordRESTBot, critic: DiscordRESTBot,
                 chairman: DiscordRESTBot, database=None):
        self.research, self.critic, self.chairman = research, critic, chairman
        self.database = database

    def publish_progress(self, stage: str, run_id: str, ticker: str, payload: Any) -> None:
        payload = payload or {}
        if stage == "RESEARCH_COMPLETED":
            output = payload.get("output", {})
            self.research.send(
                f"**[Research | Round {payload.get('round')} | {run_id}]**\n"
                f"Ticker: `{ticker}`\n결론: `{output.get('suggested_decision')}` · "
                f"Confidence {output.get('confidence')}/100\n\n**핵심 Thesis**\n" +
                "\n".join(f"- {item}" for item in output.get("bull_case", [])[:5]) +
                "\n\n**Evidence**\n" + ", ".join(output.get("evidence_ids", [])))
        elif stage == "CRITIC_COMPLETED":
            output = payload.get("output", {})
            failures = output.get("failure_scenarios", [])[:5]
            self.critic.send(
                f"**[Critic | Round {payload.get('round')} | {run_id}]**\n"
                f"Ticker: `{ticker}`\n판정: `{output.get('verdict')}` / "
                f"`{output.get('critic_decision')}`\n\n**실패 시나리오**\n" +
                "\n".join(f"- {item.get('scenario', item)}" for item in failures) +
                f"\n\n추가 Evidence 필요: `{output.get('need_more_evidence', False)}`")
        elif stage == "EVIDENCE_REFRESH":
            self.critic.send(f"**[EvidenceRequest | {run_id}]**\n" + json.dumps(payload, ensure_ascii=False))
        elif stage == "DEBATE_ROUND_COMPLETED":
            self.critic.send(
                f"**[토론 라운드 완료 | {run_id}]**\n"
                f"종목: `{ticker}` · Round: `{payload.get('round')}` · "
                f"상태: `{payload.get('status', 'IN_PROGRESS')}` · "
                f"미해결 핵심 이슈: `{payload.get('critical_open_issue_count', 0)}`")

    def publish_final(self, result: dict[str, Any]) -> None:
        certification = result.get("certification")
        if certification is not None and not certification.certified:
            mention = ("" if result["request"].discord_user_id == "CLI"
                       else f"<@{result['request'].discord_user_id}> ")
            reasons = ", ".join(certification.reason_codes[:5]) or "UNSPECIFIED_BLOCKER"
            summary = mention + (
                f"**[분석 인증 차단 | {result['market'].ticker} | {result['run_id']}]**\n"
                f"Action: **{certification.action}**\n"
                f"Execution: `{certification.execution_status}` · "
                f"Analysis: `{certification.analysis_status}` · "
                f"Certification: `{certification.certification_status}`\n"
                "TradePlan: `WITHHELD` · PositionSizing: `WITHHELD`\n"
                f"Reason: {reasons}\n\n"
                "> 투자판단이 아니라 무결성 진단 결과입니다. PAPER 계정은 변경되지 않았습니다.")
            try:
                self.chairman.send_file(result["report_path"], summary)
                self._mark_published(result["run_id"], "PUBLISHED", "")
            except Exception as exc:
                self._mark_published(result["run_id"], "FAILED", redact_secrets(exc))
                raise
            return
        decision, risk, plan = result["decision"], result["risk"], result["decision"].trade_plan
        size = result["position_size"]
        mention = "" if result["request"].discord_user_id == "CLI" else f"<@{result['request'].discord_user_id}> "
        summary = mention + (
            f"**[최종 투자심의 | {decision.ticker} | {result['run_id']}]**\n"
            f"Decision: **{decision.decision}** · Confidence: **{decision.confidence}/100**\n"
            f"Time Horizon: `{result['request'].time_horizon}` · Market Regime: `{result['market_regime']}`\n"
            f"Entry `${plan.preferred_price_min:.2f}–${plan.preferred_price_max:.2f}` · "
            f"Stop `${plan.stop_price:.2f}` · Targets `${plan.target_1:.2f}/${plan.target_2:.2f}`\n"
            f"Position Size (PAPER): `{size.quantity}`주 · Risk: `{'PASS' if risk.hard_filter_pass else 'BLOCK'}`\n"
            f"Run Status: `SUCCESS`\n\n> PAPER 리서치이며 실제 주문·투자 권유가 아닙니다.")
        try:
            self.chairman.send_file(result["report_path"], summary)
            self._mark_published(result["run_id"], "PUBLISHED", "")
        except Exception as exc:
            self._mark_published(result["run_id"], "FAILED", redact_secrets(exc))
            raise

    def publish_comparison(self, comparison: dict[str, Any]) -> None:
        lines = [f"**[비교 최종심의 | {comparison['run_id']}]**",
                 f"Preference: **{comparison['preference']}**"]
        for item in comparison["results"]:
            decision = item["decision"]
            lines.append(f"- {decision.ticker}: `{decision.decision}` ({decision.confidence}/100)")
        lines.append("\n> 둘 다 부적합하면 Preference는 NONE입니다. PAPER 전용입니다.")
        try:
            self.chairman.send_file(comparison["report_path"], "\n".join(lines))
            self._mark_published(comparison["run_id"], "PUBLISHED", "")
        except Exception as exc:
            self._mark_published(comparison["run_id"], "FAILED", redact_secrets(exc))
            raise

    def publish_error(self, run_id: str, status: str, error: str) -> None:
        self.chairman.send(f"**[분석 미완료]**\nRun: `{run_id}`\nStatus: `{status}`\n{redact_secrets(error)}")

    def _mark_published(self, run_id: str, status: str, error: str) -> None:
        if not self.database:
            return
        with self.database.connect() as connection:
            connection.execute("""UPDATE report_artifacts SET publish_status=?,
                publish_attempts=publish_attempts+1,last_error=?,
                delivered_at=CASE WHEN ?='PUBLISHED' THEN datetime('now') ELSE delivered_at END
                WHERE run_id=?""", (status, error, status, run_id))
            connection.execute("""UPDATE analysis_runs SET delivery_status=?,
                delivered_at=CASE WHEN ?='PUBLISHED' THEN datetime('now') ELSE delivered_at END
                WHERE run_id=?""", (status, status, run_id))
        self.database.mark_outbox_event(run_id, "REPORT_READY", status == "PUBLISHED", error)


class NaturalLanguageDiscordRuntime:
    def __init__(self, config: dict[str, Any], orchestrator):
        self.config = config
        self.orchestrator = orchestrator
        credentials = config["credentials"]
        allowed = {value.strip() for value in credentials.get("discord_allowed_user_ids", "").split(",")
                   if value.strip()}
        if credentials.get("discord_owner_user_id"):
            allowed.add(credentials["discord_owner_user_id"])
        command_channel = credentials.get("discord_command_channel_id", "")
        if not credentials.get("discord_guild_id") or not command_channel or not allowed:
            raise DiscordRuntimeError("Discord guild, command channel, and owner/allowed user IDs are required")
        self.policy = TriggerPolicy(credentials["discord_guild_id"], command_channel, allowed)
        self.interpreter = CommandInterpreter(hermes_llm_parser(config, orchestrator.db))
        self.clarifications = ClarificationManager(
            orchestrator.db, config.get("clarification_timeout_minutes", 20))
        self.presenters = DiscordPresenters(
            DiscordRESTBot(credentials["discord_research_token"], credentials["discord_debate_channel_id"]),
            DiscordRESTBot(credentials["discord_critic_token"], credentials["discord_debate_channel_id"]),
            DiscordRESTBot(credentials["discord_chairman_token"], credentials["discord_report_channel_id"]),
            orchestrator.db,
        )
        self.dispatcher = RequestDispatcher(orchestrator, self.presenters)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active_keys: set[str] = set()

    @staticmethod
    def _key(request) -> str:
        return f"{request.intent}:{','.join(sorted(request.tickers))}"

    async def submit(self, text: str, message_id: str, user_id: str, channel, guild_id: str,
                     is_bot: bool = False) -> str:
        accepted, reason = self.policy.evaluate(guild_id, str(channel.id), user_id, is_bot, text)
        if not accepted:
            return reason
        provisional = str(uuid.uuid4())
        if not self.orchestrator.db.mark_discord_message(message_id, user_id, str(channel.id), provisional):
            return "DUPLICATE_MESSAGE"
        pending_id, prior = self.clarifications.prior_text(user_id, str(channel.id))
        request = await asyncio.to_thread(
            self.interpreter.parse, text, message_id, user_id, prior)
        if request.status == "WAITING_CLARIFICATION":
            self.clarifications.create(request, str(channel.id))
            await channel.send(self._clarification_text(request))
            return "WAITING_CLARIFICATION"
        self.clarifications.resolve(pending_id)
        self.orchestrator.db.save_user_request(request)
        if request.intent == "CANCEL":
            run_ids = self.orchestrator.db.request_cancellation_for_tickers(request.tickers)
            await channel.send(
                "취소 요청을 기록했습니다. 대기 작업은 취소하고 실행 중 작업은 다음 안전 종료 지점에서 중단합니다."
                + (f"\n대상 Run: `{', '.join(run_ids)}`" if run_ids else ""))
            return "CANCEL_REQUESTED"
        if request.intent not in {"ANALYZE", "COMPARE", "REANALYZE"}:
            result = await asyncio.to_thread(self.dispatcher.execute, request)
            if result["kind"] == "REPORT" and result.get("path") and Path(result["path"]).is_file():
                await channel.send(result["text"], file=self._discord.File(result["path"]))
            else:
                await channel.send(result.get("text", "완료"))
            return "COMPLETED"
        key = self._key(request)
        if key in self.active_keys:
            await channel.send(f"동일 요청이 이미 실행 또는 대기 중입니다: `{key}`")
            return "DUPLICATE_RUN"
        self.active_keys.add(key)
        request.status = "QUEUED"
        self.orchestrator.db.update_request_status(request.request_id, "QUEUED")
        job_id = self.orchestrator.db.enqueue_job(request)
        await self.queue.put((request, channel, key, job_id))
        await channel.send(
            f"**[접수 완료]**\n요청: `{request.intent}` · 대상: `{', '.join(request.tickers) or '없음'}`\n"
            f"기간: `{request.time_horizon}` · 대기열: `{self.queue.qsize()}`")
        return "QUEUED"

    async def worker(self) -> None:
        while True:
            request, channel, key, job_id = await self.queue.get()
            try:
                if self.orchestrator.db.is_job_cancelled(job_id):
                    await channel.send(f"대기 Run 취소 완료: `{', '.join(request.tickers)}`")
                    continue
                if not self.orchestrator.db.start_job(job_id):
                    await channel.send(f"이미 다른 worker가 처리 중인 작업입니다: `{job_id}`")
                    continue
                result = await asyncio.to_thread(self.dispatcher.execute, request)
                result_run = result.get("result", {}).get("run_id", "")
                self.orchestrator.db.heartbeat_job(job_id, result_run)
                self.orchestrator.db.finish_job(job_id, "COMPLETED")
                if result["kind"] == "TEXT":
                    await channel.send(result["text"])
                elif result["kind"] == "REPORT":
                    if result.get("path") and Path(result["path"]).is_file():
                        await channel.send(result["text"], file=self._discord.File(result["path"]))
                    else:
                        await channel.send(result["text"])
                elif result["kind"] == "CANCEL":
                    await channel.send(result["text"])
                else:
                    await channel.send("처리가 완료되었습니다. 결과는 보고서제출 채널을 확인해 주세요.")
            except Exception as exc:
                safe = redact_secrets(exc)
                status = "CANCELLED" if "cancel" in str(exc).lower() else "FAILED"
                self.orchestrator.db.finish_job(job_id, status, safe)
                await channel.send(f"요청 처리 실패: {safe}")
            finally:
                self.active_keys.discard(key)
                self.queue.task_done()

    async def recover_jobs(self, client) -> int:
        channel = client.get_channel(int(self.config["credentials"]["discord_command_channel_id"]))
        if channel is None:
            return 0
        count = 0
        for row in self.orchestrator.db.recoverable_jobs():
            request = UserRequest(**row["payload"])
            key = self._key(request)
            if key in self.active_keys:
                continue
            self.active_keys.add(key)
            await self.queue.put((request, channel, key, row["job_id"]))
            count += 1
        if count:
            await channel.send(f"재시작 후 대기 작업 `{count}`개를 복구했습니다.")
        return count

    @staticmethod
    def _clarification_text(request) -> str:
        if "tickers" in request.missing_fields or "comparison_tickers" in request.missing_fields:
            return ("분석 대상을 구체적으로 알려주세요. 예: `IONQ`, `IONQ와 SOUN 비교`\n"
                    "양자 후보: IONQ / RGTI / QBTS / QUBT")
        if "analysis_intensity" in request.missing_fields:
            return ("분석 강도를 선택해 주세요.\n\n"
                    "`최소` · 빠른 검증, 2~3 Round\n"
                    "`보통` · 표준 정밀 분석, 3~5 Round\n"
                    "`최대` · 심층 검증, 5~10 Round + 증거 재조사 + Stress Test")
        return "요청 의도를 더 구체적으로 알려주세요: 분석 / 비교 / 가격 / 포트폴리오 / 보고서 / 상태"

    def run(self) -> None:
        try:
            import discord
            from discord import app_commands
        except ImportError as exc:
            raise DiscordRuntimeError("discord.py is required") from exc
        self._discord = discord
        credentials = self.config["credentials"]
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        tree = app_commands.CommandTree(client)
        guild = discord.Object(id=int(credentials["discord_guild_id"]))

        @client.event
        async def on_ready():
            await tree.sync(guild=guild)
            if not hasattr(client, "_stock_worker"):
                client._stock_worker = asyncio.create_task(self.worker())
                await self.recover_jobs(client)
            print(f"stock-agent Discord ready: {client.user} command_channel="
                  f"{credentials['discord_command_channel_id']}", flush=True)

        @client.event
        async def on_message(message):
            if message.guild is None:
                return
            await self.submit(message.content, str(message.id), str(message.author.id),
                              message.channel, str(message.guild.id), message.author.bot)

        async def slash_submit(interaction, text: str):
            await interaction.response.defer(ephemeral=True, thinking=True)
            status = await self.submit(text, str(interaction.id), str(interaction.user.id),
                                       interaction.channel, str(interaction.guild_id), False)
            await interaction.followup.send(f"요청 상태: `{status}`", ephemeral=True)

        @tree.command(name="analyze", description="PAPER 종목 분석", guild=guild)
        async def analyze(interaction, ticker: str):
            await slash_submit(interaction, f"{ticker} 분석")

        @tree.command(name="price", description="Toss 현재가", guild=guild)
        async def price(interaction, ticker: str):
            await slash_submit(interaction, f"{ticker} 가격")

        @tree.command(name="status", description="현재 Run 상태", guild=guild)
        async def status(interaction):
            await slash_submit(interaction, "현재 상태")

        client.run(credentials["discord_chairman_token"], log_handler=None)


def run_chairman_bot(config: dict[str, Any], orchestrator) -> None:
    NaturalLanguageDiscordRuntime(config, orchestrator).run()
