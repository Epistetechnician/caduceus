import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class VendorTests(unittest.TestCase):
    def test_vendored_plugin_copy_is_in_sync(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "vendor_plugin.py"), "--check"])
        self.assertEqual(r.returncode, 0, "run: python3 scripts/vendor_plugin.py")


if __name__ == "__main__":
    unittest.main()
