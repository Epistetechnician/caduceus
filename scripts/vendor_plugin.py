"""Copy the import closure of the plugin into plugin/caduceus_governance/_vendor/caduceus.

The Hermes catalog installs a plugin from a sparse checkout of its subdirectory only, so the plugin cannot import the repo-level `caduceus` package. This
script is the single source of the vendored copy; tests/test_vendor.py fails when the copy drifts. Pass --check to verify without writing.
"""
import ast
import os
import shutil
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SRC = os.path.join(ROOT, "caduceus")
DST = os.path.join(ROOT, "plugin", "caduceus_governance", "_vendor", "caduceus")
ENTRY = ("transition", "ledger", "control", "toolcall")


def imports(mod):
    out = set()
    for n in ast.walk(ast.parse(open(os.path.join(SRC, mod + ".py")).read())):
        if isinstance(n, ast.ImportFrom) and n.level == 1:
            out.update([n.module.split(".")[0]] if n.module else [a.name for a in n.names])
        elif isinstance(n, ast.ImportFrom) and n.module == "caduceus":
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and (n.module or "").startswith("caduceus."):
            out.add(n.module.split(".")[1])
    return {o for o in out if os.path.exists(os.path.join(SRC, o + ".py"))}


def closure():
    seen, stack = set(), list(ENTRY)
    while stack:
        m = stack.pop()
        if m not in seen:
            seen.add(m)
            stack += imports(m)
    return sorted(seen)


def want():
    files = {"__init__.py": open(os.path.join(SRC, "__init__.py"), "rb").read()}
    for m in closure():
        files[m + ".py"] = open(os.path.join(SRC, m + ".py"), "rb").read()
    return files


def main():
    files = want()
    if "--check" in sys.argv:
        have = {f: open(os.path.join(DST, f), "rb").read() for f in os.listdir(DST) if f.endswith(".py")} if os.path.isdir(DST) else {}
        sys.exit(0 if have == files else 1)
    shutil.rmtree(DST, ignore_errors=True)
    os.makedirs(DST)
    for f, data in files.items():
        with open(os.path.join(DST, f), "wb") as fh:
            fh.write(data)
    print("vendored", sorted(files))


if __name__ == "__main__":
    main()
