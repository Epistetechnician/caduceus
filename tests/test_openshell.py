import os
import tempfile
import unittest

from caduceus.openshell import render


def sections(out):
    ro, rest = out.split("  read_only:\n")[1].split("  read_write:\n")
    rw = rest.split("landlock:")[0]
    return [x.strip()[2:] for x in ro.splitlines() if x.strip()], [x.strip()[2:] for x in rw.splitlines() if x.strip()]


class OpenShellPolicyTests(unittest.TestCase):
    def test_documented_schema_and_hard_requirement(self):
        out = render("/h/.hermes/skills", "/h/.hermes/caduceus")
        self.assertIn("version: 1\nfilesystem_policy:\n  include_workdir: false", out)
        self.assertIn("landlock:\n  compatibility: hard_requirement", out)
        self.assertNotIn("best_effort", out)  # best_effort silently drops the rules, so it is never emitted

    def test_skills_read_only_never_read_write_and_root_never_listed(self):
        ro, rw = sections(render("/h/.hermes/skills", "/h/.hermes/caduceus"))
        self.assertIn("/h/.hermes/skills", ro)
        self.assertNotIn("/h/.hermes/skills", rw)
        self.assertIn("/h/.hermes/caduceus", rw)
        self.assertNotIn("/", rw)

    def test_hermes_home_entries_are_listed_individually_except_skills(self):
        with tempfile.TemporaryDirectory() as t:
            for d in ("skills", "memories", "caduceus", "cache"):
                os.makedirs(os.path.join(t, d))
            open(os.path.join(t, "config.yaml"), "w").close()
            ro, rw = sections(render(os.path.join(t, "skills"), os.path.join(t, "caduceus"), t))
            self.assertIn(os.path.join(t, "memories"), rw)
            self.assertIn(os.path.join(t, "config.yaml"), rw)
            self.assertIn(os.path.join(t, "skills"), ro)
            self.assertNotIn(os.path.join(t, "skills"), rw)
            self.assertNotIn(t, rw)  # the home as a whole would re-open the skills tree

    def test_hostile_paths_refused(self):
        for bad in ("relative/skills", '/a"b', "/a\nb", "/a\\b", "/a/../b", "/", "/a: b", "/a#b"):
            with self.assertRaises(ValueError):
                render(bad, "/c")

    def test_list_cap(self):
        with tempfile.TemporaryDirectory() as t:
            for i in range(300):
                open(os.path.join(t, "f%d" % i), "w").close()
            with self.assertRaises(ValueError):
                render(os.path.join(t, "skills"), os.path.join(t, "c"), t)


if __name__ == "__main__":
    unittest.main()
