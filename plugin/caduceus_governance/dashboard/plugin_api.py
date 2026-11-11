"""Operator API for the Caduceus UI (Hermes dashboard tab, Desktop pane, and the standalone page): everything is read-only EXCEPT POST /approve.

POST /approve releases or discards ONE held change and only on a valid Ed25519 signature from a device the operator enrolled with `caduceus device-add` (see
caduceus/approvals.py). The signature binds the action, the surface, the exact effect, the HEAD it was held against, a nonce and a short expiry, so a forged or cross-site request, or
a replay, approves nothing. Nothing here can change policy, freeze, enrol a device or switch a mode: those stay CLI-only.

Runs inside the gateway process (stdlib plus the web framework Hermes already provides). The standalone page `/ui` is served with a per-response nonce CSP and no external resources.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    import caduceus  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(_HERE, "_vendor"))

router = APIRouter()
_HEX = re.compile(r"^[0-9a-f]{64}\Z")
SURFACES = ("skills", "memory", "config")
MAX_BODY = 8192
PLUGIN_ID = "caduceus-governance"


def _home() -> str:
    try:  # profile-aware: one gateway process can serve several profiles, so the env of the launch profile is not enough
        from hermes_constants import get_hermes_home
        return str(get_hermes_home())
    except Exception:  # outside Hermes (tests, the standalone page)
        return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def _cfg():
    base = os.path.join(_home(), "caduceus")
    try:
        with open(os.path.join(base, "mode.json"), "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    mode = cfg.get("mode") if cfg.get("mode") in ("observe", "enforce", "boundary") else "observe"
    wanted = cfg.get("surfaces") if isinstance(cfg.get("surfaces"), list) else ["skills"]
    return base, mode, [s for s in SURFACES if s in wanted] or ["skills"], cfg.get("live") is True


def _kernel(base, surface, live):
    import dataclasses

    from caduceus.transition import Kernel, Policy, load_policy

    pol = load_policy(base) or Policy()
    if surface == "skills":
        return Kernel(os.path.join(_home(), "skills"), base, pol, allow_live=live, devices_dir=base)
    if surface == "memory":
        return Kernel(os.path.join(_home(), "memories"), os.path.join(base, "memory"), dataclasses.replace(pol, surface="memory"), allow_live=live, devices_dir=base)
    return Kernel(_home(), os.path.join(base, "config"), dataclasses.replace(pol, surface="config"), allow_live=live, devices_dir=base)


def _ledger_head(kernel):
    try:
        h = kernel.ledger().head()
        return {"ok": True, "size": h.size, "root": h.root}
    except Exception as exc:  # noqa: BLE001 - a broken ledger is the most important thing to show, never hide
        return {"ok": False, "error": type(exc).__name__}


def _observe_summary(base):
    path = os.path.join(base, "ledger.jsonl")
    n = {"skill_mutation_intent": 0, "skill_mutation_blocked": 0}
    try:
        with open(path, "r", encoding="ascii") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)["body"].get("event")
                except (ValueError, KeyError, TypeError):
                    continue
                if ev in n:
                    n[ev] += 1
    except OSError:
        pass
    return n


def _devices(base):
    from caduceus import approvals

    try:
        return {"ok": True, "list": approvals.list_devices(base)}
    except approvals.ApprovalError as exc:
        return {"ok": False, "error": str(exc)[:160], "list": []}


@router.get("/status")
async def status():
    from caduceus.transition import TransitionError, load_policy

    base, mode, surfaces, live = _cfg()
    out = {"mode": mode, "now": int(time.time()), "state_dir": base, "surfaces": {}, "observe": _observe_summary(base), "devices": _devices(base)}
    try:
        pol = load_policy(base)
        out["policy"] = {"installed": pol is not None, "approval_required": bool(pol and pol.approval_required)}
    except TransitionError as exc:
        out["policy"] = {"installed": True, "error": str(exc)[:160]}
    if mode == "boundary" or os.path.exists(os.path.join(base, "transitions.jsonl")):
        for s in surfaces:
            try:
                k = _kernel(base, s, live)
                st = k.status()
                st["ledger"] = _ledger_head(k)
                out["surfaces"][s] = st
            except Exception as exc:  # noqa: BLE001
                out["surfaces"][s] = {"error": type(exc).__name__, "detail": str(exc)[:160]}
    return out


@router.get("/timeline")
async def timeline(surface: str = "skills", limit: int = 60):
    base, _mode, surfaces, live = _cfg()
    if surface not in surfaces:
        raise HTTPException(status_code=404, detail="unknown surface")
    try:
        return {"surface": surface, "items": _kernel(base, surface, live).timeline(max(1, min(int(limit), 200)))}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=type(exc).__name__)


@router.get("/review/{surface}/{effect_digest}")
async def review(surface: str, effect_digest: str):
    base, _mode, surfaces, live = _cfg()
    if surface not in surfaces or not _HEX.match(effect_digest):
        raise HTTPException(status_code=404, detail="unknown surface or digest")
    try:
        return _kernel(base, surface, live).review(effect_digest)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=type(exc).__name__)


@router.post("/approve")
async def approve(request: Request):
    from caduceus import approvals
    from caduceus.transition import TransitionError

    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(status_code=413, detail="request too large")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    action, surface, digest, reason, ap = body.get("action"), body.get("surface"), body.get("effect_digest"), body.get("reason", ""), body.get("approval")
    if action not in approvals.ACTIONS or surface not in SURFACES or not isinstance(digest, str) or not _HEX.match(digest) or not isinstance(reason, str) or not isinstance(ap, dict):
        raise HTTPException(status_code=400, detail="action, surface, effect_digest, reason and approval are required")
    base, _mode, surfaces, live = _cfg()
    if surface not in surfaces:
        raise HTTPException(status_code=404, detail="unknown surface")
    k = _kernel(base, surface, live)
    try:
        out = (k.release if action == "release" else k.discard)(digest, "ui", reason.strip()[:200] or "approved from the Caduceus UI", ap)
    except (TransitionError, approvals.ApprovalError) as exc:
        msg = str(exc)
        code = 403 if msg.startswith("approval refused") or "device-signed" in msg else 409
        return JSONResponse(status_code=code, content={"ok": False, "error": msg[:240]})
    st = k.status()
    st["ledger"] = _ledger_head(k)
    return {"ok": True, "result": {"status": out["status"], "actor": out.get("actor", "")}, "surface": surface, "state": st}


# -- the standalone page (also what the dev server and the PWA use) ----------------------------------------------------------------------------------------------
def _ui_file(name):
    path = os.path.join(_HERE, "ui", name)
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        raise HTTPException(status_code=404, detail="UI not built; run scripts/build_ui.py")


_SEC = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}


@router.get("/ui", response_class=HTMLResponse)
async def ui():
    nonce = secrets.token_urlsafe(18)
    html = _ui_file("index.html").decode("utf-8").replace("{{NONCE}}", nonce)
    csp = ("default-src 'none'; script-src 'nonce-%s'; style-src 'nonce-%s'; img-src 'self' data:; connect-src 'self'; manifest-src 'self'; "
           "base-uri 'none'; form-action 'none'; frame-ancestors 'self'" % (nonce, nonce))
    return HTMLResponse(html, headers={**_SEC, "Content-Security-Policy": csp})


@router.get("/manifest.webmanifest")
async def manifest():
    doc = {"name": "Caduceus", "short_name": "Caduceus", "description": "Review and release what your Hermes agent wants to change about itself.", "start_url": "./ui",
           "scope": "./", "display": "standalone", "background_color": "#071a19", "theme_color": "#071a19",
           "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}]}
    return Response(json.dumps(doc), media_type="application/manifest+json", headers=_SEC)


@router.get("/icon.svg")
async def icon():
    return Response(_ui_file("icon.svg"), media_type="image/svg+xml", headers={**_SEC, "Cache-Control": "public, max-age=3600"})
