/* Caduceus UI: one framework-free app, mounted by three hosts (the Hermes dashboard tab, the Hermes Desktop pane, and the standalone page that phones use).
   No innerHTML, no eval, no inline style attributes, no network except host.api. Every string that came from the agent or a skill is set with textContent.
   host = { env, api(method, path, body) -> Promise<json>, canSign?, store?{get,set}, copy?(text), notify?(title, body) } */
/* The exact text a device signs; caduceus/approvals.py builds the same bytes. Kept top-level so a test can run it under node and verify the signature in Python. */
function caduceusApprovalMessage(action, surface, effectDigest, parent, nonce, expires) {
  return ["caduceus.approval.v1", action, surface, effectDigest, parent, nonce, String(expires)].join("\n");
}

function CaduceusApp(root, host) {
  "use strict";
  var doc = root.ownerDocument, win = doc.defaultView || globalThis, NS = "http://www.w3.org/2000/svg";
  var TABS = [["gate", "Gate", "gate"], ["ledger", "Ledger", "ledger"], ["state", "State", "layers"], ["devices", "Devices", "key"]];
  var SURFACE = { skills: "Skills", memory: "Memory", config: "Config" };
  var REASONS = {
    "provenance:Quarantine": ["After untrusted content", "The agent had read content from the web, a browser or an MCP tool in this session. Text from there can steer what an agent writes down, so every write made after it waits for a person."],
    "injection:Quarantine": ["Looks like an instruction override", "The text matches a known pattern for overriding rules, hiding things from the user, or piping a download into a shell."],
    "secrets:Fail": ["Contains a secret", "A key, token or private key block is in the change. Secrets never become part of what the agent remembers."],
    "deletion:Quarantine": ["Deletes files", "The change removes a skill or more files than the policy allows in this window."],
    "budget:Quarantine": ["Larger than allowed", "More files or bytes than the policy budget for one change."],
    "rate:Quarantine": ["Too many changes too fast", "The agent changed this surface more often than the policy allows in the rate window."],
    "binary_exec:Quarantine": ["Executable or binary content", "A binary, a file with a NUL byte, or an executable permission bit."],
    "checks:Fail": ["An operator check failed", "A check you registered for this skill ran against the change and did not pass."],
    "checks:Quarantine": ["An operator check could not run", "Confinement was unavailable, so the check could not run. A check that did not run is never a pass."],
    "config_keys:Quarantine": ["Changes a sensitive setting", "The change touches an approval, plugin, endpoint, credential or MCP setting."],
    "lifecycle:Quarantine": ["Hermes did not announce it", "Hermes reported different skills than the ones that changed on disk."],
    "scope:Fail": ["Outside the governed area", "A path outside the files this surface governs."],
    "file_kind:Fail": ["Not a regular file", "A symlink or special file."],
    "pinned:Fail": ["Touches a pinned skill", "The operator pinned this skill so it can never change."],
    "protected:Fail": ["Touches a protected path", "The operator marked this path read-only to the agent."],
    "format:Fail": ["Malformed skill header", "SKILL.md needs a name that matches its folder and a description."],
    "syntax:Fail": ["Code that does not parse", "A Python or JSON file in the change is not valid."]
  };
  var S = { tab: "gate", status: null, timeline: null, tlSurface: null, sel: null, review: {}, open: {}, note: "", busy: null, holding: false, dirty: false, online: true, offset: 0,
            sign: null, device: null, theme: "auto", seenHeld: null, sig: "", loading: true };
  var timers = [], listeners = [], alive = true, el = {}, modalNode = null;

  /* ---------- tiny DOM kit ---------- */
  function add(parent, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) { for (var i = 0; i < c.length; i++) add(parent, c[i]); return; }
    parent.appendChild(typeof c === "object" ? c : doc.createTextNode(String(c)));
  }
  function h(tag, attrs) {
    var n = doc.createElement(tag), k, v;
    for (k in (attrs || {})) {
      v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (var i = 2; i < arguments.length; i++) add(n, arguments[i]);
    return n;
  }
  function sv(tag, attrs) {
    var n = doc.createElementNS(NS, tag);
    for (var k in (attrs || {})) n.setAttribute(k, attrs[k]);
    for (var i = 2; i < arguments.length; i++) add(n, arguments[i]);
    return n;
  }
  var ICONS = {
    gate: ["M5 21V10a7 7 0 0 1 14 0v11", "M3 21h18", "M12 8v13", "M9 14h6"],
    ledger: ["M4 6h16", "M4 12h16", "M4 18h10"],
    layers: ["M12 3l9 5-9 5-9-5 9-5z", "M3 13l9 5 9-5"],
    key: ["c8,15,4", "M11 12l9-9", "M17 6l3 3", "M14 9l2 2"],
    check: ["M5 12.5l4.5 4.5L19 7"], x: ["M6 6l12 12", "M18 6L6 18"], clock: ["c12,12,9", "M12 7v5l3 2"],
    shield: ["M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z", "M8.5 12l2.5 2.5L15.5 9.5"],
    hourglass: ["M7 3h10", "M7 21h10", "M8 3v3a4 4 0 0 0 1.5 3L12 12l2.5-3A4 4 0 0 0 16 6V3", "M8 21v-3a4 4 0 0 1 1.5-3L12 12l2.5 3a4 4 0 0 1 1.5 3v3"],
    back: ["M15 5l-7 7 7 7"], chev: ["M9 5l7 7-7 7"], refresh: ["M20 11a8 8 0 0 0-14.5-3.5", "M4 4v4.5h4.5", "M4 13a8 8 0 0 0 14.5 3.5", "M20 20v-4.5h-4.5"],
    sun: ["c12,12,4", "M12 2v2", "M12 20v2", "M2 12h2", "M20 12h2", "M5 5l1.4 1.4", "M17.6 17.6L19 19", "M5 19l1.4-1.4", "M17.6 6.4L19 5"],
    moon: ["M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"], copy: ["M9 9h11v11H9z", "M5 15V4h10"],
    lock: ["M6 11h12v9H6z", "M8.5 11V8a3.5 3.5 0 0 1 7 0v3"], alert: ["M12 4l9 16H3L12 4z", "M12 10v4", "M12 17.4v.1"],
    phone: ["M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z", "M11 18h2"], laptop: ["M5 6h14v9H5z", "M3 19h18"],
    file: ["M7 3h7l4 4v14H7z", "M14 3v4h4"], diamond: ["M12 3l9 9-9 9-9-9 9-9z"], plus: ["M12 5v14", "M5 12h14"], trash: ["M5 7h14", "M9 7V4h6v3", "M7 7l1 13h8l1-13"],
    pen: ["M4 20l4-1 11-11-3-3L5 16l-1 4z"], link: ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1", "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"]
  };
  function icon(name, cls) {
    var s = sv("svg", { viewBox: "0 0 24 24", class: "ico" + (cls ? " " + cls : ""), "aria-hidden": "true", focusable: "false" }), p = ICONS[name] || [];
    for (var i = 0; i < p.length; i++) {
      if (p[i].charAt(0) === "c" && p[i].charAt(1) !== "") { var a = p[i].slice(1).split(","); if (a.length === 3 && !isNaN(+a[0])) { s.appendChild(sv("circle", { cx: a[0], cy: a[1], r: a[2] })); continue; } }
      s.appendChild(sv("path", { d: p[i] }));
    }
    return s;
  }
  function mark(cls) {
    var wing = "M31 16C24 9.5 13 9.5 5 15.5c7-.6 12 1.4 16 5-5.5-1-11 0-14 3.5 6-1 12 1 17 5-4-.5-7 .6-9.5 2.5 6.5 0 12 2 17.5 4.5z";
    return sv("svg", { viewBox: "0 0 64 64", class: cls || "mark", "aria-hidden": "true", focusable: "false", fill: "none", stroke: "currentColor", "stroke-linecap": "round", "stroke-linejoin": "round" },
      sv("path", { d: wing, fill: "currentColor", "fill-opacity": "0.18", "stroke-width": "1.6" }),
      sv("path", { d: wing, fill: "currentColor", "fill-opacity": "0.18", "stroke-width": "1.6", transform: "translate(64 0) scale(-1 1)" }),
      sv("path", { d: "M32 9V60", "stroke-width": "2.6" }), sv("circle", { cx: "32", cy: "6.5", r: "3.2", fill: "currentColor", stroke: "none" }),
      sv("path", { d: "M32 58C17 55 17 49 32 46C47 43 47 38 32 35", "stroke-width": "2.4" }),
      sv("path", { d: "M32 58C47 55 47 49 32 46C17 43 17 38 32 35", "stroke-width": "2.4" }),
      sv("circle", { cx: "28.6", cy: "33.2", r: "2.1", fill: "currentColor", stroke: "none" }), sv("circle", { cx: "35.4", cy: "33.2", r: "2.1", fill: "currentColor", stroke: "none" }));
  }

  /* ---------- small helpers ---------- */
  function nowS() { return Math.floor(Date.now() / 1000) + S.offset; }
  function ago(at) {
    if (!at) return "";
    var d = nowS() - at;
    if (d < 45) return "just now";
    if (d < 3600) return Math.round(d / 60) + " min ago";
    if (d < 86400) return Math.round(d / 3600) + " h ago";
    try { return new Date(at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch (e) { return ""; }
  }
  function dayOf(at) {
    if (!at) return "Earlier";
    var d = new Date(at * 1000), t = new Date(Date.now() + S.offset * 1000), y = new Date(t.getTime() - 86400000);
    if (d.toDateString() === t.toDateString()) return "Today";
    if (d.toDateString() === y.toDateString()) return "Yesterday";
    try { return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }); } catch (e) { return "Earlier"; }
  }
  function hex(buf) { var b = new Uint8Array(buf), s = ""; for (var i = 0; i < b.length; i++) s += (b[i] < 16 ? "0" : "") + b[i].toString(16); return s; }
  function short(d) { return d ? d.slice(0, 8) + "…" + d.slice(-4) : "none"; }
  function plural(n, w) { return n + " " + (n === 1 ? w : w === "entry" ? "entries" : w + "s"); }
  function reasonInfo(code) { return REASONS[code] || [code, "Reported by the policy as " + code + "."]; }
  function storeGet(k) { try { return host.store ? host.store.get(k) : win.localStorage.getItem(k); } catch (e) { return null; } }
  function storeSet(k, v) { try { if (host.store) host.store.set(k, v); else win.localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  function every(fn, ms) { var t = host.setInterval ? host.setInterval(fn, ms) : win.setInterval(fn, ms); timers.push(host.setInterval ? t : function () { win.clearInterval(t); }); }
  function listen(target, type, fn) { target.addEventListener(type, fn); listeners.push(function () { target.removeEventListener(type, fn); }); }
  function vibrate(p) { try { if (win.navigator && win.navigator.vibrate) win.navigator.vibrate(p); } catch (e) { /* not supported */ } }
  function copyText(text) {
    function fallback() {
      var ta = h("textarea", { readonly: "", "aria-hidden": "true" });
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0"; root.appendChild(ta); ta.select();
      var ok = false; try { ok = doc.execCommand("copy"); } catch (e) { ok = false; }
      root.removeChild(ta); toast(ok ? "ok" : "bad", ok ? "Copied" : "Select the text and copy it");
    }
    if (host.copy) { host.copy(text).then(function (ok) { toast(ok ? "ok" : "bad", ok ? "Copied" : "Could not copy"); }, fallback); return; }
    if (win.navigator && win.navigator.clipboard && win.isSecureContext) win.navigator.clipboard.writeText(text).then(function () { toast("ok", "Copied"); }, fallback);
    else fallback();
  }
  function toast(kind, text) {
    var t = h("div", { class: "toast " + kind, role: "status" }, icon(kind === "ok" ? "check" : kind === "bad" ? "alert" : "shield"), h("span", { text: text }));
    el.toasts.appendChild(t);
    var id = win.setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, kind === "bad" ? 6500 : 3200);
    timers.push(function () { win.clearTimeout(id); });
  }
  function normErr(e) {
    var msg = (e && e.message) || "Request failed", body = e && e.body;
    if (typeof body === "string") { try { var j = JSON.parse(body); if (j && (j.error || j.detail)) msg = String(j.error || j.detail); } catch (x) { /* not json */ } }
    var err = new Error(msg); err.status = e && e.status; return err;
  }
  function api(method, path, body) {
    return host.api(method, path, body).then(function (r) { S.online = true; return r; }, function (e) { throw normErr(e); });
  }

  /* ---------- data ---------- */
  function allHeld() {
    var out = [], st = S.status;
    if (!st) return out;
    Object.keys(st.surfaces || {}).forEach(function (s) {
      var sf = st.surfaces[s];
      (sf.held || []).forEach(function (it) { var c = {}; for (var k in it) c[k] = it[k]; c.surface = s; c.digest = it.effect_digest; c.frozen = sf.frozen; out.push(c); });
    });
    out.sort(function (a, b) { return (b.at || 0) - (a.at || 0); });
    return out;
  }
  function findSel() { var it = allHeld(); for (var i = 0; i < it.length; i++) if (S.sel && it[i].digest === S.sel.digest && it[i].surface === S.sel.surface) return it[i]; return null; }
  function totals() {
    var t = { accept: 0, quarantine: 0, reject: 0 }, st = S.status;
    Object.keys((st && st.surfaces) || {}).forEach(function (s) { var c = st.surfaces[s].counts || {}; for (var k in t) t[k] += c[k] || 0; });
    return t;
  }
  function signature(st) { return JSON.stringify([st.mode, st.surfaces, st.devices, st.policy, st.observe]); }
  function refresh(manual) {
    if (!alive) return Promise.resolve();
    if (manual) { S.spin = true; renderTop(); }
    return api("GET", "/status").then(function (st) {
      S.offset = (st.now || 0) - Math.floor(Date.now() / 1000);
      var sig = signature(st), changed = sig !== S.sig;
      S.status = st; S.sig = sig; S.loading = false; S.spin = false;
      var n = allHeld().length;
      if (S.seenHeld != null && n > S.seenHeld && host.notify) host.notify("Caduceus", plural(n - S.seenHeld, "change") + " waiting at the gate");
      S.seenHeld = n;
      if (host.env === "standalone") doc.title = (n ? "(" + n + ") " : "") + "Caduceus";
      if (S.sel && !findSel() && !S.busy) { S.sel = null; }
      if (changed || manual) { render(); if (S.tab === "ledger") loadTimeline(); }
      else renderTop();
      if (manual) toast("ok", "Up to date");
    }, function (e) {
      S.online = false; S.spin = false; S.loading = false;
      renderTop(); if (!S.status) renderMain();
      if (manual) toast("bad", e.message);
    });
  }
  function loadTimeline() {
    var st = S.status; if (!st) return;
    var sfs = Object.keys(st.surfaces || {}); if (!sfs.length) { S.timeline = []; renderMain(); return; }
    if (!S.tlSurface || sfs.indexOf(S.tlSurface) < 0) S.tlSurface = sfs[0];
    api("GET", "/timeline?surface=" + encodeURIComponent(S.tlSurface) + "&limit=80").then(function (r) { S.timeline = r.items || []; if (S.tab === "ledger") renderMain(); }, function () { S.timeline = S.timeline || []; });
  }
  function loadReview(it) {
    if (S.review[it.digest] && !S.review[it.digest].err) return;
    S.review[it.digest] = { loading: true };
    api("GET", "/review/" + it.surface + "/" + it.digest).then(function (r) { S.review[it.digest] = r; }, function (e) { S.review[it.digest] = { err: e.message }; }).then(function () { if (S.sel && S.sel.digest === it.digest) renderMain(); });
  }

  /* ---------- this device's signing key ---------- */
  var DB = "caduceus-device";
  function idb() {
    return new Promise(function (res, rej) {
      var r = win.indexedDB.open(DB, 1);
      r.onupgradeneeded = function () { r.result.createObjectStore("keys"); };
      r.onsuccess = function () { res(r.result); }; r.onerror = function () { rej(r.error); };
    });
  }
  function idbDo(mode, fn) { return idb().then(function (db) { return new Promise(function (res, rej) { var tx = db.transaction("keys", mode), out = fn(tx.objectStore("keys")); tx.oncomplete = function () { db.close(); res(out && out.result); }; tx.onerror = function () { rej(tx.error); }; }); }); }
  function fingerprint(pubHex) {
    var b = new Uint8Array(pubHex.length / 2);
    for (var i = 0; i < b.length; i++) b[i] = parseInt(pubHex.substr(i * 2, 2), 16);
    return win.crypto.subtle.digest("SHA-256", b).then(function (d) { var x = hex(d).slice(0, 16); return x.match(/.{4}/g).join("-"); });
  }
  function detectSigning() {
    if (host.canSign === false) return Promise.resolve({ ok: false, why: "host" });
    var c = win.crypto;
    if (!win.isSecureContext || !c || !c.subtle) return Promise.resolve({ ok: false, why: "insecure" });
    if (!win.indexedDB) return Promise.resolve({ ok: false, why: "store" });
    return c.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]).then(function () { return { ok: true }; }, function () { return { ok: false, why: "algo" }; });
  }
  function loadDevice() {
    if (!S.sign || !S.sign.ok) return Promise.resolve();
    return idbDo("readonly", function (s) { return s.get("device"); }).then(function (rec) {
      if (!rec) { S.device = null; return; }
      return fingerprint(rec.pub).then(function (fp) { S.device = { priv: rec.priv, pub: rec.pub, fp: fp, name: rec.name, created: rec.created }; });
    }, function () { S.device = null; });
  }
  function createDevice(name) {
    return win.crypto.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]).then(function (kp) {
      return win.crypto.subtle.exportKey("raw", kp.publicKey).then(function (raw) {
        var rec = { priv: kp.privateKey, pub: hex(raw), name: name, created: Date.now() };
        return idbDo("readwrite", function (s) { return s.put(rec, "device"); });
      });
    }).then(loadDevice);
  }
  function forgetDevice() { return idbDo("readwrite", function (s) { return s.delete("device"); }).then(function () { S.device = null; }); }
  function enrolledAs() {
    if (!S.device || !S.status || !S.status.devices) return null;
    var l = S.status.devices.list || [];
    for (var i = 0; i < l.length; i++) if (l[i].fingerprint === S.device.fp) return l[i].name;
    return null;
  }
  function randomHex(n) { var b = new Uint8Array(n); win.crypto.getRandomValues(b); return hex(b); }
  function signApproval(action, it) {
    var name = enrolledAs(), rev = S.review[it.digest];
    if (!S.device || !name) return Promise.reject(new Error("This device is not enrolled."));
    if (!rev || !rev.parent) return Promise.reject(new Error("Open the change first so its details are loaded."));
    var nonce = randomHex(16), expires = nowS() + 300;
    var msg = caduceusApprovalMessage(action, it.surface, it.digest, rev.parent, nonce, expires);
    return win.crypto.subtle.sign({ name: "Ed25519" }, S.device.priv, new TextEncoder().encode(msg)).then(function (sig) { return { device: name, nonce: nonce, expires: expires, sig: hex(sig) }; });
  }

  /* ---------- acting on a held change ---------- */
  function canAct() {
    if (!S.sign) return { ok: false, why: "checking" };
    if (!S.sign.ok) return { ok: false, why: S.sign.why };
    if (!S.device) return { ok: false, why: "nokey" };
    if (!enrolledAs()) return { ok: false, why: "unenrolled" };
    return { ok: true };
  }
  function act(action, it) {
    if (S.busy) return;
    S.busy = action; renderMain();
    signApproval(action, it).then(function (approval) {
      return api("POST", "/approve", { action: action, surface: it.surface, effect_digest: it.digest, reason: S.note, approval: approval });
    }).then(function (res) {
      S.busy = null; S.sel = null; S.note = ""; S.sig = ""; vibrate(action === "release" ? [18, 40, 18] : 12);
      showDone(action, res.result && res.result.actor);
      return refresh();
    }).catch(function (e) {
      S.busy = null; vibrate(60); toast("bad", e.message); renderMain();
    });
  }

  /* ---------- sheets ---------- */
  function closeSheet() { if (modalNode && modalNode.parentNode) modalNode.parentNode.removeChild(modalNode); modalNode = null; if (S.restoreFocus && S.restoreFocus.focus) { try { S.restoreFocus.focus(); } catch (e) { /* gone */ } } S.restoreFocus = null; }
  function openSheet(content, label) {
    closeSheet();
    S.restoreFocus = doc.activeElement;
    var sheet = h("div", { class: "sheet", role: "alertdialog", "aria-modal": "true", "aria-label": label }, content);
    modalNode = h("div", { class: "scrim", onclick: function (e) { if (e.target === modalNode) closeSheet(); } }, sheet);
    root.appendChild(modalNode);
    var f = sheet.querySelector("button, input"); if (f) f.focus();
    return sheet;
  }
  function confirmSheet(opts) {
    var note = h("input", { type: "text", maxlength: "200", placeholder: "Note for the ledger (optional)", value: S.note, "aria-label": "Note for the ledger", oninput: function (e) { S.note = e.target.value; } });
    openSheet([h("h2", { text: opts.title }), h("p", { text: opts.body }), h("div", { class: "field" }, note),
      h("div", { class: "row" },
        h("button", { class: "btn " + (opts.primary ? "primary" : ""), type: "button", onclick: function () { closeSheet(); opts.go(); } }, icon(opts.icon), opts.cta),
        h("button", { class: "btn quiet", type: "button", onclick: closeSheet, text: "Cancel" }))], opts.title);
  }
  function showDone(action, actor) {
    var node = h("div", { class: "success" }, h("div", { class: "ring" }, icon("check")), h("h2", { text: action === "release" ? "Released" : "Declined" }),
      h("p", { class: "dim", text: (action === "release" ? "The change is now part of the agent." : "The change stays out; the agent keeps its last admitted state.") }),
      h("p", { class: "faint mono", text: "signed by " + (actor || "device") }));
    var sheet = openSheet(node, "Done"), id = win.setTimeout(closeSheet, 2200);
    timers.push(function () { win.clearTimeout(id); });
    sheet.addEventListener("click", closeSheet);
  }

  /* ---------- hold-to-release ---------- */
  function holdButton(opts) {
    var fill = h("span", { class: "fill" }), lbl = h("span", { class: "lbl" }, icon("lock"), h("span", { text: opts.label }));
    var btn = h("button", { class: "hold", type: "button", "aria-describedby": opts.hintId, disabled: opts.disabled }, fill, lbl);
    var raf = 0, timer = 0, t0 = 0, active = false, byKey = false, MS = 900;
    function setP(p) { fill.style.transform = "scaleX(" + p + ")"; }
    function tick() { setP(Math.min(1, (win.performance.now() - t0) / MS)); if (active) raf = win.requestAnimationFrame(tick); }  // the fill is cosmetic: completion is a timer, so a throttled frame loop cannot strand a hold
    function begin() { if (active || btn.disabled) return; active = true; S.holding = true; t0 = win.performance.now(); btn.classList.add("is-holding"); vibrate(8); raf = win.requestAnimationFrame(tick); timer = win.setTimeout(function () { if (active) finish(); }, MS); }
    function stop() { if (!active) return; active = false; S.holding = false; win.clearTimeout(timer); win.cancelAnimationFrame(raf); setP(0); btn.classList.remove("is-holding"); if (S.dirty) { S.dirty = false; render(); } }
    function finish() { active = false; S.holding = false; win.clearTimeout(timer); win.cancelAnimationFrame(raf); setP(1); btn.classList.add("is-done"); btn.disabled = true; opts.go(); }
    btn.addEventListener("pointerdown", function (e) { if (e.button === 0 || e.pointerType === "touch") { byKey = false; begin(); } });
    ["pointerup", "pointerleave", "pointercancel"].forEach(function (t) { btn.addEventListener(t, stop); });
    btn.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    btn.addEventListener("keydown", function (e) { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); byKey = true; begin(); } });
    btn.addEventListener("keyup", function (e) { if (e.key === " " || e.key === "Enter") { stop(); win.setTimeout(function () { byKey = false; }, 0); } });  // a click made by this very key release is still ignored; later bare clicks (assistive tech) are not
    btn.addEventListener("blur", stop);
    btn.addEventListener("click", function (e) { if (byKey) return; if (e.detail === 0 && !btn.disabled) opts.confirm(); });  // assistive tech activates with a bare click: ask once instead of holding
    return btn;
  }

  /* ---------- views ---------- */
  function renderTop() {
    var st = S.status, mode = st ? st.mode : null;
    el.top.textContent = "";
    add(el.top, [
      h("div", { class: "cad-brand" }, mark("mark"), h("span", { class: "word", text: "Caduceus" }), h("span", { class: "sub", text: "the staff for Hermes" })),
      h("div", { class: "grow" }),
      mode ? h("span", { class: "pill mode " + mode }, h("span", { text: mode })) : null,
      h("span", { class: "pill " + (S.online ? "live" : "off"), title: S.online ? "Connected" : "Cannot reach the gateway" }, h("span", { class: "dot" }), h("span", { class: "txt", text: S.online ? "live" : "offline" })),
      h("button", { class: "iconbtn" + (S.spin ? " spin" : ""), type: "button", "aria-label": "Refresh", onclick: function () { refresh(true); } }, icon("refresh")),
      h("button", { class: "iconbtn", type: "button", "aria-label": "Toggle light or dark theme", onclick: cycleTheme }, icon(isLight() ? "moon" : "sun"))
    ]);
  }
  function isLight() { var t = root.getAttribute("data-theme"); if (t) return t === "light"; try { return win.matchMedia("(prefers-color-scheme: light)").matches; } catch (e) { return false; } }
  function applyTheme() { if (S.theme === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", S.theme); }
  function cycleTheme() { S.theme = isLight() ? "dark" : "light"; storeSet("caduceus.theme", S.theme); applyTheme(); renderTop(); }

  function renderNav() {
    var held = allHeld().length;
    el.nav.textContent = "";
    TABS.forEach(function (t) {
      var b = h("button", { type: "button", "aria-current": S.tab === t[0] ? "page" : null, onclick: function () { go(t[0]); } }, icon(t[2]), h("span", { text: t[1] }),
        t[0] === "gate" && held ? h("span", { class: "badge", "aria-label": held + " waiting", text: String(held) }) : null);
      el.nav.appendChild(b);
    });
  }
  function go(tab) {
    S.tab = tab; S.sel = tab === "gate" ? S.sel : null;
    root.classList.toggle("detail-open", !!S.sel);
    renderNav(); renderMain();
    if (tab === "ledger") loadTimeline();
    if (win.scrollTo && host.env === "standalone") win.scrollTo(0, 0);
  }
  function render() { renderTop(); renderNav(); renderMain(); }
  function renderMain() {
    if (S.holding) { S.dirty = true; return; }
    root.classList.toggle("detail-open", S.tab === "gate" && !!S.sel);
    el.main.textContent = "";
    var v = S.tab === "ledger" ? viewLedger() : S.tab === "state" ? viewState() : S.tab === "devices" ? viewDevices() : viewGate();
    add(el.main, v);
    renderNav();
  }
  function skeleton() { return h("div", null, h("div", { class: "skel" }), h("div", { class: "skel" }), h("div", { class: "skel" })); }
  function offlineBanner() { return S.online ? null : h("div", { class: "banner bad", role: "alert" }, icon("alert"), h("span", { text: "Can't reach the Hermes gateway. Showing the last known state; retrying." })); }

  function viewGate() {
    var st = S.status;
    if (!st) return [offlineBanner(), h("div", { class: "view-h" }, h("h1", { text: "The gate" })), skeleton()];
    if (!Object.keys(st.surfaces || {}).length) return [offlineBanner(), viewObserve()];
    var items = allHeld(), sel = findSel(), t = totals();
    var list = h("div", { class: "list" }, items.map(heldCard));
    var detail = sel ? viewDetail(sel) : h("div", { class: "detail empty" }, h("p", { text: items.length ? "Choose a change to read it." : "" }));
    if (!items.length) {
      return [offlineBanner(), h("section", { class: "hero" }, mark("mark"), h("h2", { text: "The gate is quiet" }),
        h("p", { text: "Nothing is waiting for you. Every change the agent makes to itself crosses here first; the ones that need a person will appear in this place." }),
        h("div", { class: "stats" },
          h("div", { class: "stat ok" }, h("b", { text: String(t.accept) }), h("span", { text: "admitted" })),
          h("div", { class: "stat held" }, h("b", { text: String(t.quarantine) }), h("span", { text: "ever held" })),
          h("div", { class: "stat bad" }, h("b", { text: String(t.reject) }), h("span", { text: "rejected" }))))];
    }
    return [offlineBanner(), h("div", { class: "view-h" }, h("h1", { text: "The gate" }), h("span", { class: "count", text: plural(items.length, "change") + " waiting" })),
      h("div", { class: "gate" }, list, detail)];
  }
  function titleOf(it) {
    var p = (it.paths && it.paths[0]) || "(unnamed change)";
    return p;
  }
  function heldCard(it) {
    var chips = [h("span", { class: "chip surface" }, SURFACE[it.surface] || it.surface)];
    if (it.stale) chips.push(h("span", { class: "chip" }, "HEAD moved"));
    (it.reasons || []).slice(0, 3).forEach(function (c) { chips.push(h("span", { class: "chip held", text: reasonInfo(c)[0] })); });
    if (it.taint_tools && it.taint_tools.length) chips.push(h("span", { class: "chip brass", text: "after " + it.taint_tools.join(", ") }));
    var cur = !!(S.sel && S.sel.digest === it.digest);
    return h("button", { class: "held-card" + (it.stale ? " stale" : ""), type: "button", "aria-current": cur ? "true" : null, onclick: function () { S.sel = { surface: it.surface, digest: it.digest }; S.note = ""; loadReview(it); go("gate"); } },
      h("div", { class: "top" }, h("span", { text: "Held" }), h("span", { text: ago(it.at) })),
      h("div", { class: "title", text: titleOf(it) }),
      h("div", { class: "meta", text: plural(it.n_paths || 0, "file") + (it.paths && it.paths.length < (it.n_paths || 0) ? " (first shown)" : "") }),
      h("div", { class: "chips" }, chips));
  }
  function diffLines(lines) {
    var pre = h("pre", { class: "diff", tabindex: "0", "aria-label": "Changes" });
    lines.forEach(function (ln) {
      var c = "ln";
      if (ln.indexOf("+++") === 0 || ln.indexOf("---") === 0) c += " hdr"; else if (ln.charAt(0) === "@") c += " hunk"; else if (ln.charAt(0) === "+") c += " add"; else if (ln.charAt(0) === "-") c += " del";
      pre.appendChild(h("span", { class: c, text: ln || " " }));
    });
    return pre;
  }
  function cliCommand(it, action, reasonText) {
    var st = S.status || {}, base = st.state_dir || "~/.hermes/caduceus", home = base.replace(/\/caduceus\/?$/, ""), root = it.surface === "skills" ? home + "/skills" : it.surface === "memory" ? home + "/memories" : home;
    var b = it.surface === "skills" ? base : base + "/" + it.surface;
    return "caduceus " + (action === "release" ? "release-effect " : "discard-effect ") + it.digest + " --surface " + it.surface + " --skills-root " + root + " --base " + b + " --operator me --reason \"" + (reasonText || "reviewed") + "\"" + (st.policy && st.policy.approval_required ? " --keyring ~/.caduceus/approver.json --signer me" : "");
  }
  function viewDetail(it) {
    var rev = S.review[it.digest], ctl = canAct(), frozen = !!it.frozen;
    var head = [h("button", { class: "back", type: "button", onclick: function () { S.sel = null; renderMain(); } }, icon("back"), "All changes"),
      h("div", { class: "chips" }, [h("span", { class: "chip surface" }, SURFACE[it.surface] || it.surface)].concat(it.taint_tools && it.taint_tools.length ? [h("span", { class: "chip brass", text: "after " + it.taint_tools.join(", ") })] : [], it.stale ? [h("span", { class: "chip" }, "HEAD moved")] : [])),
      h("h2", { text: titleOf(it) }), h("p", { class: "lede", text: "Held " + (ago(it.at) || "earlier") + " · " + plural(it.n_paths || 0, "file") })];
    var why = h("div", { class: "why" });
    (it.reasons || []).forEach(function (c) { var r = reasonInfo(c); why.appendChild(h("div", { class: "reason" }, h("b", { text: r[0] }), h("span", { text: r[1] }))); });
    var body = [h("div", { class: "rule" }, h("i"), h("span", { text: "Why it was held" }), h("i")), why, h("div", { class: "rule" }, h("i"), h("span", { text: "What changes" }), h("i"))];
    if (!rev || rev.loading) body.push(h("div", { class: "skel" }));
    else if (rev.err) body.push(h("div", { class: "banner bad" }, icon("alert"), h("span", { text: "Could not load the diff: " + rev.err })));
    else {
      var files = h("div", { class: "files" });
      (rev.files || []).forEach(function (f, i) {
        var key = it.digest + f.path, open = key in S.open ? S.open[key] : i === 0;
        var b = h("button", { type: "button", "aria-expanded": open ? "true" : "false", onclick: function () { S.open[key] = !open; renderMain(); } }, icon("chev", "chev"), icon("file"), h("span", { class: "p", text: f.path }), h("span", { class: "chip " + (f.status === "added" ? "ok" : ""), text: f.status }));
        files.appendChild(h("div", { class: "file" }, b, open ? diffLines(f.diff) : null, open && f.truncated ? h("p", { class: "note", text: "Diff truncated; showing the first lines." }) : null));
      });
      if (rev.removed && rev.removed.length) files.appendChild(h("div", { class: "file" }, h("button", { type: "button", "aria-expanded": "true" }, icon("trash"), h("span", { class: "p", text: plural(rev.removed.length, "file") + " removed" })), diffLines(rev.removed.map(function (p) { return "- " + p; }))));
      body.push(files);
    }
    var acts = [], hintId = "cad-hold-hint";
    var releaseOff = !ctl.ok || it.stale || frozen || !!S.busy || !rev || !rev.parent;
    acts.push(h("button", { class: "btn", type: "button", disabled: !ctl.ok || !!S.busy || !rev || !rev.parent, onclick: function () { confirmSheet({ title: "Decline this change?", body: "It stays out. The agent keeps its last admitted state and can propose it again.", cta: "Decline", icon: "x", go: function () { act("discard", it); } }); } }, icon("x"), "Decline"));
    acts.push(holdButton({ label: S.busy === "release" ? "Releasing…" : "Hold to release", hintId: hintId, disabled: releaseOff,
      go: function () { act("release", it); },
      confirm: function () { confirmSheet({ title: "Release this change?", body: "It becomes part of the agent's skills, memory or config. Only release what you have read.", cta: "Release", icon: "lock", primary: true, go: function () { act("release", it); } }); } }));
    var note = null;
    if (!ctl.ok) note = whyNot(ctl.why, it);
    else if (it.stale) note = h("p", { class: "hint", id: hintId, text: "The tree has moved on since this was held, so it can no longer be released as it was. Decline it and let the agent propose again." });
    else if (frozen) note = h("p", { class: "hint", id: hintId, text: "Evolution is frozen. You can decline, but not release." });
    else note = h("p", { class: "hint", id: hintId, text: "Press and hold for a second. Your device signs exactly this change." });
    var noteField = ctl.ok ? h("div", { class: "field" }, h("label", { for: "cad-note", text: "Note for the ledger (optional)" }), h("input", { id: "cad-note", type: "text", maxlength: "200", value: S.note, oninput: function (e) { S.note = e.target.value; } })) : null;
    return h("section", { class: "detail card pad-lg", "aria-label": "Held change" }, head, body, noteField, h("div", { class: "actions" }, h("div", { class: "btns" }, acts), h("div", { class: "help" }, note)));
  }
  function whyNot(why, it) {
    var st = S.status || {}, cmd = cliCommand(it, "release", "reviewed");
    var msg = { insecure: "Approving needs a secure page (HTTPS, or localhost). Open this over HTTPS, for example with Tailscale Serve, or approve with the command below.",
      host: "This pane is for reviewing. Approve from your phone's browser (it holds the key), or with the command below.",
      algo: "This browser cannot make an Ed25519 key (needs Chrome 137+, Safari 17+ or Firefox 129+). Approve with the command below.",
      store: "This browser blocks local storage, so it cannot keep a device key. Approve with the command below.",
      checking: "Checking what this device can do…",
      nokey: "This device has no signing key yet. Set one up to approve from here.",
      unenrolled: "This device's key is not enrolled on the host yet." }[why] || "Approving is not available here.";
    var node = [h("p", { class: "hint", id: "cad-hold-hint", text: msg })];
    if (why === "nokey" || why === "unenrolled") node.push(h("button", { class: "btn small", type: "button", onclick: function () { go("devices"); } }, icon("key"), why === "nokey" ? "Set up this device" : "Enrol this device"));
    else if (why !== "checking") node.push(h("div", { class: "cmd" }, cmd, h("button", { class: "iconbtn", type: "button", "aria-label": "Copy command", onclick: function () { copyText(cmd); } }, icon("copy"))));
    return node;
  }

  function viewObserve() {
    var st = S.status, o = st.observe || {}, cmd = "echo '{\"mode\": \"boundary\"}' > " + (st.state_dir || "~/.hermes/caduceus") + "/mode.json";
    return [h("section", { class: "hero" }, mark("mark"), h("h2", { text: "Caduceus is watching" }),
      h("p", { text: "In observe mode it records every skill write and judges none of them. Switch to boundary mode and each change is compared with the last admitted state; anything that is not admitted is restored and shown here." }),
      h("div", { class: "stats" }, h("div", { class: "stat" }, h("b", { text: String(o.skill_mutation_intent || 0) }), h("span", { text: "writes seen" })), h("div", { class: "stat bad" }, h("b", { text: String(o.skill_mutation_blocked || 0) }), h("span", { text: "blocked" })))),
      h("div", { class: "card pad-lg" }, h("h3", { text: "Turn on the gate" }), h("p", { class: "dim", text: "Run this on the machine that runs Hermes, then restart the gateway." }),
        h("div", { class: "cmd" }, cmd, h("button", { class: "iconbtn", type: "button", "aria-label": "Copy command", onclick: function () { copyText(cmd); } }, icon("copy"))))];
  }

  function timelineItem(t) {
    var ic = "diamond", cls = "", title = "", sub = "";
    if (t.type === "verdict") {
      ic = t.decision === "accept" ? "shield" : t.decision === "quarantine" ? "hourglass" : "x"; cls = t.decision === "accept" ? "ok" : t.decision === "quarantine" ? "held" : "bad";
      title = (t.decision === "accept" ? "Admitted" : t.decision === "quarantine" ? "Held" : "Rejected") + " · " + plural(t.n_paths || 0, "file");
      sub = (t.reasons && t.reasons.length ? t.reasons.map(function (c) { return reasonInfo(c)[0]; }).join(" · ") : (t.paths || [])[0] || "") + (t.taint_tools && t.taint_tools.length ? " · after " + t.taint_tools.join(", ") : "");
    } else if (t.type === "released") { ic = "check"; cls = "brass"; title = "Released"; sub = "by " + (t.operator || "operator") + (t.reason ? " · " + t.reason : ""); }
    else if (t.type === "effect_discarded") { ic = "x"; cls = "brass"; title = "Declined"; sub = "by " + (t.operator || "operator") + (t.reason ? " · " + t.reason : ""); }
    else if (t.type === "baseline") { ic = "layers"; title = "Baseline admitted"; sub = plural(t.files || 0, "file") + " became the starting point"; }
    else if (t.type === "baseline_attested") { ic = "pen"; cls = "brass"; title = "Baseline attested"; sub = "by " + (t.operator || "operator"); }
    else if (t.type === "policy_set") { ic = "pen"; title = "Policy updated"; sub = t.policy_digest ? short(t.policy_digest) : ""; }
    else if (t.type === "device_added") { ic = "key"; cls = "brass"; title = "Device enrolled"; sub = t.name || ""; }
    else if (t.type === "device_removed") { ic = "key"; title = "Device removed"; sub = t.name || ""; }
    else if (t.type === "approval_used") { ic = "pen"; cls = "brass"; title = "Signature accepted"; sub = (t.device || "") + " · " + (t.action || ""); }
    else if (t.type === "check_registered") { ic = "shield"; title = "Check registered"; sub = (t.skill || "") + " · " + (t.check_id || ""); }
    else { title = t.type; }
    return h("li", null, h("span", { class: "node " + cls }, icon(ic)), h("div", null, h("div", { class: "t", text: title }), sub ? h("div", { class: "s", text: sub }) : null), h("time", { text: ago(t.at) }));
  }
  function viewLedger() {
    var st = S.status; if (!st) return skeleton();
    var sfs = Object.keys(st.surfaces || {});
    if (!sfs.length) return viewObserve();
    if (!S.tlSurface || sfs.indexOf(S.tlSurface) < 0) S.tlSurface = sfs[0];
    var sf = st.surfaces[S.tlSurface], led = sf.ledger || {}, items = S.timeline;
    var out = [offlineBanner(), h("div", { class: "view-h" }, h("h1", { text: "Ledger" }), sfs.length > 1 ? h("div", { class: "seg", role: "group", "aria-label": "Surface" }, sfs.map(function (s) {
      return h("button", { type: "button", "aria-pressed": s === S.tlSurface ? "true" : "false", onclick: function () { S.tlSurface = s; S.timeline = null; renderMain(); loadTimeline(); } }, SURFACE[s]);
    })) : null)];
    out.push(h("div", { class: "card" }, h("div", { class: "health" }, led.ok ? h("span", { class: "ok" }, icon("shield")) : h("span", { class: "bad" }, icon("alert")),
      h("div", null, h("div", { class: led.ok ? "" : "bad", text: led.ok ? "Chain verified" : "The ledger does not verify" }), h("div", { class: "faint mono", text: led.ok ? plural(led.size, "entry") + " · root " + short(led.root) : (led.error || "unknown error") })))));
    if (items == null) { out.push(skeleton()); return out; }
    var chain = items.filter(function (t) { return t.type === "verdict" || t.type === "released" || t.type === "baseline"; }).slice().reverse().slice(-60);
    if (chain.length) out.push(h("div", { class: "rule" }, h("i"), h("span", { text: "Recent verdicts" }), h("i")), h("div", { class: "chain", "aria-hidden": "true" }, chain.map(function (t) {
      return h("i", { class: t.type === "baseline" ? "base" : t.type === "released" ? "rel" : t.decision === "accept" ? "" : t.decision === "quarantine" ? "held" : "bad", title: t.type });
    })));
    if (!items.length) { out.push(h("p", { class: "dim", text: "Nothing has crossed the gate yet." })); return out; }
    var day = null, ul = null;
    items.forEach(function (t) {
      var d = dayOf(t.at);
      if (d !== day || !ul) { day = d; out.push(h("div", { class: "rule" }, h("i"), h("span", { text: d }), h("i"))); ul = h("ul", { class: "tl" }); out.push(ul); }
      ul.appendChild(timelineItem(t));
    });
    return out;
  }

  function viewState() {
    var st = S.status; if (!st) return skeleton();
    var cards = Object.keys(st.surfaces || {}).map(function (s) {
      var sf = st.surfaces[s];
      if (sf.error) return h("div", { class: "card" }, h("h3", { text: SURFACE[s] }), h("div", { class: "banner bad" }, icon("alert"), h("span", { text: sf.error + (sf.detail ? ": " + sf.detail : "") })));
      var c = sf.counts || {}, tot = (c.accept || 0) + (c.quarantine || 0) + (c.reject || 0) || 1, held = (sf.held || []).length;
      var bar = h("div", { class: "bar", "aria-hidden": "true" }, h("i", { class: "ok" }), h("i", { class: "held" }), h("i", { class: "bad" }));
      bar.childNodes[0].style.width = (100 * (c.accept || 0) / tot) + "%"; bar.childNodes[1].style.width = (100 * (c.quarantine || 0) / tot) + "%"; bar.childNodes[2].style.width = (100 * (c.reject || 0) / tot) + "%";
      return h("div", { class: "card pad-lg" }, h("div", { class: "view-h" }, h("h2", { text: SURFACE[s] }), h("span", { class: "chip " + (held ? "held" : "ok"), text: held ? plural(held, "change") + " held" : "all clear" })),
        bar,
        h("div", { class: "legend" }, h("span", null, h("b", { text: String(c.accept || 0) }), " admitted"), h("span", null, h("b", { text: String(c.quarantine || 0) }), " held"), h("span", null, h("b", { text: String(c.reject || 0) }), " rejected")),
        h("dl", { class: "kv" }, h("dt", { text: "HEAD" }), h("dd", { class: "mono", text: short(sf.head) }), h("dt", { text: "Provenance" }), h("dd", { text: sf.provenance === "quarantine" ? "holds writes after untrusted content" : String(sf.provenance) }),
          h("dt", { text: "Frozen" }), h("dd", { text: sf.frozen ? "yes" : "no" }), h("dt", { text: "Ledger" }), h("dd", { class: sf.ledger && sf.ledger.ok ? "" : "bad", text: sf.ledger && sf.ledger.ok ? plural(sf.ledger.size, "entry") + ", verified" : "does not verify" }),
          h("dt", { text: "Policy" }), h("dd", { class: "mono", text: short(sf.policy_digest) }), h("dt", { text: "Approvals" }), h("dd", { text: sf.approval_required ? "device signature required" : "operator name" })));
    });
    return [offlineBanner(), h("div", { class: "view-h" }, h("h1", { text: "State" }), h("span", { class: "count", text: "mode: " + st.mode })), h("div", { class: "grid2" }, cards)];
  }

  function viewDevices() {
    var st = S.status, out = [offlineBanner(), h("div", { class: "view-h" }, h("h1", { text: "Devices" }))];
    var req = st && st.policy && st.policy.approval_required;
    out.push(h("p", { class: "dim", text: "A device holds a signing key that never leaves it. To release or decline a change, the device signs exactly that change and the host only checks the signature. Use your phone: a key on the same machine as the agent is only as safe as that machine." }));
    out.push(h("div", { class: "rule" }, h("i"), h("span", { text: "This device" }), h("i")));
    var me = h("div", { class: "card pad-lg" });
    var loopback = /^(localhost|127\.|\[?::1)/.test((win.location && win.location.hostname) || "");
    if (!S.sign) me.appendChild(h("div", { class: "skel" }));
    else if (!S.sign.ok) {
      var msg = { host: "This pane only reviews. Open Caduceus in your phone's browser to approve from a device that holds a key.", insecure: "Signing needs a secure page. Open Caduceus over HTTPS (for example Tailscale Serve) or on localhost.",
        algo: "This browser cannot make an Ed25519 key. Use Chrome 137+, Safari 17+ or Firefox 129+.", store: "This browser blocks local storage, so it cannot keep a key." }[S.sign.why];
      me.appendChild(h("div", { class: "banner" }, icon("lock"), h("span", { text: msg })));
    } else if (!S.device) {
      var nm = h("input", { id: "cad-dev-name", type: "text", value: /Mobi|Android|iPhone/.test((win.navigator && win.navigator.userAgent) || "") ? "phone" : "laptop", maxlength: "32", autocapitalize: "none", spellcheck: "false" });
      add(me, [h("h3", { text: "No key on this device" }), h("p", { class: "dim", text: "Create a key here. Only its public half is ever shown or sent." }),
        loopback ? h("div", { class: "banner" }, icon("alert"), h("span", { text: "This browser reaches Hermes on localhost, so it is probably the machine the agent runs on. Prefer your phone." })) : null,
        h("div", { class: "field" }, h("label", { for: "cad-dev-name", text: "Name for this device" }), nm),
        h("div", { class: "mt" }, h("button", { class: "btn primary", type: "button", onclick: function () {
          var n = (nm.value || "").trim().toLowerCase();
          if (!/^[a-z0-9][a-z0-9._-]{0,31}$/.test(n)) { toast("bad", "Use 1 to 32 characters: a-z 0-9 . _ -"); return; }
          createDevice(n).then(function () { toast("ok", "Key created on this device"); renderMain(); }, function (e) { toast("bad", e.message); });
        } }, icon("key"), "Create device key"))]);
    } else {
      var as = enrolledAs(), base = (st && st.state_dir) || "~/.hermes/caduceus", cmd = "caduceus device-add --base " + base + " --name " + S.device.name + " --pub " + S.device.pub + " --operator me";
      add(me, [h("div", { class: "view-h" }, h("h2", { text: S.device.name }), h("span", { class: "chip " + (as ? "ok" : "held"), text: as ? "enrolled" : "not enrolled" })),
        h("div", { class: "fp", text: S.device.fp }),
        h("p", { class: "faint", text: "Compare this fingerprint with the one the host prints when you enrol." }),
        as ? h("p", { class: "dim", text: "Enrolled as " + as + ". This device can release or decline held changes." })
           : [h("p", { class: "dim", text: "Run this once on the machine that runs Hermes. Enrolling is deliberately not possible from this page." }), h("div", { class: "cmd" }, cmd, h("button", { class: "iconbtn", type: "button", "aria-label": "Copy command", onclick: function () { copyText(cmd); } }, icon("copy")))],
        h("div", { class: "mt" }, h("button", { class: "btn small quiet", type: "button", onclick: function () {
          confirmSheet({ title: "Remove the key from this device?", body: "You will need to create and enrol a new key to approve from here.", cta: "Remove key", icon: "trash", go: function () { forgetDevice().then(function () { toast("ok", "Key removed"); renderMain(); }); } });
        } }, icon("trash"), "Remove key"))]);
    }
    out.push(me);
    out.push(h("div", { class: "rule" }, h("i"), h("span", { text: "Enrolled on the host" }), h("i")));
    var list = h("div", { class: "card" }), dv = (st && st.devices) || { list: [] };
    if (dv.ok === false) list.appendChild(h("div", { class: "banner bad" }, icon("alert"), h("span", { text: "The device registry does not match the ledger: " + (dv.error || "") })));
    else if (!(dv.list || []).length) list.appendChild(h("p", { class: "dim", text: "No devices are enrolled." }));
    (dv.list || []).forEach(function (d) {
      list.appendChild(h("div", { class: "devrow" }, icon(/phone|mobile|iphone|android/.test(d.name) ? "phone" : "laptop"), h("div", { class: "grow" }, h("div", { text: d.name + (d.label ? " · " + d.label : "") }), h("div", { class: "faint mono", text: d.fingerprint })),
        d.added_at ? h("span", { class: "faint", text: ago(d.added_at) }) : null));
    });
    out.push(list);
    out.push(h("div", { class: "rule" }, h("i"), h("span", { text: "Policy" }), h("i")));
    out.push(h("div", { class: "card" }, h("p", { text: req ? "Releases and declines need a device signature." : "Releases and declines accept an operator name. A device signature is still checked whenever one is sent." }),
      req ? null : h("p", { class: "faint", text: "Require signatures with caduceus policy-set (approval_required: true)." })));
    return out;
  }

  /* ---------- mount ---------- */
  function mount() {
    root.classList.add("cad"); root.textContent = "";
    el.top = h("header", { class: "cad-top" }); el.nav = h("nav", { class: "cad-nav", "aria-label": "Caduceus sections" });
    el.main = h("main", { class: "cad-main" }); el.toasts = h("div", { class: "toasts", "aria-live": "polite" });
    root.appendChild(el.top); root.appendChild(h("div", { class: "cad-body" }, h("div", { class: "cad-shell" }, el.nav, el.main))); root.appendChild(el.toasts);
    var saved = storeGet("caduceus.theme"); if (saved === "dark" || saved === "light") S.theme = saved; applyTheme();
    listen(doc, "keydown", function (e) { if (e.key === "Escape" && modalNode) closeSheet(); });
    listen(doc, "visibilitychange", function () { if (!doc.hidden) refresh(); });
    render();
    detectSigning().then(function (r) { S.sign = r; return loadDevice(); }).then(function () { if (S.tab === "devices" || S.sel) renderMain(); });
    refresh();
    every(function () { if (!doc.hidden) refresh(); }, 5000);
  }
  function destroy() {
    alive = false;
    timers.forEach(function (t) { if (typeof t === "function") t(); }); listeners.forEach(function (f) { f(); }); closeSheet();
    root.textContent = ""; root.classList.remove("cad", "detail-open"); root.removeAttribute("data-theme");
  }
  mount();
  return { destroy: destroy, refresh: refresh };
}
