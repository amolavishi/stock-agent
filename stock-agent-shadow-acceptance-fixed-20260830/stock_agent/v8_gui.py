"""Small operator GUI for the isolated V8 Challenger.

The GUI is intentionally a launcher around :mod:`stock_agent.v8_challenger`.
It never opens the Primary SQLite database and never exposes a broker action.
When the immutable Primary PIT inputs are absent, it writes a BLOCKED report
instead of trying to invent them.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlparse

from .v8_challenger import V8ArtifactStore, V8PromptBundle, _redact_secrets

BROKER_WRITE_COUNT = 0


def build_cli_command(
    bundle: str | Path,
    manifest: str | Path,
    candidates: str | Path,
    evidence: str | Path,
    output_root: str | Path,
    *,
    reasoning_effort: str = "medium",
) -> list[str]:
    """Build the canonical, read-only Challenger CLI command."""
    return [
        sys.executable,
        "-m",
        "stock_agent.v8_challenger",
        "--bundle",
        str(bundle),
        "--manifest",
        str(manifest),
        "--candidates",
        str(candidates),
        "--evidence",
        str(evidence),
        "--output-root",
        str(output_root),
        "--reasoning-effort",
        str(reasoning_effort),
    ]


def bundle_summary(path: str | Path) -> dict[str, Any]:
    bundle = V8PromptBundle.load(path)
    return {
        "source": str(bundle.source),
        "stage_count": len(bundle.stage_files),
        "phase1_stages": sorted(bundle.stage_files),
        "bundle_hash": bundle.bundle_hash,
    }


def _report_text(payload: Mapping[str, Any]) -> str:
    safe = _redact_secrets(dict(payload))
    lines = [
        "# V8 Challenger GUI Run Report",
        "",
        f"Status: {safe.get('status', 'UNKNOWN')}",
        f"Run ID: {safe.get('challenger_run_id') or 'NOT_RUN'}",
        f"Primary Run ID: {safe.get('primary_run_id') or 'UNKNOWN'}",
        f"Comparison As Of: {safe.get('comparison_as_of') or 'UNKNOWN'}",
        f"V8 Bundle Hash: {safe.get('bundle_hash') or 'UNKNOWN'}",
        "",
        "## Inputs",
        "",
        f"- Bundle: {safe.get('bundle') or 'UNKNOWN'}",
        f"- Manifest: {safe.get('manifest') or 'UNKNOWN'}",
        f"- Candidates: {safe.get('candidates') or 'UNKNOWN'}",
        f"- Evidence: {safe.get('evidence') or 'UNKNOWN'}",
        f"- Output root: {safe.get('output_root') or 'UNKNOWN'}",
        "",
        "## Result",
        "",
        f"- Candidate count: {safe.get('candidate_count', 0)}",
        f"- A certified: {safe.get('certified_a', 0)}",
        f"- A- certified: {safe.get('certified_a_minus', 0)}",
        f"- Broker writes: {safe.get('broker_write_count', BROKER_WRITE_COUNT)}",
        "",
        "## Errors",
        "",
    ]
    errors = safe.get("errors") or []
    if errors:
        lines.extend(f"- {error}" for error in errors)
    else:
        lines.append("- NONE")
    lines.extend(["", "## Artifacts", ""])
    artifacts = safe.get("artifacts") or {}
    if artifacts:
        lines.extend(f"- {key}: {value}" for key, value in sorted(artifacts.items()))
    else:
        lines.append("- NONE")
    lines.extend(["", "ORDER_EXECUTED = NO", ""])
    return "\n".join(lines)


def write_gui_report(output_root: str | Path, payload: Mapping[str, Any]) -> Path:
    """Write a bounded human report inside the Challenger-only directory."""
    store = V8ArtifactStore(Path(output_root))
    # Reuse the Challenger store's atomic, path-confined writer so a GUI run
    # cannot partially overwrite a report or escape the isolated directory.
    return store._atomic_write("V8_GUI_REPORT.md", _report_text(payload))


def _parse_cli_output(stdout: str) -> dict[str, Any]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return {"status": "FAILED", "errors": ["V8 CLI returned non-JSON output"]}
    return value if isinstance(value, dict) else {"status": "FAILED", "errors": ["V8 CLI returned a non-object"]}


def run_once(
    *,
    bundle: str | Path,
    manifest: str | Path,
    candidates: str | Path,
    evidence: str | Path,
    output_root: str | Path,
    reasoning_effort: str = "medium",
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], Path]:
    """Run one canonical Challenger invocation and create its GUI report."""
    paths = {"bundle": Path(bundle), "manifest": Path(manifest), "candidates": Path(candidates), "evidence": Path(evidence)}
    missing = [key for key, path in paths.items() if not path.is_file()]
    if missing:
        payload: dict[str, Any] = {
            "status": "BLOCKED_INPUT",
            "errors": [f"missing immutable Primary input: {', '.join(missing)}"],
            "bundle": str(bundle),
            "manifest": str(manifest),
            "candidates": str(candidates),
            "evidence": str(evidence),
            "output_root": str(output_root),
            "broker_write_count": BROKER_WRITE_COUNT,
        }
        try:
            payload["bundle_hash"] = bundle_summary(bundle)["bundle_hash"]
        except Exception as exc:
            payload["errors"] = [*payload["errors"], str(exc)[:240]]
        return payload, write_gui_report(output_root, payload)

    command = build_cli_command(bundle, manifest, candidates, evidence, output_root, reasoning_effort=reasoning_effort)
    try:
        completed = subprocess.run(
            command,
            cwd=str(Path(__file__).resolve().parents[1]),
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        payload = {
            "status": "FAILED",
            "errors": [f"unable to start V8 Challenger: {str(exc)[:240]}"],
            "bundle": str(bundle),
            "manifest": str(manifest),
            "candidates": str(candidates),
            "evidence": str(evidence),
            "output_root": str(output_root),
            "broker_write_count": BROKER_WRITE_COUNT,
        }
        return payload, write_gui_report(output_root, payload)
    payload = _parse_cli_output(completed.stdout)
    if completed.returncode != 0 and not payload.get("errors"):
        payload["errors"] = ["V8 Challenger process failed"]
    payload.update({
        "bundle": str(bundle),
        "manifest": str(manifest),
        "candidates": str(candidates),
        "evidence": str(evidence),
        "output_root": str(output_root),
        "broker_write_count": BROKER_WRITE_COUNT,
    })
    try:
        payload["bundle_hash"] = bundle_summary(bundle)["bundle_hash"]
    except Exception as exc:
        payload.setdefault("errors", []).append(str(exc)[:240])
    return payload, write_gui_report(output_root, payload)


def _default_bundle() -> str:
    candidate = Path(r"C:\Users\ohjin\Downloads\STOCK_SCANNING_PROMPTS_V8_A_GRADE_PIPELINE.zip")
    return str(candidate) if candidate.exists() else ""


def _web_page(state: Mapping[str, Any]) -> str:
    """Render the fallback local-browser GUI without embedding secrets."""
    values = state.get("values") or {}
    status = html.escape(str(state.get("status") or "Ready"))
    report_link = ""
    if state.get("report"):
        report_link = '<p><a href="/report" target="_blank">Open latest report</a></p>'
    fields = (
        ("V8 bundle", "bundle", _default_bundle()),
        ("Primary manifest", "manifest", ""),
        ("Candidates JSON", "candidates", ""),
        ("Evidence JSON", "evidence", ""),
        ("Output root", "output_root", "shadow_runs"),
    )
    rows = []
    for label, key, default in fields:
        value = html.escape(str(values.get(key, default)), quote=True)
        rows.append(f'<label>{html.escape(label)}<input name="{key}" value="{value}" size="100"></label>')
    effort = html.escape(str(values.get("effort", "medium")), quote=True)
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Stock Agent — V8 Challenger</title>
<style>body{font:14px sans-serif;max-width:1100px;margin:2em auto}label{display:block;margin:.7em 0}input{margin-left:1em;max-width:80%;padding:.35em}button{padding:.6em 1em;margin-right:.5em}.status{white-space:pre-wrap;background:#f4f4f4;padding:1em;border-radius:4px}</style>
</head><body><h1>Stock Agent — V8 Challenger (read-only)</h1>
<p>This local fallback GUI binds to 127.0.0.1 only. Primary authority and broker writes are never touched.</p>
<form method="post" action="/action">""" + "".join(rows) + f"""
<label>Reasoning effort<select name="effort"><option>low</option><option{' selected' if effort == 'medium' else ''}>medium</option><option{' selected' if effort == 'high' else ''}>high</option><option{' selected' if effort == 'xhigh' else ''}>xhigh</option><option{' selected' if effort == 'max' else ''}>max</option></select></label>
<button name="action" value="validate">Validate bundle</button><button name="action" value="run">Run V8 Challenger (read-only)</button>
</form><h2>Status</h2><div class="status">""" + status + "</div>""" + report_link + "</body></html>"


