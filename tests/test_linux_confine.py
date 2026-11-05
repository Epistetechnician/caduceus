import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

from caduceus import linux_confine

LAUNCHER = linux_confine.__file__
LINUX = sys.platform.startswith("linux") and linux_confine.abi_version() > 0


def confined(code, workdir, env=None):
    return subprocess.run([sys.executable, "-I", LAUNCHER, "--workdir", workdir, "--", sys.executable, "-I", "-c", code],
                          capture_output=True, text=True, timeout=30, env=env)


@unittest.skipUnless(LINUX, "needs Linux with Landlock")
class LinuxConfineTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="cf-work-")
        self.other = tempfile.mkdtemp(prefix="cf-other-")
        with open(os.path.join(self.other, "secret.txt"), "w") as fh:
            fh.write("secret")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)
        shutil.rmtree(self.other, ignore_errors=True)

    def test_baseline_unconfined_can_do_everything_below(self):  # keeps the denials below meaningful
        self.assertEqual(open(os.path.join(self.other, "secret.txt")).read(), "secret")
        open(os.path.join(self.other, "w.txt"), "w").write("x")

    def test_can_write_inside_workdir(self):
        r = confined("open('ok.txt','w').write('x'); print(open('ok.txt').read())", self.work)
        r2 = confined("import os; p=os.path.join(%r,'ok.txt'); open(p,'w').write('x'); print(open(p).read())" % self.work, self.work)
        self.assertEqual((r2.returncode, r2.stdout.strip()), (0, "x"), r2.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.work, "ok.txt")))
        del r

    def test_cannot_write_create_remove_or_truncate_elsewhere(self):
        for code in ("open(%r,'w')" % os.path.join(self.other, "new.txt"),
                     "import os; os.remove(%r)" % os.path.join(self.other, "secret.txt"),
                     "open(%r,'a').write('x')" % os.path.join(self.other, "secret.txt"),
                     "import os; os.rename(%r, %r)" % (os.path.join(self.other, "secret.txt"), os.path.join(self.work, "stolen.txt"))):
            r = confined(code, self.work)
            self.assertNotEqual(r.returncode, 0, code)
            self.assertIn("PermissionError", r.stderr, code)
        self.assertEqual(open(os.path.join(self.other, "secret.txt")).read(), "secret")

    def test_cannot_read_outside_the_allowed_roots(self):
        r = confined("print(open(%r).read())" % os.path.join(self.other, "secret.txt"), self.work)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PermissionError", r.stderr)

    def test_system_files_stay_readable_so_tools_can_run(self):
        r = confined("import os; print(len(os.listdir('/usr')) > 0, os.path.exists('/etc'))", self.work)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "True True"), r.stderr)

    def test_cannot_exec_a_binary_planted_outside_the_workdir_roots(self):
        planted = os.path.join(self.other, "true")
        shutil.copy("/bin/true" if os.path.exists("/bin/true") else "/usr/bin/true", planted)
        os.chmod(planted, 0o755)
        r = confined("import subprocess; subprocess.run([%r], check=True)" % planted, self.work)
        self.assertNotEqual(r.returncode, 0)

    def test_tcp_connect_to_a_local_listener_is_denied(self):
        if linux_confine.abi_version() < 4:
            self.skipTest("TCP restriction needs Landlock ABI 4 (the seccomp filter still blocks it; see the next test)")
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        try:
            r = confined("import socket; socket.create_connection(('127.0.0.1', %d), 2)" % srv.getsockname()[1], self.work)
        finally:
            srv.close()
        self.assertNotEqual(r.returncode, 0)

    def test_inet_and_udp_sockets_are_denied_but_unix_sockets_work(self):
        r = confined("import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM)", self.work)
        self.assertIn("PermissionError", r.stderr)
        r = confined("import socket; socket.socket(socket.AF_INET6, socket.SOCK_STREAM)", self.work)
        self.assertIn("PermissionError", r.stderr)
        r = confined("import socket; a, b = socket.socketpair(); a.send(b'x'); print(b.recv(1))", self.work)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "b'x'"), r.stderr)

    def test_failure_to_confine_exits_71_and_runs_nothing(self):
        env = dict(os.environ, CADUCEUS_FORCE_NO_LANDLOCK="1")
        marker = os.path.join(self.work, "ran.txt")
        r = confined("open(%r,'w').write('x')" % marker, self.work, env)
        self.assertEqual(r.returncode, 71)
        self.assertIn("caduceus-confine", r.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_run_check_uses_it_and_reports_pass_fail_and_not_unavailable(self):
        from caduceus import checks

        self.assertTrue(checks.confinement_available())
        skill = os.path.join(self.work, "skill")
        os.makedirs(skill)
        open(os.path.join(skill, "t.py"), "w").write("print('ok')\n")
        self.assertEqual(checks.run_check(skill, {"id": "a", "argv": [sys.executable, "t.py"], "timeout_s": 10, "expect_exit": 0}), "Pass")
        self.assertEqual(checks.run_check(skill, {"id": "b", "argv": [sys.executable, "t.py"], "timeout_s": 10, "expect_exit": 3}), "Fail")
        probe = "import socket,sys\ntry:\n socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\nexcept OSError:\n sys.exit(7)\n"
        open(os.path.join(skill, "n.py"), "w").write(probe)
        self.assertEqual(checks.run_check(skill, {"id": "c", "argv": [sys.executable, "n.py"], "timeout_s": 10, "expect_exit": 7}), "Pass")


class FailClosedElsewhere(unittest.TestCase):
    def test_launcher_refuses_bad_arguments(self):
        self.assertEqual(linux_confine.main([]), 71)
        self.assertEqual(linux_confine.main(["--workdir", "/nonexistent-dir-xyz", "--", "true"]), 71)


if __name__ == "__main__":
    unittest.main()
