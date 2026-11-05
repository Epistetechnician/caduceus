"""Linux confinement for operator-registered checks and the broker (PRD R12): Landlock (filesystem + TCP) and a seccomp socket filter, in pure standard-library ctypes.

Run as a launcher: `python3 linux_confine.py --workdir DIR [--rw DIR ...] [--ro DIR ...] -- argv...` applies the restrictions to itself, then execs argv.
What the child can do afterwards:
  * read: the system roots (/usr /lib /lib64 /bin /sbin /etc /opt /proc /dev), the interpreter's prefix, and any --ro path; NOT the home directory or anything else;
  * write: --workdir, each --rw path, and /dev/null; nothing else (no create, remove, rename or truncate elsewhere);
  * network: no TCP bind/connect (Landlock ABI 4+), and socket() for any domain except AF_UNIX returns EPERM (seccomp), which also removes UDP and raw sockets.
Every failure to apply a restriction exits 71 with a `caduceus-confine:` line on stderr BEFORE exec, so the caller maps it to `Unavailable` and the change is held, never passed.
There is no way to ask for less confinement. Not covered: reads inside the allowed system roots, abstract UNIX sockets below ABI 6, and a kernel without Landlock (exit 71).
Checked on Linux 7.0 aarch64 (Landlock ABI 8) in a container (`tests/test_linux_confine.py`); x86_64 numbers are from the kernel ABI and untested here.
"""

from __future__ import annotations

import ctypes
import os
import platform
import struct
import sys
from typing import List

EX_UNAVAILABLE = 71
SYS_LANDLOCK_CREATE, SYS_LANDLOCK_ADD, SYS_LANDLOCK_RESTRICT = 444, 445, 446
PR_SET_NO_NEW_PRIVS, PR_SET_SECCOMP, SECCOMP_MODE_FILTER = 38, 22, 2
LANDLOCK_CREATE_RULESET_VERSION = 1
RULE_PATH_BENEATH = 1

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
FS_ABI1 = (1 << 13) - 1  # bits 0..12
REFER, TRUNCATE, IOCTL_DEV = 1 << 13, 1 << 14, 1 << 15
NET_BIND_TCP, NET_CONNECT_TCP = 1, 2
SCOPE_ABSTRACT_UNIX, SCOPE_SIGNAL = 1, 2
FILE_ONLY_ALLOWED = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE | IOCTL_DEV  # access bits valid on a non-directory

ARCH = {"x86_64": (0xC000003E, 41), "aarch64": (0xC00000B7, 198)}  # (AUDIT_ARCH, __NR_socket)
READ_ROOTS = ("/usr", "/lib", "/lib64", "/lib32", "/bin", "/sbin", "/etc", "/opt", "/proc", "/dev")


class ConfineError(Exception):
    pass


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def abi_version() -> int:
    """Landlock ABI version, or 0 if the kernel has no usable Landlock."""
    if os.environ.get("CADUCEUS_FORCE_NO_LANDLOCK") == "1":  # test hook: it can only make confinement fail, never weaken it
        return 0
    r = _libc().syscall(SYS_LANDLOCK_CREATE, None, 0, LANDLOCK_CREATE_RULESET_VERSION)
    return r if r > 0 else 0


def _fs_mask(abi: int) -> int:
    m = FS_ABI1
    if abi >= 2:
        m |= REFER
    if abi >= 3:
        m |= TRUNCATE
    if abi >= 5:
        m |= IOCTL_DEV
    return m


def _sys(libc, nr, *args) -> int:
    r = libc.syscall(nr, *args)
    if r < 0:
        raise ConfineError(os.strerror(ctypes.get_errno()))
    return r


def _add_path(libc, ruleset_fd: int, path: str, access: int, handled: int) -> None:
    if not os.path.exists(path):
        return
    is_dir = os.path.isdir(path)
    mask = access & handled
    if not is_dir:
        mask &= FILE_ONLY_ALLOWED
    if not mask:
        return
    fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        attr = struct.pack("=Qi", mask, fd)  # struct landlock_path_beneath_attr is packed: 12 bytes
        buf = ctypes.create_string_buffer(attr, len(attr))
        _sys(libc, SYS_LANDLOCK_ADD, ruleset_fd, RULE_PATH_BENEATH, ctypes.byref(buf), 0)
    finally:
        os.close(fd)


