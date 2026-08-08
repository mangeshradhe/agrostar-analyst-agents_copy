"""Thin wrapper around the `claude` CLI for headless (non-interactive) calls.

Uses the machine's existing `claude` login (Claude Code / claude.ai account) —
no ANTHROPIC_API_KEY required. Each call is a fresh, isolated `claude -p` run.
"""
import subprocess
import time


class ClaudeCliError(RuntimeError):
    pass


def run_claude(prompt, allowed_tools, cwd=None, timeout=600, retries=2):
    """Run `claude -p <prompt>` headless, restricted to `allowed_tools`.

    Returns the CLI's final text output (stdout). Raises ClaudeCliError if
    every attempt fails.
    """
    tools_str = " ".join(allowed_tools)
    last_err = None
    for attempt in range(1, retries + 2):
        try:
            result = subprocess.run(
                ["claude", "-p", prompt, "--allowedTools", tools_str],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            last_err = f"timed out after {timeout}s"
            time.sleep(3)
            continue

        if result.returncode == 0:
            return result.stdout.strip()

        last_err = f"exit {result.returncode}: {result.stderr.strip()[:500]}"
        if "spend limit" in result.stderr.lower() or "spend limit" in result.stdout.lower():
            # Org spend limit hit — retrying won't help until it resets.
            raise ClaudeCliError(f"org spend limit hit: {last_err}")
        time.sleep(3)

    raise ClaudeCliError(f"claude -p failed after {retries + 1} attempts: {last_err}")
