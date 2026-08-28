"""flash — pyocd flash/mass-erase-recovery/reset sequence.

Extracted verbatim from ``cli._cmd_deploy`` so sprint 002's ``serve``
daemon can drive the exact same locked-part recovery path over the
network, instead of growing a second, divergent copy of it.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable

import intelhex

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


def _validate_hex(hex_path: str) -> str | None:
    """Parse ``hex_path`` with ``intelhex`` before any pyocd invocation.

    This is a pre-flight check, not a flash attempt: it never touches a
    board, only the file on disk. Returns ``None`` if ``hex_path`` parses
    as a valid Intel HEX file, or a short, human-readable message
    otherwise -- covering a missing/unreadable file (``OSError``, e.g.
    ``FileNotFoundError``/``PermissionError``) and a malformed one
    (``intelhex.IntelHexError`` and its subclasses, e.g. a bad record or
    checksum) without leaking either exception's raw traceback.
    """
    try:
        intelhex.IntelHex().loadhex(hex_path)
    except OSError as exc:
        return f"cannot read hex file {hex_path!r}: {exc}"
    except intelhex.IntelHexError as exc:
        return f"invalid hex file {hex_path!r}: {exc}"
    return None


# ---------------------------------------------------------------------------
# Failure-signature matching
#
# These are the ONE named, documented place pyocd's failure wording is
# matched against (per sprint 004's hazard about string-matching
# brittleness): narrow substrings drawn from the concrete field reports
# behind this sprint, not a general parser of pyocd's output. Anything
# that matches neither list is "not recoverable" -- flash_hex must never
# treat an unrecognized failure as a reason to mass-erase (see
# flash_hex's Design Rationale in sprint.md: an unrecognized failure that
# *was* actually a lock costs one manual `pyocd erase --mass`; treating
# an unrecognized failure as locked and erasing anyway costs a board's
# firmware -- the asymmetry is not close).
# ---------------------------------------------------------------------------

#: A flaky USB/probe/communication problem, not a property of the board's
#: flash contents or protection state -- worth exactly one blind retry,
#: since the same flash often succeeds outright the second time.
_TRANSIENT_SIGNATURES = (
    "timeout reading from probe",
    "probe timeout",
    "communication failure",
    "communication fault",
    "transfer fault",
    "transfer error",
    "dapaccess",
)


def _looks_transient(output: str) -> bool:
    """True if ``output`` (pyocd's captured stdout/stderr) names a
    transient probe/communication problem worth one blind retry."""
    lowered = output.lower()
    return any(sig in lowered for sig in _TRANSIENT_SIGNATURES)


#: A locked/protected nRF (APPROTECT set, or a protected SoftDevice
#: region at 0x0) that rejects every flash-algorithm erase -- only a
#: CTRL-AP mass erase (ERASEALL) clears it, per UC-009. This is the
#: *only* signature that justifies a mass erase; anything that matches
#: neither this list nor ``_TRANSIENT_SIGNATURES`` above is deliberately
#: treated as unrecoverable rather than assumed locked (see
#: sprint.md's Design Rationale) -- an unrecognized failure that was a
#: real lock costs one manual ``pyocd erase --mass``; treating an
#: unrecognized failure as locked and erasing anyway can cost a board's
#: firmware.
_LOCKED_SIGNATURES = (
    "0x67",  # observed CMSIS-DAP fault code for a locked-device sector-erase failure
    "flash erase sector failure",
    "approtect",
    "access port protection",
    "authentication failed",
    "not authenticated",
    "device is locked",
    "target is locked",
)


def _looks_locked(output: str) -> bool:
    """True if ``output`` (pyocd's captured stdout/stderr) names a
    locked/protected-device signature recoverable only by a CTRL-AP mass
    erase."""
    lowered = output.lower()
    return any(sig in lowered for sig in _LOCKED_SIGNATURES)


def _run_streamed(
    cmd: list[str], log: Callable[[str], None] | None
) -> tuple[int, str]:
    """Run ``cmd``, relaying its combined stdout/stderr through ``_log``
    line by line as it arrives, and return ``(exit_code, output_text)``.

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

    ``output_text`` accumulates the exact same lines already relayed to
    ``log`` (newline-joined), as a side buffer for signature matching
    (:func:`_looks_transient`/:func:`_looks_locked`) -- it does not
    change what is streamed or when, and it is not batching or deferring
    anything: every line still reaches ``log`` the instant it arrives.

    ``stderr=subprocess.STDOUT`` merges pyocd's stderr into the same
    stream, since pyocd's progress output is not reliably confined to
    one of the two and both matter equally to ``log``'s caller.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    assert proc.stdout is not None  # guaranteed by stdout=PIPE above
    lines: list[str] = []
    for line in proc.stdout:
        stripped = line.rstrip("\n")
        lines.append(stripped)
        _log(log, stripped)
    return proc.wait(), "\n".join(lines)


def flash_hex(
    uid: str,
    hex_path: str,
    target_mcu: str = DEFAULT_MCU,
    log: Callable[[str], None] | None = None,
    board_name: str | None = None,
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

    Before any of that: ``hex_path`` is validated with ``intelhex``
    (:func:`_validate_hex`). A missing, unreadable, or malformed hex file
    fails here, with a clear message routed through ``log``, before any
    ``pyocd`` subprocess is constructed or run -- so an operator-side file
    problem never reaches the board at all.

    A first flash failure whose output looks transient (a probe timeout,
    a communication/transfer fault, or a ``DAPAccess`` error --
    :func:`_looks_transient`) is retried exactly once, logged visibly,
    before anything else is decided -- most flaky-USB failures simply
    succeed the second time with no change to the board's state at all.

    A failure that persists past that (or that never looked transient in
    the first place) is mass-erased and retried only if its output looks
    *locked* (:func:`_looks_locked`) -- a `0x67` sector-erase failure, or
    auth/lock/APPROTECT wording. Any other failure (an invalid hex that
    slipped past validation, a bad ``target_mcu``, or anything else this
    module doesn't recognize) fails immediately **without** erasing:
    an unrecognized signature is deliberately never treated as "assume
    locked," because an unnecessary mass erase destroys a working
    board's firmware while a missed recovery only costs the operator one
    manual ``pyocd erase --mass``.

    If the mass erase itself succeeds but the retried flash still fails,
    the board has no firmware at all -- the erase already wiped it and
    reflashing didn't take. That is reported explicitly and unmissably
    through ``log`` (not only local stderr), naming the board via
    ``board_name`` (falling back to ``uid`` when not given), so a remote
    operator over ``deploy --remote`` sees it too, not just "flashing
    failed."
    """
    hex_error = _validate_hex(hex_path)
    if hex_error is not None:
        _log(log, f"Error: {hex_error}")
        return 1

    # --- flash (with mass-erase recovery for locked parts) ---
    flash_cmd = [
        *_PYOCD, "flash",
        "-t", target_mcu,
        "--uid", uid,
        hex_path,
    ]
    rc, output = _run_streamed(flash_cmd, log)
    if rc != 0 and _looks_transient(output):
        _log(
            log,
            "flash failed with a transient-looking probe/communication "
            "error — retrying once before any mass-erase decision.",
        )
        rc, output = _run_streamed(flash_cmd, log)

    if rc != 0 and _looks_locked(output):
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
        erase_rc, _erase_output = _run_streamed(erase_cmd, log)
        if erase_rc != 0:
            _log(log, f"Error: mass erase failed (exit {erase_rc}).")
            return erase_rc
        rc, output = _run_streamed(flash_cmd, log)
        if rc != 0:
            name = board_name or uid
            _log(
                log,
                f"Error: flash still failed after mass erase (exit {rc}) "
                f"— {name} WAS ERASED AND NOW HAS NO FIRMWARE. It will "
                "not run until it is successfully reflashed.",
            )
            return rc
    elif rc != 0:
        # No recognized signature -- neither transient (already retried
        # above) nor locked. Fail as-is, without ever mass-erasing: see
        # the "unrecognized = don't erase" rationale above.
        _log(
            log,
            f"Error: flash failed (exit {rc}) with no recognized "
            "recoverable signature -- not mass-erasing. If this device "
            "is actually locked/protected, run 'pyocd erase --mass' "
            "manually.",
        )
        return rc

    reset_cmd = [
        *_PYOCD, "reset",
        "-t", target_mcu,
        "--uid", uid,
    ]
    reset_rc, _reset_output = _run_streamed(reset_cmd, log)
    return reset_rc
