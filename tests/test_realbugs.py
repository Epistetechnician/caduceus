import os
import subprocess
import tempfile
import unittest

from caduceus import realbugs


def git(d, *a):
    subprocess.run(["git", "-C", d, *a], check=True, capture_output=True,
                   env={"PATH": os.environ["PATH"], "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


class RealBugs(unittest.TestCase):
    def test_split_is_deterministic_and_covers_all_splits(self):
        s = {realbugs.split_of_repo("r%d" % i, 7) for i in range(200)}
        self.assertEqual(s, {"fit", "tune", "assess"})
        self.assertEqual(realbugs.split_of_repo("attrs", 7), realbugs.split_of_repo("attrs", 7))

    def test_build_reads_parent_source_and_ids_are_stable(self):
        d = tempfile.mkdtemp()
        root = os.path.join(d, "lib")
        os.makedirs(root)
        git(root, "init", "-q")
        open(os.path.join(root, "m.py"), "w").write("def f():\n    return 1\n")
        git(root, "add", "."); git(root, "commit", "-qm", "a")
        open(os.path.join(root, "m.py"), "w").write("def f():\n    return 2\n")
        git(root, "commit", "-qam", "b")
        c = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        e = [{"repo": "lib", "commit": c, "path": "m.py", "tests": ["tests/test_m.py"]}]
        a, b = realbugs.build(d, e, 1), realbugs.build(d, e, 1)
        self.assertEqual(a[0]["task_id"], b[0]["task_id"])
        self.assertIn("return 1", a[0]["mutated_source"])
        self.assertEqual(realbugs.verify_repair(d, a[0], "def f(:\n")["reason"], "syntax_error")
        self.assertEqual(realbugs.verify_repair(d, a[0], a[0]["mutated_source"])["reason"], "unchanged")


if __name__ == "__main__":
    unittest.main()
