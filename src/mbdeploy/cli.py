"""mbdeploy CLI — entry point and subcommand definitions."""

from __future__ import annotations

import argparse
import subprocess
import sys
from importlib import resources
from pathlib import Path

from mbdeploy import __version__

# Invoke pyocd through the running interpreter rather than as a bare PATH
# lookup. mbdeploy is typically installed via pipx into an isolated venv, so
# pyocd (a declared dependency) is importable here but its console script is
# not on PATH. This mirrors the pattern already used in devices.py.
_PYOCD = [sys.executable, "-m", "pyocd"]


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = Path("config") / "devices.json"
_DEFAULT_HEX = "MICROBIT.hex"
_DEFAULT_MCU = "nrf52833"

# Mirrors devices.BAUD_RATE, kept here so building the parser doesn't have to
# import the device layer (and pyserial) on every invocation.
_DEFAULT_BAUD = 115200
_DEFAULT_CONNECT_TIMEOUT = 2.0

_AGENT_MANUAL = "agent_manual.md"


# ---------------------------------------------------------------------------
# Agent manual
# ---------------------------------------------------------------------------

def _read_agent_manual() -> str:
    """Return the bundled agent manual markdown shipped inside the package."""
    return resources.files("mbdeploy").joinpath(_AGENT_MANUAL).read_text(
        encoding="utf-8"
    )


class _AgentManualAction(argparse.Action):
    """Print the full agent manual and exit, before any subcommand is required."""

    def __init__(self, option_strings, dest, **kwargs):
        kwargs.setdefault("nargs", 0)
        kwargs.setdefault("default", argparse.SUPPRESS)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        manual = _read_agent_manual()
        sys.stdout.write(manual if manual.endswith("\n") else manual + "\n")
        parser.exit()


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_build(args: argparse.Namespace) -> int:
    from mbdeploy import builder

    return builder.run(
        clean=args.clean,
        verbose=args.verbose,
        jobs=args.jobs,
        build_cmd=args.build_cmd,
    )


_ROW_FMT = (
    "{enum:<5} {conn:<5} {name:<12} {common:<12} {role:<13} {port:<24} {uid}"
)
_TABLE_HEADER = _ROW_FMT.format(
    enum="ENUM", conn="CONN", name="DEVICE NAME", common="COMMON NAME",
    role="ROLE", port="PORT", uid="UID",
)


def _print_device_table(rows: list[dict]) -> None:
    """Print the shared `list` / `probe` table, connected devices first."""
    print(_TABLE_HEADER)
    print("-" * len(_TABLE_HEADER))
    for row in rows:
        print(_ROW_FMT.format(**row))


def _device_rows(
    entries: dict[str, dict],
    live_uids: set[str],
    live_ports: dict[str, str],
    read_names: bool,
    target_mcu: str,
) -> list[dict]:
    """Merge registry entries with the live probe list into display rows.

    Every known board appears — connected or not — and a connected board with
    no recorded name has its five-letter micro:bit name read over SWD, so a
    board that has never been probed still shows up identifiably.
    """
    import mbdeploy.devices as devices_mod

    uids = list(entries) + [uid for uid in live_uids if uid not in entries]
    rows = []
    for uid in uids:
        entry = entries.get(uid, {})
        connected = uid in live_uids
        name = entry.get("device_name") or entry.get("board_name") or ""
        if not name and connected and read_names:
            name = devices_mod.read_board_name(uid, target_mcu) or ""
        rows.append({
            "enum": str(entry.get("enum", "")),
            "conn": "yes" if connected else "no",
            "name": name,
            "common": entry.get("common_name") or "",
            "role": entry.get("role") or "",
            # A remembered port is meaningless once the board is unplugged.
            "port": (live_ports.get(uid) or entry.get("port") or "") if connected else "",
            "uid": uid,
        })
    # Connected first, then by enum (unregistered boards last), then by UID.
    rows.sort(key=lambda r: (
        r["conn"] == "no",
        int(r["enum"]) if r["enum"] else 1 << 30,
        r["uid"],
    ))
    return rows


