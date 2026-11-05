"""Read-only operator view for the Caduceus Desktop pane: GET /status and GET /review/{effect_digest}.

Deliberately has NO mutating route. Releasing a held change, changing policy, freezing and attesting stay CLI-only (`caduceus release-effect`, ...), so nothing the agent can
reach through the gateway can approve its own change. Runs inside the gateway process; stdlib plus the web framework Hermes already provides.
"""

from __future__ import annotations

import os
import re
import sys

from fastapi import APIRouter, HTTPException

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    import caduceus  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(_HERE, "_vendor"))

router = APIRouter()
_HEX = re.compile(r"^[0-9a-f]{64}$")


def _home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def _surfaces_state():
    import json

    base = os.path.join(_home(), "caduceus")
    try:
        with open(os.path.join(base, "mode.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    mode = cfg.get("mode") if cfg.get("mode") in ("observe", "enforce", "boundary") else "observe"
    return base, mode, [s for s in ("skills", "memory", "config") if s in (cfg.get("surfaces") or ["skills"])] or ["skills"], cfg.get("live") is True


def _kernel(base, surface, live):
    import dataclasses

    from caduceus.transition import Kernel, Policy, load_policy

    pol = load_policy(base) or Policy()
    if surface == "skills":
        return Kernel(os.path.join(_home(), "skills"), base, pol, allow_live=live)
    if surface == "memory":
        return Kernel(os.path.join(_home(), "memories"), os.path.join(base, "memory"), dataclasses.replace(pol, surface="memory"), allow_live=live)
    return Kernel(_home(), os.path.join(base, "config"), dataclasses.replace(pol, surface="config"), allow_live=live)


@router.get("/status")
async def status():
    base, mode, surfaces, live = _surfaces_state()
    out = {"mode": mode, "surfaces": {}}
    for s in surfaces:
        try:
            out["surfaces"][s] = _kernel(base, s, live).status()
        except Exception as exc:  # noqa: BLE001 - a broken surface is shown, never hidden
            out["surfaces"][s] = {"error": type(exc).__name__}
    return out


@router.get("/review/{surface}/{effect_digest}")
async def review(surface: str, effect_digest: str):
    base, mode, surfaces, live = _surfaces_state()
    if surface not in surfaces or not _HEX.match(effect_digest):
        raise HTTPException(status_code=404, detail="unknown surface or digest")
    try:
        return _kernel(base, surface, live).review(effect_digest)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=type(exc).__name__)
