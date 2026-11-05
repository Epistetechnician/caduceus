import unittest

from caduceus.openshell import render


class OpenShellPolicyTests(unittest.TestCase):
    def test_skills_read_only_caduceus_dir_writable(self):
        out = render("/h/.hermes/skills", "/h/.hermes/caduceus")
        self.assertIn("version: 1", out)
        ro, rw = out.split("read_only:")[1].split("read_write:")
        self.assertIn('"/h/.hermes/skills"', ro)
        self.assertNotIn('"/h/.hermes/skills"', rw)
        self.assertIn('"/h/.hermes/caduceus"', rw)
        self.assertIn("UNVERIFIED", out)

    def test_hostile_paths_refused(self):
        for bad in ("relative/skills", '/a"b', "/a\nb", "/a\\b"):
            with self.assertRaises(ValueError):
                render(bad, "/c")


if __name__ == "__main__":
    unittest.main()
