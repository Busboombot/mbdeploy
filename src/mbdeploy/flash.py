"""flash — pyocd flash/mass-erase-recovery/reset sequence.

Extracted verbatim from ``cli._cmd_deploy`` so sprint 002's ``serve``
daemon can drive the exact same locked-part recovery path over the
network, instead of growing a second, divergent copy of it.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable

from mbdeploy.devices import DEFAULT_MCU

# Invoke pyocd through the running interpreter rather than as a bare PATH
# lookup. mbdeploy is typically installed via pipx into an isolated venv, so
# pyocd (a declared dependency) is importable here but its console script is
# not on PATH. Mirrors the pattern already used in devices.py / cli.py.
_PYOCD = [sys.executable, "-m", "pyocd"]


def _log(log: Callable[[str], None] | None, message: str) -> None:
    """Route a status/error line to ``log`` if given, else to stderr.

    ``log=None`` must never go silent — callers (today, ``_cmd_deploy``)
    rely on these lines landing on stderr exactly as before this function
    existed.
    """
    if log is None:
        print(message, file=sys.stderr)
    else:
        log(message)


def _run_streamed(cmd: list[str], log: Callable[[str], None] | None) -> int:
    """Run ``cmd``, relaying its combined stdout/stderr through ``_log``
    line by line as it arrives, and return its exit code.

    Uses ``subprocess.Popen`` rather than a single blocking
    ``subprocess.run()`` specifically so pyocd's own progress output
    (erase/program/verify lines -- previously visible only in the
    daemon's inherited stdout / ``journalctl``) reaches the
    caller-supplied ``log`` callback throughout the run, not only once
    at exit. This is what lets ``server.py::serve_flash`` (whose ``log``
    forwards every call to the network client as a ``LOG`` line) emit a
    steady stream of progress for the whole duration of a real flash,
    keeping ``remote.py``'s client-side read timeout meaningful instead
    of expiring during a multi-second silent gap. See ticket 010 --
    ``flash_hex``'s three fixed status messages alone left a real
    ~450 KB flash silent from ``log``'s point of view for long enough to
    trip that timeout even though the flash itself succeeded.

    ``stderr=subprocess.STDOUT`` merges pyocd's stderr into the same
    stream, since pyocd's progress output is not reliably confined to
    one of the two and both matter equally to ``log``'s caller.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    assert proc.stdout is not None  # guaranteed by stdout=PIPE above
    for line in proc.stdout:
        _log(log, line.rstrip("\n"))
    return proc.wait()


def flash_hex(
    uid: str,
    hex_path: str,
    target_mcu: str = DEFAULT_MCU,
    log: Callable[[str], None] | None = None,
) -> int:
    """Flash ``hex_path`` to the board behind ``uid``, with mass-erase recovery.

    Mirrors the pyocd argv, messages, and return codes that used to live
    inline in ``cli._cmd_deploy``: a failed first flash triggers a CTRL-AP
    mass erase (to clear a locked/protected nRF) and one retry; a mass-erase
    failure returns its own return code without retrying; a still-failing
    flash after mass erase returns its return code; success returns the
    ``reset`` return code.

    Each pyocd subprocess's output is streamed through ``log`` as it
    arrives (see :func:`_run_streamed`) rather than captured and
    discarded, so a caller-supplied ``log`` sees progress throughout
    each invocation, not just at the three fixed transition messages
    below.
    """
    # --- flash (with mass-erase recovery for locked parts) ---
    flash_cmd = [
        *_PYOCD, "flash",
        "-t", target_mcu,
        "--uid", uid,
        hex_path,
    ]
    rc = _run_streamed(flash_cmd, log)
    if rc != 0:
        # A locked/protected nRF (APPROTECT set, or a protected SoftDevice
        # region at 0x0) rejects every flash-algorithm erase, so the flash
        # fails before it can program. Neither sector nor chip erase clears
        # that — only a CTRL-AP mass erase (ERASEALL), which also resets
        # APPROTECT. Recover by mass-erasing, then retry the flash once.
        _log(
            log,
            "flash failed — attempting CTRL-AP mass erase to recover a "
            "locked device, then retrying.",
        )
        erase_cmd = [
            *_PYOCD, "erase",
            "-t", target_mcu,
            "--uid", uid,
            "--mass",
        ]
        erase_rc = _run_streamed(erase_cmd, log)
        if erase_rc != 0:
            _log(log, f"Error: mass erase failed (exit {erase_rc}).")
            return erase_rc
        rc = _run_streamed(flash_cmd, log)
        if rc != 0:
            _log(
                log,
                f"Error: flash still failed after mass erase (exit {rc}).",
            )
            return rc

    reset_cmd = [
        *_PYOCD, "reset",
        "-t", target_mcu,
        "--uid", uid,
    ]
    return _run_streamed(reset_cmd, log)
