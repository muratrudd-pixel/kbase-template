"""One way to call a model: the Claude Code CLI in print mode (`claude -p`).

It runs under your Claude subscription (or ANTHROPIC_API_KEY if that's how your CLI is set up), so there is no extra
API bill for the template's jobs. Each call runs in an empty temp folder with no tools and no MCP connections: the model
sees only the prompt. Model names are CLI aliases (haiku / sonnet / opus) set in config/pipeline.yaml.

For scheduled jobs (launchd / cron) run `claude setup-token` once and put the token in .env as
CLAUDE_CODE_OAUTH_TOKEN; jobs inherit it from the environment.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .core import env


def claude_bin():
    return env("CLAUDE_BIN") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def claude(prompt, model="sonnet", timeout=1800, web=False):
    """Run one prompt, return the text. web=True allows WebSearch/WebFetch (off by default)."""
    args = [claude_bin(), "-p", "--model", model, "--output-format", "text", "--strict-mcp-config"]
    args += ["--allowedTools", "WebSearch,WebFetch", "--max-turns", "8"] if web else ["--tools", "", "--max-turns", "1"]
    with tempfile.TemporaryDirectory() as cwd:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                           env=dict(os.environ))
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"claude -p ({model}) failed rc={r.returncode}: {(r.stderr or r.stdout)[:400]}")
    return r.stdout


def parse_json(text):
    """First JSON object/array in a model reply (tolerates ```json fences and stray prose)."""
    m = re.search(r"```(?:json)?\s*([\[{].*[\]}])\s*```", text, re.S)
    raw = m.group(1) if m else text[min(i for i in (text.find("{"), text.find("[")) if i >= 0):]
    raw = raw[: max(raw.rfind("}"), raw.rfind("]")) + 1]
    return json.loads(raw)