def _launch_web_gui(reason: str) -> int:  # pragma: no cover - exercised manually on desktop
    """Serve a short-lived localhost GUI when Tk lacks its Tcl runtime."""
    state: dict[str, Any] = {"status": f"Tk unavailable; using local browser GUI. {reason}", "values": {}}

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, body: str, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path == "/report" and state.get("report"):
                try:
                    self._respond(Path(state["report"]).read_text(encoding="utf-8"), content_type="text/plain; charset=utf-8")
                except OSError:
                    self._respond("Report is unavailable", status=404, content_type="text/plain; charset=utf-8")
                return
            self._respond(_web_page(state))

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64 * 1024:
                self._respond("Request too large", status=413, content_type="text/plain; charset=utf-8")
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            values = {key: items[0] for key, items in form.items() if items}
            action = values.get("action", "")
            state["values"] = values
            if action == "validate":
                try:
                    summary = bundle_summary(values.get("bundle", ""))
                    state["status"] = f"Bundle PASS: {summary['stage_count']} stages; hash={summary['bundle_hash']}"
                except Exception as exc:
                    state["status"] = f"Bundle FAIL: {str(exc)[:240]}"
            elif action == "run":
                try:
                    payload, report = run_once(
                        bundle=values.get("bundle", ""), manifest=values.get("manifest", ""),
                        candidates=values.get("candidates", ""), evidence=values.get("evidence", ""),
                        output_root=values.get("output_root", "shadow_runs"),
                        reasoning_effort=values.get("effort", "medium"),
                    )
                    state["report"] = str(report)
                    state["status"] = f"{payload.get('status', 'UNKNOWN')} — report: {report} — broker writes: 0"
                except Exception as exc:
                    state["status"] = f"FAILED — {str(exc)[:240]}"
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Local V8 Challenger GUI: {url}", flush=True)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _launch_gui() -> int:  # pragma: no cover - exercised manually on desktop
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except Exception as exc:
        print(json.dumps({"status": "GUI_UNAVAILABLE", "error": str(exc)[:240]}, ensure_ascii=False))
        return 2

    try:
        root = tk.Tk()
    except Exception as exc:
        return _launch_web_gui(str(exc)[:240])
    root.title("Stock Agent — V8 Challenger (read-only)")
    root.geometry("900x430")
    variables = {
        "bundle": tk.StringVar(value=_default_bundle()),
        "manifest": tk.StringVar(),
        "candidates": tk.StringVar(),
        "evidence": tk.StringVar(),
        "output_root": tk.StringVar(value="shadow_runs"),
        "effort": tk.StringVar(value=os.getenv("LUNA_DEFAULT_REASONING_EFFORT", "medium")),
        "status": tk.StringVar(value="Ready — validate the real V8 ZIP first."),
    }

    def add_row(row: int, label: str, key: str, browse: bool = True) -> None:
        tk.Label(root, text=label, anchor="w", width=18).grid(row=row, column=0, padx=8, pady=6, sticky="w")
        tk.Entry(root, textvariable=variables[key], width=84).grid(row=row, column=1, padx=4, pady=6, sticky="ew")
        if browse:
            def choose() -> None:
                selected = filedialog.askopenfilename() if key != "output_root" else filedialog.askdirectory()
                if selected:
                    variables[key].set(selected)
            tk.Button(root, text="Browse", command=choose).grid(row=row, column=2, padx=8, pady=6)

    for row, (label, key) in enumerate((("V8 bundle", "bundle"), ("Primary manifest", "manifest"), ("Candidates JSON", "candidates"), ("Evidence JSON", "evidence"), ("Output root", "output_root"))):
        add_row(row, label, key)
    tk.Label(root, text="Reasoning effort", anchor="w", width=18).grid(row=5, column=0, padx=8, pady=6, sticky="w")
    tk.OptionMenu(root, variables["effort"], "low", "medium", "high", "xhigh", "max").grid(row=5, column=1, padx=4, pady=6, sticky="w")
    status_label = tk.Label(root, textvariable=variables["status"], anchor="w", justify="left", wraplength=820)
    status_label.grid(row=6, column=0, columnspan=3, padx=8, pady=12, sticky="w")

    def validate() -> None:
        try:
            summary = bundle_summary(variables["bundle"].get())
            variables["status"].set(f"Bundle PASS: {summary['stage_count']} stages; hash={summary['bundle_hash']}")
        except Exception as exc:
            variables["status"].set(f"Bundle FAIL: {str(exc)[:240]}")

    def execute() -> None:
        variables["status"].set("Running read-only Challenger…")
        def worker() -> None:
            payload, report = run_once(
                bundle=variables["bundle"].get(), manifest=variables["manifest"].get(),
                candidates=variables["candidates"].get(), evidence=variables["evidence"].get(),
                output_root=variables["output_root"].get(), reasoning_effort=variables["effort"].get(),
            )
            root.after(0, lambda: variables["status"].set(
                f"{payload.get('status', 'UNKNOWN')} — report: {report} — broker writes: {BROKER_WRITE_COUNT}"
            ))
        threading.Thread(target=worker, daemon=True).start()

    def open_report() -> None:
        report = Path(variables["output_root"].get()) / "challenger_v8" / "V8_GUI_REPORT.md"
        if report.exists() and hasattr(os, "startfile"):
            os.startfile(str(report))
        else:
            messagebox.showinfo("Report", str(report))

    tk.Button(root, text="Validate bundle", command=validate).grid(row=7, column=0, padx=8, pady=14, sticky="w")
    tk.Button(root, text="Run V8 Challenger (read-only)", command=execute).grid(row=7, column=1, padx=8, pady=14, sticky="w")
    tk.Button(root, text="Show report path", command=open_report).grid(row=7, column=2, padx=8, pady=14)
    root.columnconfigure(1, weight=1)
    root.mainloop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GUI launcher for isolated V8 Challenger")
    parser.add_argument("--validate-bundle", type=Path, help="Validate a V8 ZIP/directory and print its hash")
    args = parser.parse_args(argv)
    if args.validate_bundle:
        try:
            print(json.dumps({"status": "PASS", **bundle_summary(args.validate_bundle), "broker_write_count": BROKER_WRITE_COUNT}, ensure_ascii=False))
            return 0
        except Exception as exc:
            print(json.dumps({"status": "FAIL", "error": str(exc)[:240], "broker_write_count": BROKER_WRITE_COUNT}, ensure_ascii=False))
            return 2
    return _launch_gui()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
