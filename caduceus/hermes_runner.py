"""Hermes-side runner for `executor.HermesExecutor`. Not imported by Caduceus.

Executed as a subprocess by the Hermes venv interpreter. Reads one JSON request
on stdin, runs one Hermes turn (no tools unless the request names toolsets), prints one JSON line on stdout:

  request:  {"hermes_src", "model", "provider", "base_url", "api_key_env",
             "use_hermes_login", "skill" (name or null), "prompt"}

With `use_hermes_login`, the provider credential is resolved by Hermes' own
`resolve_runtime_provider` against the real home (read; Hermes may refresh an
expiring OAuth token there, which is Hermes maintaining its own login).
  response: {"ok": true, "final_response": str} | {"ok": false, "error": str}

Mirrors Hermes' own one-shot path (`hermes_cli/oneshot.py`): the skill is
preloaded with `build_preloaded_skills_prompt` and passed as
`ephemeral_system_prompt`. Context files, memory and background review are
off, so the only behavioural input besides the prompt is the skill. It refuses
to run unless HERMES_HOME is set and is not the real `~/.hermes`.
"""

import contextlib
import json
import os
import sys


def _fail(msg):
    print(json.dumps({"ok": False, "error": msg}), file=sys.__stdout__)
    return 1


def main():
    sys.dont_write_bytecode = True
    req = json.loads(sys.stdin.read())
    home = os.environ.get("HERMES_HOME", "")
    real = os.path.realpath(os.path.expanduser("~/.hermes"))
    if not home or os.path.realpath(home) == real or os.path.realpath(home).startswith(real + os.sep):
        return _fail("refusing: HERMES_HOME unset or inside the real Hermes home")
    sys.path.insert(0, req["hermes_src"])
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from run_agent import AIAgent

            system = None
            if req.get("skill"):
                from agent.skill_commands import build_preloaded_skills_prompt

                system, loaded, missing = build_preloaded_skills_prompt([req["skill"]])
                if missing or not system:
                    return _fail("skill_not_loaded")
            base_url, api_key, extra = req["base_url"], os.environ.get(req["api_key_env"] or "", ""), {}
            if req.get("use_hermes_login"):
                # Resolve the provider login stored in the real Hermes home,
                # in-process, then switch back to the isolated home. The
                # credential never leaves this process.
                from hermes_cli.runtime_provider import resolve_runtime_provider

                os.environ["HERMES_HOME"] = real
                try:
                    rt = resolve_runtime_provider(requested=req["provider"], target_model=req["model"])
                finally:
                    os.environ["HERMES_HOME"] = home
                base_url, api_key = rt.get("base_url") or base_url, rt.get("api_key") or ""
                if rt.get("api_mode"):
                    extra["api_mode"] = rt["api_mode"]
                if not api_key:
                    return _fail("no_hermes_login")
            agent = AIAgent(
                base_url=base_url,
                api_key=api_key,
                provider=req["provider"],
                model=req["model"],
                **extra,
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
                skip_background_review=True,
                enabled_toolsets=list(req.get("toolsets") or []),
                **({"max_iterations": int(req["max_iterations"])} if req.get("max_iterations") else {}),
                ephemeral_system_prompt=system,
            )
            task_id = req.get("task_id") or None
            try:
                result = agent.run_conversation(req["prompt"], task_id=task_id)
                collected = None
                if req.get("collect_command"):
                    # Read the agent's work out of its sandbox (e.g. `git diff`), then remove the sandbox.
                    from tools.terminal_tool import terminal_tool

                    out = json.loads(terminal_tool(command=req["collect_command"], task_id=task_id, timeout=120))
                    collected = out.get("output") if out.get("exit_code") == 0 else None
            finally:
                if req.get("toolsets"):
                    # One runner process serves one call, so every sandbox it opened is this call's.
                    from tools.terminal_tool import _active_environments, cleanup_vm

                    for tid in list(_active_environments):
                        with contextlib.suppress(Exception):
                            cleanup_vm(tid, force_remove=True)
        if not isinstance(result, dict):
            return _fail("no_result")
        if req.get("collect_command"):
            # Agentic runs: a turn that ends on its iteration budget still leaves work to grade;
            # provider failures are still NotRun.
            if result.get("failed") or result.get("error") or collected is None:
                return _fail("turn_failed")
            print(json.dumps({"ok": True, "final_response": collected}), file=sys.__stdout__)
            return 0
        # Hermes reports provider failures (e.g. HTTP 429) as final_response
        # text; they must surface as NotRun, never as a model answer.
        if result.get("failed") or result.get("interrupted") or result.get("completed") is False or result.get("error"):
            return _fail("turn_failed")
        text = result.get("final_response")
        if not isinstance(text, str):
            return _fail("no_final_response")
        print(json.dumps({"ok": True, "final_response": text}), file=sys.__stdout__)
        return 0
    except Exception as exc:  # reported to the executor; never raised past here
        return _fail(type(exc).__name__)


if __name__ == "__main__":
    sys.exit(main())
