import os
import subprocess
import sys
import unittest

DEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "demo_hackathon.py")


class DemoRuns(unittest.TestCase):
    def test_demo_runs_and_tells_the_story(self):
        r = subprocess.run([sys.executable, DEMO], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        for needle in ("provenance:Quarantine", "hostile line live: False", "can no longer be released", "verify-ledger:", "does NOT catch"):
            self.assertIn(needle, out)


if __name__ == "__main__":
    unittest.main()