def _cmd_list(args: argparse.Namespace) -> int:
    import mbdeploy.devices as devices_mod

    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG

    probes = devices_mod.flashable_probes()
    live_uids = {p["uid"] for p in probes}
    ports = devices_mod.port_serial_map(live_uids) if probes else {}
    registry = devices_mod.load_devices(config_path)

    rows = _device_rows(
        registry, live_uids, ports,
        read_names=not getattr(args, "fast", False),
        target_mcu=getattr(args, "target_mcu", None) or _DEFAULT_MCU,
    )
    if not rows:
        print("no devices found")
        return 0

    _print_device_table(rows)
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    import mbdeploy.devices as devices_mod

    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG

    target_mcu = getattr(args, "target_mcu", None) or _DEFAULT_MCU
    entries = devices_mod.probe_all(
        config_path,
        clear=getattr(args, "clear", False),
        target_mcu=target_mcu,
    )

    if not entries:
        print("no devices found")
        return 0

    registry = {entry["uid"]: entry for entry in entries if entry.get("uid")}
    rows = _device_rows(
        registry,
        devices_mod.connected_uids(),
        {},                       # probe_all already refreshed each entry's port
        read_names=False,         # probe_all already read every missing name
        target_mcu=target_mcu,
    )
    _print_device_table(rows)
    return 0


def _device_label(entry: dict) -> str:
    """Name a device the way the user would type it as a target.

    Never ``common_name``: that is a human label for a board ("Jane's robot"),
    not something :func:`resolve_target` matches, so quoting it back in an
    error would name the device by the one word that cannot address it.
    """
    name = entry.get("device_name") or entry.get("board_name")
    if name:
        return name
    enum = entry.get("enum")
    return str(enum) if enum is not None else entry.get("uid", "unknown")


def _deploy_entry(target: str, registry: dict[str, dict]) -> dict:
    """Return the registry entry for the board ``deploy`` should flash.

    An enum, UID, or name is a stable identifier, so it is resolved straight
    through the registry.

    A ``/dev/...`` path is not, and is deliberately **not** looked up in the
    registry: a recorded ``port`` is only as fresh as the last ``probe``, and
    macOS re-issues ``/dev/cu.usbmodem*`` names on every reconnect.  Matching
    one would return the board that *used to* sit on that path, and ``deploy``
    would then flash that board's UID — writing firmware to a different,
    currently-connected board than the path names.  ``connect`` sidesteps this
    by opening the path verbatim (see :func:`_connect_port`); ``deploy`` cannot,
    because pyOCD addresses a board by UID, so the path is translated through
    the *live* ``ioreg`` mapping instead: whichever board is on that port right
    now is the one that gets flashed.

    That live UID must still be present in the registry.  The entry is where
    ``role`` comes from, and ``role`` is what the relay guard reads, so
    flashing an unregistered UID would mean flashing with no relay guard at
    all.  Better to send the user to ``probe`` once.

    Raises ``ValueError`` if the target cannot be resolved.
    """
    import mbdeploy.devices as devices_mod

    if not (target.startswith("/dev/") or "/" in target):
        return devices_mod.resolve_target(target, registry)

    # Restrict the ioreg scan to connected CMSIS-DAP probes so some other
    # USB serial device can never be mistaken for a micro:bit.
    known = {p["uid"] for p in devices_mod.flashable_probes()}
    live_ports = devices_mod.port_serial_map(known)

    if not live_ports:
        # No live mapping, so there is no safe answer.  Falling back to the
        # registry's recorded port is exactly the wrong-board bug this branch
        # exists to prevent, so refuse and name the cause instead.
        if not known:
            raise ValueError(
                f"cannot resolve port '{target}': no micro:bit is connected."
            )
        raise ValueError(
            f"cannot resolve port '{target}': no live port mapping is "
            "available (it is read from macOS 'ioreg'). Refusing to fall back "
            "to the registry's recorded port, which may name a different "
            "board. Target by enum, name, or UID instead."
        )

    uid_by_port = {port: uid for uid, port in live_ports.items()}
    uid = uid_by_port.get(target)
    if uid is None:
        connected = ", ".join(sorted(uid_by_port)) or "none"
        raise ValueError(
            f"no micro:bit is on port '{target}' right now "
            f"(connected micro:bit ports: {connected})."
        )

    entry = registry.get(uid)
    if entry is None:
        raise ValueError(
            f"port '{target}' is device {uid}, which is not in the registry. "
            "Run 'mbdeploy probe' first — deploy needs the registry entry to "
            "know whether the board is a relay."
        )
    return entry


