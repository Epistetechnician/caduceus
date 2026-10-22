"""Real-skill executor: run one Hermes turn per probe in an isolated HERMES_HOME.

Caduceus itself still makes no model or network call. `HermesExecutor` spawns
the Hermes interpreter on `hermes_runner.py`; that subprocess talks to whatever
`base_url` the caller configured. Constructing an executor therefore requires
`allow_model_calls=True` as an explicit opt-in, and tests point it at a fake
runner or a 127.0.0.1 stub.

Isolation: each executor gets a fresh temp HERMES_HOME holding only a copy of
the one skill under test (or nothing, for the no-skill arm) and a config with
every plugin disabled. The real Hermes home is only ever read (to copy the
skill), and the executor refuses a home inside it.

Pinning: model, provider and base_url are fixed per executor. Hermes exposes no
temperature or seed on `AIAgent`, so sampling uses the provider default; the
manifest records that as `temperature: "provider_default"` rather than
pretending it is pinned.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .digest import domain_digest
from .skills import load_skill

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hermes_runner.py")


class ExecutorError(Exception):
    """The executor could not produce an output. A NotRun, never a probe fail."""


@dataclass(frozen=True)
class ExecutorConfig:
    hermes_python: str
    hermes_src: str
    model: str
    provider: str
    base_url: str
    api_key_env: Optional[str]
    timeout_s: int = 300
    use_hermes_login: bool = False

    def manifest(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "base_url": self.base_url,
            "temperature": "provider_default",
            "timeout_s": self.timeout_s,
        }


def _real_home() -> str:
    return os.path.realpath(os.path.expanduser("~/.hermes"))


def _inside(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


class HermesExecutor:
    """`Callable[[str], str]` usable as a `probes.Skill`. Raises ExecutorError."""

    def __init__(
        self,
        config: ExecutorConfig,
        skills_root: Optional[str],
        skill_rel_dir: Optional[str],
        *,
        allow_model_calls: bool = False,
        runner_cmd: Optional[List[str]] = None,
    ):
        if not allow_model_calls:
            raise ExecutorError("HermesExecutor needs allow_model_calls=True")
        self.config = config
        self._runner_cmd = runner_cmd or [config.hermes_python, RUNNER]
        self._tmp = tempfile.mkdtemp(prefix="caduceus-exec-")
        self.home = os.path.join(self._tmp, "hermes_home")
        for guard in (_real_home(), os.environ.get("HERMES_HOME") or _real_home()):
            if _inside(self.home, guard):
                shutil.rmtree(self._tmp, ignore_errors=True)
                raise ExecutorError("refusing: executor home inside a real Hermes home")
        os.makedirs(os.path.join(self.home, "skills"))
        with open(os.path.join(self.home, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write("plugins:\n  enabled: []\n")
        self.skill_name: Optional[str] = None
        self.skill_tree_digest: Optional[str] = None
        if skill_rel_dir is not None:
            if skills_root is None:
                raise ExecutorError("skill_rel_dir given without skills_root")
            record = load_skill(skills_root, skill_rel_dir)
            shutil.copytree(
                os.path.join(skills_root, skill_rel_dir),
                os.path.join(self.home, "skills", skill_rel_dir),
                ignore=shutil.ignore_patterns(".*"),
                symlinks=False,
            )
            copied = load_skill(os.path.join(self.home, "skills"), skill_rel_dir)
            if copied.tree_digest != record.tree_digest:
                raise ExecutorError("skill copy digest mismatch")
            self.skill_name = record.name
            self.skill_tree_digest = record.tree_digest

    def manifest(self) -> Dict[str, Any]:
        return dict(self.config.manifest(), skill=self.skill_name, skill_tree_digest=self.skill_tree_digest)

    def manifest_digest(self) -> str:
        return domain_digest("caduceus.executor.manifest.v1", self.manifest())

    def __call__(self, prompt: str, env_extra: Optional[Dict[str, str]] = None,
                 request_extra: Optional[Dict[str, Any]] = None) -> str:
        """`env_extra` / `request_extra` configure a tool sandbox for agentic runs (e.g. Hermes' Docker
        terminal backend); only TERMINAL_* variables and the runner's known keys are accepted."""
        request = {
            "hermes_src": self.config.hermes_src,
            "model": self.config.model,
            "provider": self.config.provider,
            "base_url": self.config.base_url,
            "api_key_env": self.config.api_key_env,
            "use_hermes_login": self.config.use_hermes_login,
            "skill": self.skill_name,
            "prompt": prompt,
        }
        extra = request_extra or {}
        if set(extra) - {"toolsets", "max_iterations", "task_id", "collect_command"}:
            raise ExecutorError("unknown request_extra keys")
        request.update(extra)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("HERMES_") or k == self.config.api_key_env}
        env["HERMES_HOME"] = self.home
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for k, v in (env_extra or {}).items():
            if not k.startswith("TERMINAL_"):
                raise ExecutorError("env_extra accepts TERMINAL_* only")
            env[k] = v
        try:
            proc = subprocess.run(
                self._runner_cmd,
                input=json.dumps(request),
                capture_output=True,
                text=True,
                cwd=self._tmp,
                env=env,
                timeout=self.config.timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise ExecutorError("timeout") from None
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        try:
            reply = json.loads(lines[-1])
        except (IndexError, ValueError):
            raise ExecutorError("runner_no_json:rc=%d" % proc.returncode) from None
        if not isinstance(reply, dict) or reply.get("ok") is not True:
            raise ExecutorError("runner_error:%s" % str(reply.get("error") if isinstance(reply, dict) else "?")[:80])
        text = reply.get("final_response")
        if not isinstance(text, str):
            raise ExecutorError("runner_bad_reply")
        return text

    def close(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def __enter__(self) -> "HermesExecutor":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def default_config_from_env(
    hermes_src: str, model: str, provider: str, base_url: str, api_key_env: Optional[str], use_hermes_login: bool = False
) -> ExecutorConfig:
    src = os.path.abspath(os.path.expanduser(hermes_src))
    py = os.path.join(src, "venv", "bin", "python")
    return ExecutorConfig(py if os.path.exists(py) else sys.executable, src, model, provider, base_url,
                          api_key_env, use_hermes_login=use_hermes_login)
