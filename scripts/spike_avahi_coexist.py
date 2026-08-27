#!/usr/bin/env python3
"""Spike: does python-zeroconf coexist with a running avahi-daemon?

Ticket:
  clasi/sprints/002-fleet-daemon/tickets/
    001-avahi-coexistence-spike-verify-python-zeroconf-alongside-avahi-daemon-on-nolanet.md

This is a throwaway verification tool, not shipped product code -- it is
NOT part of the `mbdeploy` package and has no `pyproject.toml` entry. It
is kept under `scripts/` and committed for reproducibility, since sprint
003's acceptance may want to re-run it.

It registers a dummy `_mbspike._tcp` service via `zeroconf.ServiceInfo`,
confirms it can be discovered (including a TXT-record round trip through
bytes), and confirms unregistering makes it disappear again -- all while
avahi-daemon keeps running unmodified on port 5353.

Usage (on a Nolanet node, inside a venv with `zeroconf` installed):

    # Full local round trip in one process: register, browse for the
    # instance this same process just registered, verify TXT records,
    # unregister, and confirm the advertisement disappears again.
    python3 scripts/spike_avahi_coexist.py selftest

    # Register only, and hold the advertisement up for --hold seconds so
    # a second machine on the LAN can discover it externally, e.g.:
    #   dns-sd -B _mbspike._tcp                  (from a Mac)
    #   dns-sd -L <instance-name> _mbspike._tcp  (from a Mac)
    #   avahi-browse -rt _mbspike._tcp           (on the node itself)
    python3 scripts/spike_avahi_coexist.py register --hold 60

    # Browse only, for --duration seconds, printing whatever instances
    # (if any) are found, including their TXT records.
    python3 scripts/spike_avahi_coexist.py browse --duration 10
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

SERVICE_TYPE = "_mbspike._tcp.local."
DEFAULT_PORT = 17235

# TXT record fixture. One plain-ASCII value and one with a non-ASCII
# character encoded as UTF-8 bytes, to exercise the str/bytes handling
# python-zeroconf does on the wire (TXT records are raw bytes).
EXPECTED_TXT: dict[str, bytes] = {
    "uid": b"spike-uid-0001",
    "role": b"spike",
    "msg": "hello-mbdeploy-☃".encode("utf-8"),
}


def _instance_name() -> str:
    return f"spike-{socket.gethostname()}.{SERVICE_TYPE}"


def _local_ip() -> str:
    """Best-effort LAN IPv4 address (not 127.0.0.1) for the ServiceInfo."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def make_service_info(port: int = DEFAULT_PORT) -> ServiceInfo:
    return ServiceInfo(
        SERVICE_TYPE,
        _instance_name(),
        addresses=[socket.inet_aton(_local_ip())],
        port=port,
        properties=dict(EXPECTED_TXT),
        server=f"{socket.gethostname()}.local.",
    )