def _cmd_deploy(args: argparse.Namespace) -> int:
    import mbdeploy.devices as devices_mod

    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    hex_path = args.hex if args.hex else _DEFAULT_HEX
    target_mcu = args.target_mcu if args.target_mcu else _DEFAULT_MCU
    force_relay = args.force_relay

    # --- resolve device entry ---
    registry = devices_mod.load_devices(config_path)

    if args.target:
        try:
            entry = _deploy_entry(args.target, registry)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        # Auto-pick: unique non-relay device
        non_relay = [
            e for e in registry.values() if not devices_mod.is_relay(e.get("role"))
        ]
        if len(non_relay) == 0:
            print(
                "Error: no non-relay devices in registry. Run 'mbdeploy probe' first.",
                file=sys.stderr,
            )
            return 1
        if len(non_relay) > 1:
            names = [_device_label(e) for e in non_relay]
            print(
                f"Error: ambiguous — multiple non-relay devices: {names}. "
                "Specify a target.",
                file=sys.stderr,
            )
            return 1
        entry = non_relay[0]

    # --- relay guard ---
    if devices_mod.is_relay(entry.get("role")) and not force_relay:
        label = _device_label(entry)
        print(
            f"Error: {label} is a relay. Use --force-relay to override.",
            file=sys.stderr,
        )
        return 1

    # --- live-probe confirmation ---
    uid = entry["uid"]
    live_uids = {p["uid"] for p in devices_mod.flashable_probes()}
    if uid not in live_uids:
        print(f"Error: device not connected: {uid}", file=sys.stderr)
        return 1

    # --- optional build step ---
    if args.build or args.clean:
        from mbdeploy import builder

        rc = builder.run(
            clean=args.clean,
            verbose=getattr(args, "verbose", False),
            jobs=args.jobs,
        )
        if rc != 0:
            print(f"Error: build failed (exit {rc}).", file=sys.stderr)
            return rc

    # --- flash (with mass-erase recovery for locked parts) ---
    flash_cmd = [
        *_PYOCD, "flash",
        "-t", target_mcu,
        "--uid", uid,
        hex_path,
    ]
    rc = subprocess.run(flash_cmd).returncode
    if rc != 0:
        # A locked/protected nRF (APPROTECT set, or a protected SoftDevice
        # region at 0x0) rejects every flash-algorithm erase, so the flash
        # fails before it can program. Neither sector nor chip erase clears
        # that — only a CTRL-AP mass erase (ERASEALL), which also resets
        # APPROTECT. Recover by mass-erasing, then retry the flash once.
        print(
            "flash failed — attempting CTRL-AP mass erase to recover a "
            "locked device, then retrying.",
            file=sys.stderr,
        )
        erase_cmd = [
            *_PYOCD, "erase",
            "-t", target_mcu,
            "--uid", uid,
            "--mass",
        ]
        erase_rc = subprocess.run(erase_cmd).returncode
        if erase_rc != 0:
            print(f"Error: mass erase failed (exit {erase_rc}).", file=sys.stderr)
            return erase_rc
        rc = subprocess.run(flash_cmd).returncode
        if rc != 0:
            print(
                f"Error: flash still failed after mass erase (exit {rc}).",
                file=sys.stderr,
            )
            return rc

    reset_cmd = [
        *_PYOCD, "reset",
        "-t", target_mcu,
        "--uid", uid,
    ]
    return subprocess.run(reset_cmd).returncode


