"""builder — firmware build orchestration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run(
    clean: bool = False,
    verbose: bool = False,
    jobs: int | None = None,
    build_cmd: str | None = None,
) -> int:
    """Shell out to the firmware build script and return its exit code.

    Parameters
    ----------
    clean:     Pass ``--clean`` to the build script.
    verbose:   Pass ``--verbose`` to the build script.
    jobs:      Pass ``-j N`` to the build script.
    build_cmd: Override the entire build command (split on whitespace).
               When *not* given the default is ``python3 build.py`` in CWD.

    Returns the subprocess exit code (never raises on build failure).
    """
    if build_cmd:
        cmd: list[str] = build_cmd.split()
    else:
        if not Path("build.py").exists():
            print(
                "Error: build.py not found in CWD. Use --build-cmd to override.",
                file=sys.stderr,
            )
            return 1
        cmd = [sys.executable, "build.py"]

    if clean:
        cmd.append("--clean")
    if verbose:
        cmd.append("--verbose")
    if jobs is not None:
        cmd += ["-j", str(jobs)]

    result = subprocess.run(cmd)
    return result.returncode
