import importlib.util
import json
import os
import re
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PLUGIN = os.path.join(ROOT, "plugin", "caduceus_governance")
MAGIC = (b"\x7fELF", b"MZ", b"\x00asm", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


def _load():
    spec = importlib.util.spec_from_file_location("cad_plugin_pkg_test", os.path.join(PLUGIN, "__init__.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class PackageRules(unittest.TestCase):
    def test_no_binaries_or_bytecode(self):  # catalog rule 17
        for d, _, fs in os.walk(PLUGIN):
            if "__pycache__" in d:
                continue
            for f in fs:
                self.assertFalse(f.endswith((".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".wasm")), f)
                with open(os.path.join(d, f), "rb") as fh:
                    head = fh.read(4)
                self.assertFalse(any(head.startswith(m) for m in MAGIC), os.path.join(d, f))

    def test_manifest_hooks_equal_registered_hooks(self):  # catalog rule 6
        declared, reg = [], []
        for line in open(os.path.join(PLUGIN, "plugin.yaml")):
            if line.startswith("  - "):
                declared.append(line[4:].strip())

        class Ctx:
            def register_hook(self, name, fn):
                reg.append(name)

        _load().register(Ctx())
        self.assertEqual(sorted(declared), sorted(reg))

    def test_no_forbidden_surfaces_in_plugin_source(self):  # rules 3, 9, 11: no self-update, core patching or credential reads
        for d, _, fs in os.walk(PLUGIN):
            if "__pycache__" in d:
                continue
            for f in fs:
                if not f.endswith(".py"):
                    continue
                src = open(os.path.join(d, f)).read()
                for bad in ("urllib", "import requests", "socket", "auth.json", "sys.modules[", "setattr(", "os.system", "eval(", " exec("):
                    self.assertNotIn(bad, src, "%s uses %s" % (os.path.join(d, f), bad))
                if "_vendor" not in d:  # the vendored kernel carries .env only inside its injection-detection regexes
                    self.assertIsNone(re.search(r"\.env\b", src), os.path.join(d, f))

    def test_fresh_observe_install_writes_only_a_ledger_line(self):  # R5
        m = _load()
        with tempfile.TemporaryDirectory() as t:
            base = os.path.join(t, "caduceus")
            self.assertIsNone(m.decide("skill_manage", {"operations": [{"action": "create", "name": "x"}]}, base))
            self.assertEqual(os.listdir(base), ["ledger.jsonl"])
            self.assertEqual(sum(1 for _ in open(os.path.join(base, "ledger.jsonl"))), 1)
            self.assertNotIn('"name"', open(os.path.join(base, "ledger.jsonl")).read())  # digests and action names only, never call content


if __name__ == "__main__":
    unittest.main()