def _connect_port(target: str, registry: dict[str, dict]) -> str:
    """Return the serial port to open for ``target``.

    An explicit ``/dev/...`` path is opened verbatim: it is the most concrete
    address there is, and it also lets a board be talked to before it has ever
    been probed.  Deliberately *not* looked up in the registry first — a
    recorded port is only as fresh as the last ``probe``, so matching one and
    then re-resolving that board's current port would quietly open a different
    board than the path names.

    Every other token (enum, name, UID) is resolved through the registry, and
    the port is then re-read live for the same staleness reason.
    """
    import mbdeploy.devices as devices_mod

    if target.startswith("/dev/") or "/" in target:
        return target

    entry = devices_mod.resolve_target(target, registry)
    uid = entry["uid"]
    if uid not in devices_mod.connected_uids():
        raise ValueError(f"device not connected: {target}")
    port = devices_mod.port_serial_map({uid}).get(uid) or entry.get("port")
    if not port:
        raise ValueError(f"no serial port for device: {target}")
    return port


def _cmd_connect(args: argparse.Namespace) -> int:
    import mbdeploy.devices as devices_mod
    from mbdeploy import console

    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    registry = devices_mod.load_devices(config_path)

    try:
        port = _connect_port(args.target, registry)
    except ValueError as exc:
        print(f"Error: {exc}. Run 'mbdeploy probe' first.", file=sys.stderr)
        return 1

    try:
        ser = console.open_port(port, args.baud)
    except console.ConsoleError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        if not args.message:
            print(
                f"connected to {port} at {args.baud} baud "
                "— Ctrl-D or Ctrl-C to exit",
                file=sys.stderr,
            )
            return console.interact(ser)

        # One-shot: the reply is the command's output, so it goes to stdout
        # while every status line goes to stderr.
        lines = console.send_command(
            ser, " ".join(args.message), timeout=args.timeout
        )
        for line in lines:
            print(line)
        if not lines:
            print(
                f"Error: no response from {port} within {args.timeout:g}s.",
                file=sys.stderr,
            )
            return 1
        return 0
    finally:
        ser.close()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class _IntermixedSubparser(argparse.ArgumentParser):
    """Subparser that allows options to sit between two positional groups.

    ``connect <target> --baud 9600 "HELLO"`` interleaves an option with the
    positionals, which plain argparse cannot place: it matches positionals
    greedily in one pass, gives ``target`` the leading chunk, and then reports
    the trailing message as unrecognised.  ``parse_known_intermixed_args``
    exists for exactly this shape but subparsers never reach for it, so every
    mbdeploy subparser opts in.  The guard is needed because the intermixed
    parser drives ``parse_known_args`` internally.
    """

    _intermixing = False

    def parse_known_args(self, args=None, namespace=None):
        if self._intermixing:
            return super().parse_known_args(args, namespace)
        self._intermixing = True
        try:
            return self.parse_known_intermixed_args(args, namespace)
        finally:
            self._intermixing = False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mbdeploy",
        description="Build and deploy micro:bit firmware to one or more devices.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"mbdeploy {__version__}",
        help="Print the mbdeploy version and exit.",
    )
    parser.add_argument(
        "--agent",
        action=_AgentManualAction,
        help="Print the detailed agent manual (usage, recipes) and exit.",
    )
    subparsers = parser.add_subparsers(
        dest="subcommand",
        metavar="<subcommand>",
        parser_class=_IntermixedSubparser,
    )
    subparsers.required = True

    # --- build ---
    build_p = subparsers.add_parser(
        "build",
        help="Compile the micro:bit firmware.",
    )
    build_p.add_argument("--clean", action="store_true", help="Clean before building.")
    build_p.add_argument("--verbose", action="store_true", help="Show build output.")
    build_p.add_argument("-j", dest="jobs", type=int, metavar="N", help="Parallel jobs.")
    build_p.add_argument(
        "--build-cmd", metavar="CMD", dest="build_cmd",
        help="Override the build command."
    )
    build_p.set_defaults(func=_cmd_build)

    # --- deploy ---
    deploy_p = subparsers.add_parser(
        "deploy",
        help="Flash firmware to one or more micro:bit devices.",
    )
    deploy_p.add_argument(
        "target",
        nargs="?",
        metavar="target",
        help="Target device: enum, port, UID, or name (default: auto-pick unique non-relay).",
    )
    deploy_p.add_argument(
        "--build", action="store_true", help="Build before deploying."
    )
    deploy_p.add_argument(
        "--clean", action="store_true", help="Clean before building (implies --build)."
    )
    deploy_p.add_argument("-j", dest="jobs", type=int, metavar="N", help="Parallel jobs.")
    deploy_p.add_argument(
        "--force-relay",
        action="store_true",
        dest="force_relay",
        help="Allow deploying to a relay device.",
    )
    deploy_p.add_argument("--hex", metavar="PATH", help="Path to a pre-built .hex file.")
    deploy_p.add_argument(
        "--target-mcu",
        metavar="MCU",
        dest="target_mcu",
        default=_DEFAULT_MCU,
        help=f"Target MCU type (default: {_DEFAULT_MCU}).",
    )
    deploy_p.add_argument(
        "--config", metavar="PATH", help="Path to device config file."
    )
    deploy_p.set_defaults(func=_cmd_deploy)

    # --- list ---
    list_p = subparsers.add_parser(
        "list",
        help="List detected micro:bit devices.",
    )
    list_p.add_argument("--config", metavar="PATH", help="Path to device config file.")
    list_p.add_argument(
        "--fast",
        action="store_true",
        help="Skip reading board names over SWD for devices missing from the registry.",
    )
    list_p.add_argument(
        "--target-mcu",
        metavar="MCU",
        dest="target_mcu",
        default=_DEFAULT_MCU,
        help=f"Target MCU type used when reading board names (default: {_DEFAULT_MCU}).",
    )
    list_p.set_defaults(func=_cmd_list)

    # --- probe ---
    probe_p = subparsers.add_parser(
        "probe",
        help="Probe connected micro:bit devices and update the registry.",
    )
    probe_p.add_argument("--config", metavar="PATH", help="Path to device config file.")
    probe_p.add_argument(
        "--target-mcu",
        metavar="MCU",
        dest="target_mcu",
        default=_DEFAULT_MCU,
        help=f"Target MCU type used when reading board names (default: {_DEFAULT_MCU}).",
    )
    probe_p.add_argument(
        "--clear",
        action="store_true",
        help="Clear the registry before probing, keeping only currently connected devices.",
    )
    probe_p.set_defaults(func=_cmd_probe)

    # --- connect ---
    connect_p = subparsers.add_parser(
        "connect",
        help="Open a serial connection to a device, or send it one line and print the reply.",
    )
    connect_p.add_argument(
        "target",
        metavar="target",
        help="Device to talk to: enum, name, UID, or /dev/ port path.",
    )
    connect_p.add_argument(
        "message",
        nargs="*",
        metavar="word",
        help="Text to send, joined with spaces and terminated with a newline. "
             "Omit it for an interactive session.",
    )
    connect_p.add_argument(
        "--baud",
        type=int,
        default=_DEFAULT_BAUD,
        metavar="N",
        help=f"Serial baud rate (default: {_DEFAULT_BAUD}).",
    )
    connect_p.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_CONNECT_TIMEOUT,
        metavar="SEC",
        help="How long to wait for the reply to a sent message "
             f"(default: {_DEFAULT_CONNECT_TIMEOUT:g}).",
    )
    connect_p.add_argument(
        "--config", metavar="PATH", help="Path to device config file."
    )
    connect_p.set_defaults(func=_cmd_connect)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
