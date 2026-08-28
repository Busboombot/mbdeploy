#!/usr/bin/env python3
"""Spike: does hidapi's exit-time crash (isolated on macOS in sprint 002
ticket 007) reproduce on Linux/aarch64 (Nolanet)?

Ticket:
  clasi/sprints/003-remote-client-and-nolanet-acceptance/tickets/
    001-hardware-risk-spike-verify-hidapi-exit-crash-risk-on-nolanet-linux.md

This is a throwaway verification tool, not shipped product code -- it is
NOT part of the `mbdeploy` package and has no `pyproject.toml` entry. It
is kept under `scripts/` and committed for reproducibility, same
convention as `scripts/spike_avahi_coexist.py` (sprint 002 ticket 001).

Sprint 002 ticket 007 isolated a pre-existing hidapi/IOKit thread-safety
bug on macOS: `mbdeploy serve` crashes at process exit with an
`NSInvalidArgumentException` inside hidapi's `hid_exit()`, reproducible
with `devices.flashable_probes()` alone run on a background thread and
the interpreter then exiting -- no mbdeploy server code in the path, and
only with real HID hardware attached. `NSInvalidArgumentException` is an
Objective-C/Cocoa construct and cannot appear verbatim on Linux, but
Linux's hidapi backend (libusb or hidraw, not IOKit) could still have its
own exit-time thread-safety issue -- unknown until tested.

Two independent checks, each run several times because this class of bug
is often intermittent:

1. `probe-thread` -- the minimal repro: `devices.flashable_probes()`
   called once on a background thread (mirroring `Supervisor.run`'s
   shape -- see server.py's `Supervisor.run`/`_tick`), then the main
   thread finishes and the interpreter exits normally. Needs real HID
   hardware attached to mean anything. Each trial is its own subprocess,
   since what's being measured is that subprocess's own exit code and
   stderr at interpreter teardown -- not anything observable from
   inside the same still-running process.

2. `serve-cycle` -- the real thing: launch `mbdeploy serve --no-flash`
   as a subprocess, give it a moment to come up and poll at least once,
   then send it SIGINT or SIGTERM and wait for it to exit.

Usage (on loki, inside ~/mbdeploy-test/.venv):

    # Minimal repro, 10 trials (10 subprocesses, one interpreter exit
    # each).
    .venv/bin/python scripts/spike_hidapi_exit.py probe-thread --trials 10

    # A single probe-thread trial -- this is what --trials shells out to
    # per-iteration; rarely invoked directly.
    .venv/bin/python scripts/spike_hidapi_exit.py probe-thread-once

    # The real thing: 5 trials each of SIGINT and SIGTERM against a real
    # `mbdeploy serve --no-flash`.
    .venv/bin/python scripts/spike_hidapi_exit.py serve-cycle --trials 5

    # Both checks back to back, with a combined summary at the end.
    .venv/bin/python scripts/spike_hidapi_exit.py run-all --trials 5
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def _mbdeploy_bin() -> str:
    """Path to the `mbdeploy` console script installed in this venv --
    the same entrypoint `systemctl start mbdeploy` uses in production,
    not `python -m` invoked some other way."""
    candidate = Path(sys.executable).parent / "mbdeploy"
    return str(candidate) if candidate.exists() else "mbdeploy"


# ---------------------------------------------------------------------------
# Check 1: devices.flashable_probes() on a background thread, then exit.
# ---------------------------------------------------------------------------

def cmd_probe_thread_once(args: argparse.Namespace) -> int:
    """Single trial: run `flashable_probes()` on a background thread
    (mirroring `Supervisor.run`'s shape), join it, then fall off the end
    of `main()` into normal interpreter shutdown -- the same teardown
    path where the macOS bug's `hid_exit()` crash occurred."""
    from mbdeploy import devices

    outcome: dict = {}

    def worker() -> None:
        try:
            outcome["probes"] = devices.flashable_probes()
        except Exception as exc:  # noqa: BLE001 - spike, want to see everything
            outcome["error"] = repr(exc)

    t = threading.Thread(target=worker, name="probe-thread")
    t.start()
    t.join(timeout=args.timeout)
    if t.is_alive():
        print(
            f"[probe-thread-once] FAIL: worker thread did not finish "
            f"within {args.timeout}s",
            file=sys.stderr,
        )
        return 1
    if "error" in outcome:
        print(
            f"[probe-thread-once] flashable_probes() raised: {outcome['error']}",
            file=sys.stderr,
        )
        return 1
    print(f"[probe-thread-once] flashable_probes() -> {outcome.get('probes')}")
    # Falls through to normal `sys.exit()`/interpreter teardown below.
    return 0


def cmd_probe_thread(args: argparse.Namespace) -> int:
    """Driver: run `probe-thread-once` as its own subprocess, `--trials`
    times, so each trial's own process exit code/stderr is captured
    independently."""
    results = []
    for i in range(1, args.trials + 1):
        print(f"=== probe-thread trial {i}/{args.trials} ===", flush=True)
        proc = subprocess.run(
            [sys.executable, __file__, "probe-thread-once", "--timeout", str(args.timeout)],
            capture_output=True,
            text=True,
            timeout=args.timeout + 10,
        )
        results.append(proc)
        print(f"  exit_code={proc.returncode}")
        if proc.stdout.strip():
            print(f"  stdout: {proc.stdout.strip()}")
        if proc.stderr.strip():
            print(f"  stderr:\n{proc.stderr}")
    clean = [p for p in results if p.returncode == 0 and not p.stderr.strip()]
    print(
        f"\n[probe-thread] summary: {len(clean)}/{len(results)} trials clean "
        f"(exit 0, no stderr)"
    )
    return 0 if len(clean) == len(results) else 1


# ---------------------------------------------------------------------------
# Check 2: mbdeploy serve --no-flash, then SIGINT/SIGTERM.
# ---------------------------------------------------------------------------

def _run_one_serve_cycle(
    sig_name: str, warmup: float, poll_interval: float, timeout: float
) -> dict:
    cmd = [
        _mbdeploy_bin(),
        "serve",
        "--no-flash",
        "--poll-interval",
        str(poll_interval),
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    time.sleep(warmup)

    early = proc.poll()
    if early is not None:
        out, err = proc.communicate()
        return {
            "signal": sig_name,
            "exit_code": early,
            "stdout": out,
            "stderr": err,
            "note": "serve exited on its own before the signal was sent",
            "timed_out": False,
        }

    sig = getattr(signal, sig_name)
    proc.send_signal(sig)
    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        timed_out = True

    return {
        "signal": sig_name,
        "exit_code": proc.returncode,
        "stdout": out,
        "stderr": err,
        "note": None,
        "timed_out": timed_out,
    }


def cmd_serve_cycle(args: argparse.Namespace) -> int:
    results = []
    for i in range(1, args.trials + 1):
        for sig_name in ("SIGINT", "SIGTERM"):
            print(f"=== serve-cycle trial {i}/{args.trials} signal={sig_name} ===", flush=True)
            r = _run_one_serve_cycle(sig_name, args.warmup, args.poll_interval, args.timeout)
            results.append(r)
            print(f"  exit_code={r['exit_code']} timed_out={r['timed_out']}")
            if r["note"]:
                print(f"  note: {r['note']}")
            if r["stdout"].strip():
                print(f"  stdout:\n{r['stdout']}")
            if r["stderr"].strip():
                print(f"  stderr:\n{r['stderr']}")
    clean = [
        r for r in results
        if r["exit_code"] == 0 and not r["timed_out"] and not r["stderr"].strip()
    ]
    print(
        f"\n[serve-cycle] summary: {len(clean)}/{len(results)} runs clean "
        f"(exit 0, no timeout, no stderr)"
    )
    return 0 if len(clean) == len(results) else 1


# ---------------------------------------------------------------------------
# Combined driver
# ---------------------------------------------------------------------------

def cmd_run_all(args: argparse.Namespace) -> int:
    print("##### Check 1: devices.flashable_probes() on a background thread #####")
    rc1 = cmd_probe_thread(args)
    print("\n##### Check 2: mbdeploy serve --no-flash, SIGINT/SIGTERM #####")
    rc2 = cmd_serve_cycle(args)
    overall = "PASS" if rc1 == 0 and rc2 == 0 else "FAIL"
    print(f"\n[run-all] overall: {overall}")
    return 0 if overall == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_once = sub.add_parser(
        "probe-thread-once", help="single-trial minimal repro (one process exit)"
    )
    p_once.add_argument("--timeout", type=float, default=15.0)
    p_once.set_defaults(func=cmd_probe_thread_once)

    p_probe = sub.add_parser(
        "probe-thread", help="run probe-thread-once N times, one subprocess each"
    )
    p_probe.add_argument("--trials", type=int, default=10)
    p_probe.add_argument("--timeout", type=float, default=15.0)
    p_probe.set_defaults(func=cmd_probe_thread)

    p_serve = sub.add_parser(
        "serve-cycle", help="run mbdeploy serve --no-flash + SIGINT/SIGTERM N times"
    )
    p_serve.add_argument("--trials", type=int, default=5)
    p_serve.add_argument("--warmup", type=float, default=3.0, help="seconds to let serve start + poll once before signaling")
    p_serve.add_argument("--poll-interval", type=float, default=2.0, dest="poll_interval")
    p_serve.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for exit after the signal")
    p_serve.set_defaults(func=cmd_serve_cycle)

    p_all = sub.add_parser("run-all", help="both checks back to back")
    p_all.add_argument("--trials", type=int, default=5)
    p_all.add_argument("--timeout", type=float, default=15.0)
    p_all.add_argument("--warmup", type=float, default=3.0)
    p_all.add_argument("--poll-interval", type=float, default=2.0, dest="poll_interval")
    p_all.set_defaults(func=cmd_run_all)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
