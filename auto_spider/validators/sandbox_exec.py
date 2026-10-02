"""Linux Landlock launcher: generated code reads its checkout and writes only scratch.

No shell is used. The sandbox is inherited by all children, including subprocesses
started by generated tests. Unsupported kernels fail closed.
"""

import ctypes
import os
import sys
from pathlib import Path


def restrict(workspace: Path, scratch: Path) -> None:
    if sys.platform != "linux":
        raise RuntimeError("VALIDATION_SANDBOX_UNAVAILABLE: Linux Landlock required")
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 1:
        raise RuntimeError("VALIDATION_SANDBOX_UNAVAILABLE: kernel does not support Landlock")
    handled = (1 << 13) - 1
    if abi >= 2:
        handled |= 1 << 13
    if abi >= 3:
        handled |= 1 << 14
    attr = ctypes.c_uint64(handled)
    ruleset = libc.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    def allow(path: Path, access: int):
        if not path.exists():
            return
        fd = os.open(str(path.resolve()), os.O_PATH | os.O_CLOEXEC)
        try:
            if path.is_file() or not path.is_dir():
                access &= (1 << 0) | (1 << 1) | (1 << 2) | (1 << 14)
            rule = PathRule(access, fd)
            if libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0) != 0:
                raise OSError(ctypes.get_errno(), f"landlock_add_rule: {path}")
        finally:
            os.close(fd)

    try:
        read = (1 << 0) | (1 << 2) | (1 << 3)
        for path in (
            workspace,
            Path(sys.prefix),
            Path("/usr"),
            Path("/lib"),
            Path("/lib64"),
            Path("/bin"),
            Path("/etc/ssl"),
            Path("/etc/resolv.conf"),
            Path("/etc/hosts"),
            Path("/etc/nsswitch.conf"),
            Path("/etc/localtime"),
            Path("/etc/ld.so.cache"),
            Path("/dev/urandom"),
            Path("/dev/random"),
            Path("/proc/self/stat"),
        ):
            allow(path, read)
        allow(Path("/dev/null"), read | (1 << 1))
        allow(scratch, handled)
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
        if libc.syscall(446, ruleset, 0) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    finally:
        os.close(ruleset)


if __name__ == "__main__":
    try:
        restrict(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
        os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    except (OSError, RuntimeError) as exc:
        print(f"VALIDATION_SANDBOX_UNAVAILABLE: {exc}", file=sys.stderr)
        sys.exit(78)
