# The Caduceus operator UI

One app, three ways in, built from one source (`plugin/caduceus_governance/ui/src`, assembled by `scripts/build_ui.py`):

| Where | How you get there | Can approve? |
|---|---|---|
| **Hermes dashboard tab** | `hermes dashboard`, then the **Caduceus** tab (after Skills). Reaches a phone when the dashboard is bound to a reachable address behind its auth gate (Hermes docs: *Web Dashboard*, *Open it from your phone*) | Yes, on a secure page (HTTPS or localhost) in a browser with Ed25519 WebCrypto |
| **Hermes Desktop pane** | Right-hand pane `caduceus` and a status-bar chip | No. The pane reviews; approving is signed on a phone or another browser, because a key on the agent's own machine proves little |
| **Standalone page** | `<gateway>/api/plugins/caduceus-governance/ui`, installable to a phone's home screen (web manifest). Needs the dashboard's cookie auth (non-loopback, gated mode); in loopback mode the API wants the SPA's session token, so use the tab | Same as the tab |

Layout follows the width the app is given (CSS container queries), not the window: a phone, a dashboard column and a Desktop pane all get the phone layout; a wide window gets a rail and a two-pane gate.

## What you do in it

* **Gate**: what is waiting for you. Each card says why it was held in plain words, shows a bounded diff (always as text), and offers **Decline** or **Hold to release**. A change HEAD has since moved past is marked and can only be declined.
* **Ledger**: verdicts and operator actions, newest first, with the chain's health ("29 entries, root 41ea3e0d…") and a strip of recent verdicts.
* **State**: per surface (skills, memory, config): HEAD, counts, provenance, frozen flag, ledger health, policy digest, whether device signatures are required.
* **Devices**: this device's key, the fingerprint to compare, the exact `caduceus device-add` command, and what is enrolled on the host.

## The approval model

Releasing is the one act that lets an agent's change become part of the agent, so it is signed:

1. A browser makes an Ed25519 key with WebCrypto, **non-extractable**, kept in IndexedDB. Only the public key is ever shown or sent.
2. You enrol it on the host with `caduceus device-add --base <state dir> --name phone --pub <hex> --operator me`. Enrolling is deliberately not possible from the UI. The host prints the same fingerprint the UI shows (JS and Python agree: tested).
3. To release or decline, the device signs the plain-text message `caduceus.approval.v1 \n action \n surface \n effect digest \n HEAD it was held against \n nonce \n expiry`. The host checks the signature against the enrolled public key, the expiry (at most 10 minutes), a single-use nonce, and that the effect is still held and HEAD has not moved. The nonce use and the actor (`device:phone`) go on the ledger.
4. `caduceus policy-set` with `approval_required: true` makes the signature mandatory for every path, including the CLI (which can sign with a local keyring: `--keyring ... --signer ...`).

`POST /approve` is the only route that changes anything; it refuses everything that is not a valid device signature (forged, wrong action, wrong surface, wrong effect, stale HEAD, expired, replayed, oversized, not JSON). Policy, freeze, enrolment and mode stay CLI-only.

**What this does not give you, plainly.** A key that lives in the browser of the machine the agent runs on is only as safe as that machine: a same-user process can read the browser profile. Use your phone. And `devices.json`, `policy.json` and the ledger are files: they are verified against the ledger so a silent edit is refused, but a same-user process can still append to the ledger. Real separation needs the state directory out of the agent's reach (`caduceus openshell-policy`, or another OS user). The signature proves a person with the device approved; file permissions are what stop an agent from skipping the check.

**HTTPS on a phone.** `crypto.subtle` exists only on secure pages. Over plain HTTP on a LAN the UI still reviews and shows the CLI command to copy, but cannot sign. Put the dashboard behind HTTPS (Tailscale Serve, a tunnel, a reverse proxy) as Hermes recommends for phone access.

## Design

Hermes Teal: deep teal and cream with a brass accent and the caduceus mark; serif headings (system Georgia/Palatino stack), system sans, system mono; no external fonts or images; dark and light follow the system with a manual toggle. The hold-to-release gesture (900 ms, with a keyboard equivalent and a confirm dialog for assistive tech) makes releasing deliberate without a second screen. Motion respects `prefers-reduced-motion`; focus is visible; colour is never the only signal.

Security of the page itself: nothing from the agent is ever HTML (every string is set with `textContent`, enforced by a test; the seeded demo carries `<img onerror>`, `<script>` and a `javascript:` link that render as text). The standalone page is served with a per-response nonce CSP (`default-src 'none'`), no inline style attributes, no external resources, `no-store`, `no-referrer`.

## Verified, and not

Verified: the real Hermes dashboard (loopback mode, token auth) discovers the tab, serves the bundle and the API, and the whole flow works inside it: create a device key, enrol with the CLI (fingerprints equal), hold, signature accepted, change live, ledger shows `device:laptop`; an unsigned POST gets 400 and a POST without the dashboard token 401. The same flow on the standalone page under the strict CSP. A WebCrypto signature made under node is verified by the real kernel and the messages are byte-identical (`tests/test_ui_signing.py`). 285+ tests, including the API over a real seeded kernel (`tests/test_plugin_api.py`), build sync and source rules (`tests/test_ui_build.py`). `hermes plugins validate` passes (desktop-surface lint, security scan).

Not verified: Hermes Desktop rendering (the pane is lint-clean and unrendered); Safari and Firefox; a physical phone (a 375×812 emulated viewport and synthetic touch pointer events were used); the keyboard and screen-reader paths by hand; the standalone page behind the dashboard's real cookie gate; HTTPS from a phone.

## Try it

```sh
~/.hermes/hermes-agent/venv/bin/python scripts/ui_dev_server.py --port 8791   # seeded throw-away home; open http://127.0.0.1:8791/
python3 scripts/build_ui.py                                                       # rebuild after editing ui/src
```
The dev server's `_dev/enroll` route stands in for the operator running `caduceus device-add`; it exists only in that script.
