import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PLUGIN = os.path.join(ROOT, "plugin", "caduceus_governance")


class DesktopPackageRules(unittest.TestCase):
    def test_desktop_js_stays_inside_the_sdk_surface(self):  # catalog rule 8
        src = open(os.path.join(PLUGIN, "desktop", "plugin.js")).read()
        imports = [ln for ln in src.splitlines() if ln.startswith("import ")]
        for ln in imports:
            self.assertTrue(any(f"'{m}'" in ln for m in ("@hermes/plugin-sdk", "react", "react/jsx-runtime")), ln)
        for bad in ("eval(", "new Function", "import(", "document.", "window.", "MutationObserver", "prototype", "data-slot", "localStorage", "fetch("):
            self.assertNotIn(bad, src)

    def test_the_view_has_no_release_control_and_the_api_no_mutating_route(self):
        js = open(os.path.join(PLUGIN, "desktop", "plugin.js")).read()
        api = open(os.path.join(PLUGIN, "dashboard", "plugin_api.py")).read()
        self.assertNotIn("method: 'POST'", js)
        self.assertNotIn("@router.post", api)
        self.assertNotIn("@router.put", api)
        self.assertNotIn("@router.delete", api)
        self.assertEqual(json.load(open(os.path.join(PLUGIN, "dashboard", "manifest.json"))), {"name": "caduceus-governance", "api": "plugin_api.py"})


def _fastapi():
    try:
        import fastapi  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_fastapi(), "fastapi not installed (it ships with Hermes)")
class StatusRoutes(unittest.TestCase):
    def test_status_and_review_routes_over_a_real_kernel(self):
        import asyncio

        spec = importlib.util.spec_from_file_location("cad_plugin_api_test", os.path.join(PLUGIN, "dashboard", "plugin_api.py"))
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        from caduceus.transition import Kernel, Policy

        with tempfile.TemporaryDirectory() as t:
            os.environ["HERMES_HOME"] = t
            try:
                skills = os.path.join(t, "skills", "dev", "a")
                os.makedirs(skills)
                open(os.path.join(skills, "SKILL.md"), "w").write("---\nname: a\ndescription: a\n---\nx\n")
                base = os.path.join(t, "caduceus")
                os.makedirs(base)
                json.dump({"mode": "boundary", "live": True}, open(os.path.join(base, "mode.json"), "w"))
                k = Kernel(os.path.join(t, "skills"), base, Policy(), clock=lambda: 1000, allow_live=True)
                k.reconcile()
                k.observe_taint("s", "web_extract", "page")
                open(os.path.join(skills, "SKILL.md"), "a").write("a new step\n")
                ed = k.reconcile(taint=k.taint_scope("s"))["verdict"]["effect_digest"]
                st = asyncio.run(api.status())
                self.assertEqual(st["surfaces"]["skills"]["held_effects"], [ed])
                rv = asyncio.run(api.review("skills", ed))
                self.assertTrue(any("+a new step" in ln for f in rv["files"] for ln in f["diff"]))
                from fastapi import HTTPException
                for surface, digest in (("skills", "z" * 64), ("nope", ed), ("skills", "../x")):
                    with self.assertRaises(HTTPException):
                        asyncio.run(api.review(surface, digest))
            finally:
                os.environ.pop("HERMES_HOME", None)


if __name__ == "__main__":
    unittest.main()
