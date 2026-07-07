from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4


def _extract_json_line(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return {"response": output.strip()}
    return parsed if isinstance(parsed, dict) else {"response": str(parsed)}


def _run_openclaw_agent(agent: str, prompt: str, timeout_seconds: int) -> dict[str, Any]:
    session_id = f"biz-monitor-ai-{int(time.time())}-{uuid4().hex[:8]}"
    command = [
        "docker",
        "exec",
        "openclaw-agent",
        "openclaw",
        "agent",
        "--agent",
        agent,
        "--session-id",
        session_id,
        "--message",
        prompt,
        "--json",
        "--timeout",
        str(timeout_seconds),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds + 15,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "openclaw failed").strip())
    parsed = _extract_json_line(completed.stdout)
    parsed.setdefault("agent", agent)
    parsed.setdefault("session_id", session_id)
    return parsed


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenClawAgentGateway/1.0"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json({"ok": True})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/v1/agent":
            self.send_error(404)
            return
        token = os.getenv("OPENCLAW_AGENT_GATEWAY_TOKEN", "").strip()
        if token:
            expected = f"Bearer {token}"
            if self.headers.get("Authorization", "") != expected:
                self.send_error(401)
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body)
            agent = str(payload.get("agent") or "cron-google").strip()
            prompt = str(payload.get("prompt") or "").strip()
            timeout_seconds = int(payload.get("timeout_seconds") or 90)
            if not prompt:
                raise ValueError("prompt is required")
            result = _run_openclaw_agent(agent, prompt, timeout_seconds)
            self._send_json({"ok": True, **result})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def log_message(self, format: str, *args: Any) -> None:
        if os.getenv("OPENCLAW_AGENT_GATEWAY_ACCESS_LOG", "0") in {"1", "true", "yes"}:
            super().log_message(format, *args)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description="Internal OpenClaw agent HTTP gateway")
    parser.add_argument("--host", default=os.getenv("OPENCLAW_AGENT_GATEWAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OPENCLAW_AGENT_GATEWAY_PORT", "8091")))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"OpenClaw agent gateway listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
