"""Install the plugin the way the catalog does and run it: `hermes plugins install file://<clone>#plugin/caduceus_governance --ref <40-hex sha>` (a blobless clone with a sparse
checkout of that subdirectory only), into a throw-away HERMES_HOME, then drive the real Hermes loop with the repository's own `caduceus` made un-importable, so only the
installed copy's vendored kernel can run. Proves the self-contained-package claim (PRD R1) through the real install path, not just the validator.
Usage: python3 scripts/verify_catalog_install.py --hermes ~/.hermes/hermes-agent [--sha <commit>]   (default: the current HEAD, which must be committed)"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes", required=True)
    ap.add_argument("--sha")
    ns = ap.parse_args()
    hermes = os.path.abspath(os.path.expanduser(ns.hermes))
    py = os.path.join(hermes, "venv", "bin", "python")
    sha = ns.sha or run(["git", "rev-parse", "HEAD"], cwd=REPO).stdout.strip()
    tmp = tempfile.mkdtemp(prefix="caduceus-install-")
    try:
        src, home = os.path.join(tmp, "src"), os.path.join(tmp, "hh")
        os.makedirs(home)
        assert run(["git", "clone", "-q", "file://" + REPO, src]).returncode == 0
        run(["git", "config", "uploadpack.allowFilter", "true"], cwd=src)
        run(["git", "config", "uploadpack.allowAnySHA1InWant", "true"], cwd=src)
        env = dict(os.environ, HERMES_HOME=home, PYTHONPATH="", HERMES_DISABLE_LAZY_INSTALLS="1")
        r = run([py, "-m", "hermes_cli.main", "plugins", "install", "file://%s#plugin/caduceus_governance" % src, "--ref", sha, "--no-enable", "--yes-deps"], env=env, cwd=hermes)
        installed = os.path.join(home, "plugins", "caduceus-governance")
        files = sorted(os.listdir(installed)) if os.path.isdir(installed) else []
        print("install rc=%d; installed files: %s" % (r.returncode, files))
        if r.returncode != 0 or "_vendor" not in files or "plugin.yaml" not in files:
            print(r.stdout[-800:], r.stderr[-800:])
            return 1
        leak = [f for f in files if f in ("caduceus", "tests", "docs", "scripts")]
        if leak:
            print("repository directories leaked into the install:", leak)
            return 1
        e = run([py, os.path.join(REPO, "scripts", "e2e_hermes_provenance.py"), "--hermes-src", hermes, "--plugin-dir", installed], cwd=tmp, env=dict(os.environ, PYTHONPATH=""))
        print(e.stdout.strip().splitlines()[-1] if e.stdout.strip() else e.stderr[-600:])
        return 0 if "E2E-PROVENANCE PASS" in e.stdout else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