def _seccomp_no_inet() -> None:
    arch = ARCH.get(platform.machine())
    if arch is None:
        raise ConfineError("seccomp filter not implemented for this architecture")
    audit_arch, nr_socket = arch
    BPF_LD_W_ABS, BPF_JEQ_K, BPF_RET_K = 0x20, 0x15, 0x06
    prog = [
        (BPF_LD_W_ABS, 0, 0, 4),                   # arch
        (BPF_JEQ_K, 1, 0, audit_arch),             # right arch: skip the kill
        (BPF_RET_K, 0, 0, 0x80000000),             # SECCOMP_RET_KILL_PROCESS
        (BPF_LD_W_ABS, 0, 0, 0),                   # syscall number
        (BPF_JEQ_K, 0, 3, nr_socket),              # not socket(): allow
        (BPF_LD_W_ABS, 0, 0, 16),                  # socket() domain (args[0], low 32 bits)
        (BPF_JEQ_K, 1, 0, 1),                      # AF_UNIX: allow
        (BPF_RET_K, 0, 0, 0x00050000 | 1),         # SECCOMP_RET_ERRNO | EPERM
        (BPF_RET_K, 0, 0, 0x7FFF0000),             # SECCOMP_RET_ALLOW
    ]
    raw = b"".join(struct.pack("=HBBI", c, jt, jf, k) for c, jt, jf, k in prog)
    buf = ctypes.create_string_buffer(raw, len(raw))

    class Fprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    fp = Fprog(len(prog), ctypes.cast(buf, ctypes.c_void_p))
    libc = _libc()
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(fp), 0, 0) != 0:
        raise ConfineError("seccomp: " + os.strerror(ctypes.get_errno()))


def confine(workdir: str, rw: List[str] = (), ro: List[str] = ()) -> int:
    """Apply the restrictions to this process. Returns the Landlock ABI used. Raises ConfineError; the caller must not run untrusted code after an error."""
    abi = abi_version()
    if abi < 1:
        raise ConfineError("Landlock is not available on this kernel")
    libc = _libc()
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise ConfineError("no_new_privs: " + os.strerror(ctypes.get_errno()))
    fs = _fs_mask(abi)
    net = (NET_BIND_TCP | NET_CONNECT_TCP) if abi >= 4 else 0
    scoped = (SCOPE_ABSTRACT_UNIX | SCOPE_SIGNAL) if abi >= 6 else 0
    size = 8 if abi < 4 else (16 if abi < 6 else 24)
    attr = struct.pack("=QQQ", fs, net, scoped)[:size]
    buf = ctypes.create_string_buffer(attr, len(attr))
    rs = _sys(libc, SYS_LANDLOCK_CREATE, ctypes.byref(buf), size, 0)
    try:
        reads = list(READ_ROOTS) + [sys.base_prefix, sys.prefix, os.path.dirname(os.path.realpath(sys.executable))] + list(ro)
        for p in reads:
            _add_path(libc, rs, os.path.realpath(p), EXECUTE | READ_FILE | READ_DIR, fs)
        for p in [workdir] + list(rw):
            _add_path(libc, rs, os.path.realpath(p), fs, fs)
        _add_path(libc, rs, "/dev/null", WRITE_FILE | READ_FILE | TRUNCATE, fs)
        _sys(libc, SYS_LANDLOCK_RESTRICT, rs, 0)
    finally:
        os.close(rs)
    _seccomp_no_inet()
    return abi


def main(argv: List[str]) -> int:
    workdir, rw, ro, cmd, i = None, [], [], [], 0
    while i < len(argv):
        if argv[i] == "--workdir" and i + 1 < len(argv):
            workdir, i = argv[i + 1], i + 2
        elif argv[i] == "--rw" and i + 1 < len(argv):
            rw.append(argv[i + 1])
            i += 2
        elif argv[i] == "--ro" and i + 1 < len(argv):
            ro.append(argv[i + 1])
            i += 2
        elif argv[i] == "--":
            cmd = argv[i + 1:]
            break
        else:
            print("caduceus-confine: bad arguments", file=sys.stderr)
            return EX_UNAVAILABLE
    if not workdir or not cmd or not os.path.isdir(workdir):
        print("caduceus-confine: --workdir DIR and a command are required", file=sys.stderr)
        return EX_UNAVAILABLE
    try:
        confine(workdir, rw, ro)
    except (ConfineError, OSError) as exc:
        print("caduceus-confine: cannot confine: %s" % exc, file=sys.stderr)
        return EX_UNAVAILABLE
    try:
        os.execvp(cmd[0], cmd)
    except OSError as exc:
        print("caduceus-confine: exec failed: %s" % exc, file=sys.stderr)
        return 127
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