class _CollectingListener(ServiceListener):
    """Records add/update/remove events keyed by instance name."""

    def __init__(self) -> None:
        self.found: dict[str, ServiceInfo] = {}
        self.removed: set[str] = set()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name, timeout=3000)
        if info is not None:
            self.found[name] = info
            self.removed.discard(name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.found.pop(name, None)
        self.removed.add(name)


def _report_info(name: str, info: ServiceInfo) -> bool:
    """Print a discovered instance's details; return True if TXT matches."""
    print(f"[found] {name}")
    print(f"        addresses: {info.parsed_addresses()}")
    print(f"        port: {info.port}")
    print(f"        server: {info.server}")
    ok = True
    for key, expected in EXPECTED_TXT.items():
        actual = info.properties.get(key.encode("utf-8"))
        status = "OK" if actual == expected else "MISMATCH"
        if actual != expected:
            ok = False
        print(f"        txt[{key}] = {actual!r} expected {expected!r} [{status}]")
    return ok


def cmd_register(args: argparse.Namespace) -> int:
    zc = Zeroconf()
    info = make_service_info(args.port)
    print(f"[register] registering {info.name} on port {info.port} ...")
    try:
        zc.register_service(info)
    except Exception as exc:  # noqa: BLE001 - spike, want to see everything
        print(f"[register] FAIL: register_service raised {exc!r}")
        zc.close()
        return 1
    print("[register] registered OK -- no bind error, no exception.")
    print(
        f"[register] holding for {args.hold}s -- browse from another "
        "machine now, e.g.:\n"
        "    dns-sd -B _mbspike._tcp\n"
        "    avahi-browse -rt _mbspike._tcp"
    )
    try:
        time.sleep(args.hold)
    except KeyboardInterrupt:
        pass
    finally:
        print("[register] unregistering...")
        zc.unregister_service(info)
        zc.close()
        print("[register] unregistered and closed.")
    return 0


def cmd_browse(args: argparse.Namespace) -> int:
    zc = Zeroconf()
    listener = _CollectingListener()
    ServiceBrowser(zc, SERVICE_TYPE, listener)
    print(f"[browse] browsing {SERVICE_TYPE} for {args.duration}s ...")
    time.sleep(args.duration)
    if not listener.found:
        print("[browse] FAIL: no instances found.")
        zc.close()
        return 1
    all_ok = True
    for name, info in listener.found.items():
        if not _report_info(name, info):
            all_ok = False
    zc.close()
    return 0 if all_ok else 1


def cmd_selftest(args: argparse.Namespace) -> int:
    results: dict[str, bool] = {}

    zc = Zeroconf()
    info = make_service_info(args.port)
    print(f"[selftest] registering {info.name} on port {info.port} ...")
    try:
        zc.register_service(info)
    except Exception as exc:  # noqa: BLE001
        print(f"[selftest] FAIL: register_service raised {exc!r}")
        zc.close()
        return 1
    print("[selftest] registered OK -- no bind error, no exception.")
    results["register"] = True

    listener = _CollectingListener()
    ServiceBrowser(zc, SERVICE_TYPE, listener)
    deadline = time.time() + args.duration
    while time.time() < deadline and info.name not in listener.found:
        time.sleep(0.2)

    txt_ok = False
    if info.name in listener.found:
        print("[selftest] browse discovered the registered instance.")
        txt_ok = _report_info(info.name, listener.found[info.name])
    else:
        print(
            "[selftest] FAIL: browse did not discover the registered "
            f"instance within {args.duration}s."
        )
    results["browse"] = info.name in listener.found
    results["txt_roundtrip"] = txt_ok

    print("[selftest] unregistering...")
    zc.unregister_service(info)

    deadline = time.time() + args.duration
    while time.time() < deadline and info.name not in listener.removed:
        time.sleep(0.2)
    if info.name in listener.removed:
        print("[selftest] unregister confirmed: advertisement disappeared.")
    else:
        print(
            "[selftest] FAIL: no remove_service callback within "
            f"{args.duration}s after unregister."
        )
    results["unregister"] = info.name in listener.removed

    zc.close()
    print("[selftest] closed.")

    print("\n[selftest] summary:")
    all_ok = True
    for name, ok in results.items():
        print(f"  {name:15s} {'PASS' if ok else 'FAIL'}")
        all_ok = all_ok and ok
    print(f"\n[selftest] overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_selftest = sub.add_parser(
        "selftest", help="register, self-browse, verify TXT, unregister"
    )
    p_selftest.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_selftest.add_argument("--duration", type=float, default=10.0)
    p_selftest.set_defaults(func=cmd_selftest)

    p_register = sub.add_parser(
        "register", help="register only, hold for external browsing"
    )
    p_register.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_register.add_argument("--hold", type=float, default=60.0)
    p_register.set_defaults(func=cmd_register)

    p_browse = sub.add_parser("browse", help="browse only, for a fixed duration")
    p_browse.add_argument("--duration", type=float, default=10.0)
    p_browse.set_defaults(func=cmd_browse)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
