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


def _cmd_list(args: argparse.Namespace) -> int:
    import mbdeploy.devices as devices_mod

    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG

    probes = devices_mod.flashable_probes()
    ports = devices_mod.port_serial_map({p["uid"] for p in probes}) if probes else {}
    registry = devices_mod.load_devices(config_path)

    if not probes:
        print("no devices found")
        return 0

    # Build display rows: merge live probe info with registry annotation
    print(f"{'ENUM':<6} {'UID':<44} {'PORT':<26} {'ROLE':<16} {'NAME'}")
    print("-" * 110)
    for probe in probes:
        uid = probe["uid"]
        port = ports.get(uid, "")
        entry = registry.get(uid, {})
        enum_val = str(entry.get("enum", ""))
        role = entry.get("role", "")
        name = entry.get("common_name") or entry.get("device_name") or ""
        print(f"{enum_val:<6} {uid:<44} {port:<26} {role:<16} {name}")

    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    import mbdeploy.devices as devices_mod

    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG

    entries = devices_mod.probe_all(config_path)

    if not entries:
        print("no devices found")
        return 0

    print(f"{'ENUM':<6} {'UID':<44} {'PORT':<26} {'ROLE':<16} {'NAME'}")
    print("-" * 110)
    for entry in entries:
        enum_val = str(entry.get("enum", ""))
        uid = entry.get("uid", "")
        port = entry.get("port") or ""
        role = entry.get("role") or ""
        name = entry.get("common_name") or entry.get("device_name") or ""
        print(f"{enum_val:<6} {uid:<44} {port:<26} {role:<16} {name}")

    return 0


def _cmd_deploy(args: argparse.Namespace) -> int:
    import mbdeploy.devices as devices_mod

    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    hex_path = args.hex if args.hex else _DEFAULT_HEX
    target_mcu = args.target_mcu if args.target_mcu else _DEFAULT_MCU
    force_relay = args.force_relay

    # --- resolve device entry ---
    registry = devices_mod.load_devices(config_path)

    if args.target:
        # Explicit target — resolve via registry
        try:
            entry = devices_mod.resolve_target(args.target, registry)
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
            names = [e.get("common_name") or e["uid"][:8] for e in non_relay]
            print(
                f"Error: ambiguous — multiple non-relay devices: {names}. "
                "Specify a target.",
                file=sys.stderr,
            )
            return 1
        entry = non_relay[0]

    # --- relay guard ---
    if devices_mod.is_relay(entry.get("role")) and not force_relay:
        label = entry.get("common_name") or entry.get("uid", "unknown")
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


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

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
    subparsers = parser.add_subparsers(dest="subcommand", metavar="<subcommand>")
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
    list_p.set_defaults(func=_cmd_list)

    # --- probe ---
    probe_p = subparsers.add_parser(
        "probe",
        help="Probe connected micro:bit devices and update the registry.",
    )
    probe_p.add_argument("--config", metavar="PATH", help="Path to device config file.")
    probe_p.set_defaults(func=_cmd_probe)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
