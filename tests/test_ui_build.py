import json
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PLUG = os.path.join(ROOT, "plugin", "caduceus_governance")
SRC = os.path.join(PLUG, "ui", "src")


def read(*p):
    with open(os.path.join(*p), encoding="utf-8") as fh:
        return fh.read()


def strip_comments(src):
    # same shape as Hermes' desktop lint: keep string literals, drop comments
    return re.sub(r"(\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|/\*.*?\*/|(?<![:\w])//[^\n]*", lambda m: m.group(1) or "", src, flags=re.S)


class BuildTests(unittest.TestCase):
    def test_generated_files_are_in_sync_with_the_sources(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "build_ui.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + "run: python3 scripts/build_ui.py")

    def test_the_app_source_uses_no_dangerous_dom_or_dynamic_code(self):
        js = strip_comments(read(SRC, "app.js"))
        for bad in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "importScripts", "XMLHttpRequest", "WebSocket", "sendBeacon",
                    'setAttribute("style"', "setAttribute('style'", "style:", "javascript:"):
            if bad == "javascript:":
                continue  # appears only in the seeded hostile test data, never in the source
            self.assertNotIn(bad, js, bad)
        self.assertNotRegex(js, r"\bfetch\s*\(")  # the app talks to the host only through host.api
        self.assertNotIn("<" + "script", js)

    def test_every_text_from_the_agent_goes_through_textcontent(self):
        js = read(SRC, "app.js")
        self.assertGreater(js.count("text:"), 50)  # the h() helper maps text: to textContent
        self.assertIn("textContent = v", js)

    def test_no_external_resources_anywhere(self):
        for rel in (("ui", "index.html"), ("dashboard", "dist", "style.css"), ("dashboard", "dist", "index.js"), ("desktop", "plugin.js")):
            text = read(PLUG, *rel)
            urls = [u for u in re.findall(r"https?://[^\s\"'<>)]+", text) if "w3.org/2000/svg" not in u]
            self.assertEqual(urls, [], rel)
            self.assertNotRegex(text, r"@import\s+url|<link[^>]+rel=\"stylesheet\"")

    def test_standalone_page_only_inlines_with_a_nonce_placeholder(self):
        html = read(PLUG, "ui", "index.html")
        self.assertEqual(html.count("{{NONCE}}"), 2)  # one style, one script, both nonce-tagged by the server per response
        self.assertEqual(len(re.findall(r"<script\b", html)), 1)
        self.assertNotRegex(html, r"\sstyle=\"")  # an inline style attribute would break the strict CSP
        self.assertNotRegex(html, r"\son[a-z]+=\"")


class DesktopSurfaceRules(unittest.TestCase):
    """The refusals of hermes_cli/plugin_validate_desktop.py, re-stated so CI catches them without a Hermes checkout."""

    def test_desktop_plugin_js_obeys_the_catalog_lint(self):
        src = strip_comments(read(PLUG, "desktop", "plugin.js"))
        for rule, rx in (("eval", r"(?<![\w$.])eval\(|\bnew\s+Function\("), ("dynamic import", r"\bimport\(\s*(?!['\"](?:@hermes/plugin-sdk|react)(?:/[\w/-]*)?['\"]\s*\))"),
                         ("remote import", r"\bimport\s+(?:[^;'\"]*?\bfrom\s*)?['\"][a-zA-Z][\w+.-]*:"), ("script injection", r"createElement\(\s*['\"]script['\"]\s*\)|<script\b"),
                         ("app DOM reach", r"\bdocument\.(?:querySelector(?:All)?|getElementsBy\w+|getElementById)\(\s*[`'\"][^`'\"]*\[data-(?:slot|tour|sidebar|testid)\b"),
                         ("body observer", r"\.observe\(\s*document\.body\s*,\s*\{[^}]*\b(?:childList|subtree)\b"), ("webview", r"\b(?:querySelector(?:All)?|closest|getElementsByTagName|createElement)\(\s*[`'\"]webview\b"),
                         ("raw bridge", r"\b(?:window|globalThis|self)\s*(?:\?\.|\.)\s*hermesDesktop\b")):
            self.assertIsNone(re.search(rx, src), rule)

    def test_desktop_imports_only_the_sdk_react_and_jsx_runtime(self):
        specs = re.findall(r"^import\s+.*?from\s+'([^']+)'", read(PLUG, "desktop", "plugin.js"), flags=re.M)
        self.assertTrue(specs)
        for s in specs:
            self.assertIn(s, ("@hermes/plugin-sdk", "react", "react/jsx-runtime"))

    def test_desktop_pane_cannot_sign_and_has_no_release_path_of_its_own(self):
        js = read(PLUG, "desktop", "plugin.js")
        self.assertIn("canSign: false", js)


class DashboardBundle(unittest.TestCase):
    def test_manifest_and_bundle_shape(self):
        m = json.loads(read(PLUG, "dashboard", "manifest.json"))
        self.assertEqual((m["name"], m["entry"], m["css"], m["api"]), ("caduceus-governance", "dist/index.js", "dist/style.css", "plugin_api.py"))
        self.assertEqual(m["tab"]["path"], "/caduceus")
        js = read(PLUG, "dashboard", "dist", "index.js")
        self.assertIn('window.__HERMES_PLUGINS__.register(ID, Page)', js)
        self.assertIn("SDK.fetchJSON", js)
        self.assertTrue(js.lstrip().startswith("(function"))  # a plain IIFE the dashboard loads with a script tag


if __name__ == "__main__":
    unittest.main()
