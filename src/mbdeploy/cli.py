"""mbdeploy CLI — entry point and subcommand definitions."""

from __future__ import annotations

import argparse
import shlex
import shutil
import signal
import socket
import sys
import threading
from importlib import resources
from pathlib import Path
from typing import Any

from mbdeploy import __version__


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

#: How long `connect --remote` waits, right after connecting, to see
#: whether the daemon sent an immediate `ERR ...` line (e.g. `ERR busy`
#: from a second client racing an already-claimed board) before treating
#: the connection as a normal session. Short: a real `ERR ...` line is
#: the very first (and only) thing `serve_serial` ever writes before a
#: raw relay begins, sent synchronously on accept -- this only has to
#: cover one LAN round trip, not whatever pace a real board talks at
#: afterwards. Module-level so a test can `monkeypatch.setattr` it small.
_REMOTE_ERR_PEEK_TIMEOUT = 0.3

# Mirrors server.DEFAULT_POLL_INTERVAL, kept here for the same reason: building
# the parser (and printing --help) shouldn't have to import mbdeploy.server
# (and transitively pyocd/zeroconf) at all.
_DEFAULT_POLL_INTERVAL = 2.0

#: How long `serve`'s shutdown waits for the accept-loop/Supervisor threads
#: to actually exit before giving up on them anyway -- shutdown must never
#: hang past this on a thread that refuses to die.
_SERVE_JOIN_TIMEOUT = 5.0

_AGENT_MANUAL = "agent_manual.md"

# ---------------------------------------------------------------------------
# `serve --print-service` / `--install-service`
# ---------------------------------------------------------------------------

_SERVICE_RESOURCE_DIR = "service"
_SYSTEMD_UNIT_TEMPLATE_NAME = "mbdeploy.service.template"
_SYSTEMD_UNIT_FILENAME = "mbdeploy.service"

#: Default install targets. Module-level constants (rather than hardcoded
#: string literals inline) so tests can `monkeypatch.setattr` them to a
#: `tmp_path` and exercise the real install path without ever writing into
#: an actual `/etc` or the invoking user's real home directory.
_SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
_USER_UNIT_DIR = Path("~/.config/systemd/user")
_SYSTEM_TOKEN_DIR = Path("/etc/mbdeploy")
_USER_TOKEN_DIR = Path("~/.config/mbdeploy")
_TOKEN_FILENAME = "token"


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
# Service unit rendering / installation
# ---------------------------------------------------------------------------

def _read_systemd_unit_template() -> str:
    """Return the bundled systemd unit template shipped inside the package."""
    return (
        resources.files("mbdeploy")
        .joinpath(_SERVICE_RESOURCE_DIR, _SYSTEMD_UNIT_TEMPLATE_NAME)
        .read_text(encoding="utf-8")
    )


def _unit_install_dir(scope: str) -> Path:
    """Directory the generated unit is written into for `scope`
    ("system" or "user"). Read from the module-level constants at call
    time (not cached), so a test's `monkeypatch.setattr` on
    `_SYSTEM_UNIT_DIR`/`_USER_UNIT_DIR` is honored."""
    base = _USER_UNIT_DIR if scope == "user" else _SYSTEM_UNIT_DIR
    return base.expanduser()


def _token_install_dir(scope: str) -> Path:
    """Directory an `--install-service --token`-supplied secret is
    written into, for the same `scope`. Same call-time lookup as
    `_unit_install_dir`, for the same testability reason."""
    base = _USER_TOKEN_DIR if scope == "user" else _SYSTEM_TOKEN_DIR
    return base.expanduser()


def _resolve_mbdeploy_executable() -> list[str]:
    """Return the argv-prefix that should invoke mbdeploy in `ExecStart`.

    Prefers the installed console-script (`mbdeploy`) if it is on `PATH`
    -- the normal case once installed via pip/pipx, and more portable
    than pinning to one venv's interpreter. Falls back to
    `<sys.executable> -m mbdeploy.cli`, invocable from any interpreter
    mbdeploy is importable from, rather than trusting `sys.argv[0]`:
    `serve --print-service`/`--install-service` isn't always invoked as
    literally ``mbdeploy serve`` (e.g. under a test runner), so
    `sys.argv[0]` would bake in the wrong program.
    """
    found = shutil.which("mbdeploy")
    if found:
        return [str(Path(found).resolve())]
    return [sys.executable, "-m", "mbdeploy.cli"]


def _resolve_install_token(
    args: argparse.Namespace, scope: str, *, allow_write: bool
) -> tuple[str | None, str | None]:
    """Return `(token_file_for_exec_start, secret_to_write)` for the
    generated unit.

    `--token-file` already names an existing file, so it is resolved to
    an absolute path and passed straight through with nothing to write.
    `--token` supplies a literal secret that must never land in
    `ExecStart` (world-readable via `systemctl cat`); when `allow_write`
    is set (`--install-service`) it is redirected to a fresh token file
    under `_token_install_dir(scope)`, returned alongside the secret so
    the caller can write it out (mode 0600) before/with the unit itself.
    `--print-service` (`allow_write=False`) touches no filesystem, so
    there is nowhere to put that secret -- raises `ValueError` instead of
    ever emitting it literally. Neither flag given -> `(None, None)`,
    matching `serve`'s own fully-open default.
    """
    if args.token_file is not None:
        return str(Path(args.token_file).expanduser().resolve()), None
    if args.token is not None:
        if not allow_write:
            raise ValueError(
                "--token cannot be combined with --print-service: nothing "
                "is written to disk, so there is nowhere to store the "
                "secret, and it must never appear literally in ExecStart "
                "(world-readable via 'systemctl cat'). Use --token-file, "
                "or --install-service to have the secret written to a "
                "file automatically."
            )
        token_path = _token_install_dir(scope) / _TOKEN_FILENAME
        return str(token_path), args.token
    return None, None


def _build_exec_start(
    args: argparse.Namespace,
    config_path: Path,
    *,
    token_file_for_exec: str | None,
) -> str:
    """Return the full `ExecStart=` command line for the generated unit.

    Every daemon-affecting flag from the `serve` subparser is baked in
    explicitly using its *effective* value -- including the resolved
    (always absolute) `--config` path -- since a service manager gives
    the process no useful CWD to resolve a relative one against later.
    Service-management flags (`--print-service`/`--install-service`/
    `--system`/`--user`) are naturally excluded: they configure this
    render, not the running daemon. Never `args.token` -- see
    `_resolve_install_token`.
    """
    parts = [shlex.quote(p) for p in _resolve_mbdeploy_executable()]
    parts.append("serve")
    parts += ["--config", shlex.quote(str(config_path))]
    parts += ["--poll-interval", f"{args.poll_interval:g}"]
    parts += ["--base-port", str(args.base_port)]
    if args.bind:
        parts += ["--bind", shlex.quote(args.bind)]
    if token_file_for_exec is not None:
        parts += ["--token-file", shlex.quote(token_file_for_exec)]
    if args.no_flash:
        parts.append("--no-flash")
    parts += ["--target-mcu", shlex.quote(args.target_mcu)]
    if args.service_name:
        parts += ["--service-name", shlex.quote(args.service_name)]
    return " ".join(parts)


def _render_service_unit(
    *,
    scope: str,
    working_directory: Path,
    exec_start: str,
    token_file_for_exec: str | None,
) -> str:
    """Render the bundled systemd unit template for one `serve` invocation.

    `scope` ("system" or "user") only affects `WantedBy=`: a system unit
    wants `multi-user.target`, a user unit wants `default.target` (there
    is no `multi-user.target` in a `--user` manager instance).
    """
    token_comment = ""
    if token_file_for_exec is not None:
        token_comment = (
            "# A secret was configured for this daemon. It is written to "
            f"{token_file_for_exec} (mode 0600) and referenced below via "
            "--token-file -- never as a literal --token, which would be "
            "readable by anyone via `systemctl cat`.\n"
        )
    wanted_by = "default.target" if scope == "user" else "multi-user.target"
    return _read_systemd_unit_template().format(
        token_comment=token_comment,
        description=(
            "mbdeploy fleet daemon: watches USB for micro:bit boards and "
            "advertises each one's serial/flash services over mDNS."
        ),
        working_directory=str(working_directory),
        exec_start=exec_start,
        wanted_by=wanted_by,
    )


def _cmd_serve_service(args: argparse.Namespace) -> int:
    """Handle `serve --print-service` / `--install-service`.

    Neither runs the daemon. `WorkingDirectory` is the directory `serve`
    was invoked from (this process's CWD at the moment this runs), taken
    absolute via `Path.cwd().resolve()`; `--config` is resolved against
    that same CWD when it was given as a relative path (or when it
    defaults to the CWD-relative `_DEFAULT_CONFIG`), so the generated
    unit's `ExecStart` never depends on a CWD the service manager won't
    provide.
    """
    scope = args.service_scope or "system"
    working_directory = Path.cwd().resolve()
    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    if not config_path.is_absolute():
        config_path = (working_directory / config_path).resolve()

    try:
        token_file_for_exec, secret_to_write = _resolve_install_token(
            args, scope, allow_write=args.install_service
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    exec_start = _build_exec_start(
        args, config_path, token_file_for_exec=token_file_for_exec
    )
    unit_text = _render_service_unit(
        scope=scope,
        working_directory=working_directory,
        exec_start=exec_start,
        token_file_for_exec=token_file_for_exec,
    )

    if args.print_service:
        sys.stdout.write(unit_text if unit_text.endswith("\n") else unit_text + "\n")
        return 0

    unit_dir = _unit_install_dir(scope)
    unit_path = unit_dir / _SYSTEMD_UNIT_FILENAME
    try:
        unit_dir.mkdir(parents=True, exist_ok=True)
        if secret_to_write is not None:
            token_path = Path(token_file_for_exec)  # type: ignore[arg-type]
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(secret_to_write + "\n", encoding="utf-8")
            token_path.chmod(0o600)
        unit_path.write_text(unit_text, encoding="utf-8")
    except OSError as exc:
        sudo_hint = (
            " Installing the system unit requires root -- re-run with sudo."
            if scope == "system" else ""
        )
        print(
            f"Error: cannot install service unit at {unit_path}: {exc}."
            f"{sudo_hint}",
            file=sys.stderr,
        )
        return 1

    print(f"mbdeploy: installed {unit_path}")
    if scope == "user":
        print(
            "mbdeploy: this is a systemd --user unit. On a host with the "
            "default Linger=no (e.g. Nolanet nodes), it will NOT start at "
            "boot and stops when the installing user logs out. Run "
            "'loginctl enable-linger <user>' if this daemon must survive "
            "logout/reboot, or reinstall with --system instead.",
            file=sys.stderr,
        )
    return 0


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

# `list --remote`'s table: no CONN/PORT columns (meaningless for a board
# reached only via mDNS -- every row is, by construction, currently
# advertising, and a board joining both service types can carry two
# different network ports, neither of which is the one right value for a
# column that used to mean "the local serial device path"), a HOST column
# in their place. Same style as `_ROW_FMT` (same column ordering/widths
# for the columns the two tables share), not a second table format.
_REMOTE_ROW_FMT = (
    "{enum:<5} {name:<12} {common:<12} {role:<13} {host:<24} {uid}"
)
_REMOTE_TABLE_HEADER = _REMOTE_ROW_FMT.format(
    enum="ENUM", name="DEVICE NAME", common="COMMON NAME",
    role="ROLE", host="HOST", uid="UID",
)


def _print_device_table(rows: list[dict], remote: bool = False) -> None:
    """Print the shared `list` / `probe` / `list --remote` table.

    `remote=False` (the default -- every existing caller) is byte-for-byte
    unchanged from before this parameter existed: same header, same
    `_ROW_FMT`, same output for the same rows. `remote=True` (`list
    --remote` only) swaps in `_REMOTE_ROW_FMT`/`_REMOTE_TABLE_HEADER`
    instead, adding the HOST column described above.
    """
    header = _REMOTE_TABLE_HEADER if remote else _TABLE_HEADER
    row_fmt = _REMOTE_ROW_FMT if remote else _ROW_FMT
    print(header)
    print("-" * len(header))
    for row in rows:
        print(row_fmt.format(**row))


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


def _cmd_list_remote(args: argparse.Namespace) -> int:
    """`list --remote`: browse the LAN instead of local USB devices.

    No local registry, no `devices_mod` import, no target argument --
    `remote.list_remote()` already returns exactly the rows to print.
    `--fast`/`--target-mcu` are local-only (they control the SWD name
    read `list_remote()` never performs -- board names come from mDNS,
    not a debug probe) and are silently ignored here, per this ticket's
    `--remote` `--help` text.

    Unlike local `list`, an empty result prints an empty table (header
    plus zero rows) rather than "no devices found": no boards currently
    advertising on the LAN is an unremarkable, momentary state for a
    network listing (nothing is unplugged, there is simply nothing to
    show right now), not the same as a *local* board that was probed and
    should still be sitting in the registry.
    """
    from mbdeploy import remote as remote_mod

    rows = remote_mod.list_remote()
    _print_device_table(rows, remote=True)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    if getattr(args, "remote", False):
        return _cmd_list_remote(args)

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
    the OS re-issues serial port names (e.g. ``/dev/cu.usbmodem*`` on macOS)
    on every reconnect.  Matching one would return the board that *used to*
    sit on that path, and ``deploy`` would then flash that board's UID —
    writing firmware to a different, currently-connected board than the path
    names.  ``connect`` sidesteps this by opening the path verbatim (see
    :func:`_connect_port`); ``deploy`` cannot, because pyOCD addresses a
    board by UID, so the path is translated through the *live* serial-port
    mapping instead (a ``pyserial`` VID:PID scan, on macOS or Linux alike):
    whichever board is on that port right now is the one that gets flashed.

    That live UID must still be present in the registry.  The entry is where
    ``role`` comes from, and ``role`` is what the relay guard reads, so
    flashing an unregistered UID would mean flashing with no relay guard at
    all.  Better to send the user to ``probe`` once.

    Raises ``ValueError`` if the target cannot be resolved.
    """
    import mbdeploy.devices as devices_mod

    if not (target.startswith("/dev/") or "/" in target):
        return devices_mod.resolve_target(target, registry)

    # Restrict the live serial-port scan to connected CMSIS-DAP probes so
    # some other USB serial device can never be mistaken for a micro:bit.
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
            f"cannot resolve port '{target}': no micro:bit serial port was "
            "found, even though a probe is connected. On Linux, check that "
            "this user is in the 'plugdev'/'dialout' group. Refusing to fall "
            "back to the registry's recorded port, which may name a "
            "different board. Target by enum, name, or UID instead."
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
    from mbdeploy import flash as flash_mod

    return flash_mod.flash_hex(uid, hex_path, target_mcu)


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


def _run_connect_session(
    ser: Any, args: argparse.Namespace, *, banner_target: str, error_target: str
) -> int:
    """Run `connect`'s one-shot or interactive session against an
    already-open `ser`, closing it before returning.

    Shared by local `connect` and `connect --remote`: both hand this
    something satisfying `console.py`'s duck-typed serial contract -- a
    real pyserial port for the local path, a `remote.SocketSerial`
    wrapping a connected TCP socket for `--remote` -- and this function
    never branches on which one it got (sprint.md Step 3's R2: zero
    lines of `console.py` change, and no new branching outside
    `_cmd_connect`'s own port-vs-socket setup). `banner_target`/
    `error_target` let each caller phrase "connected to ..."/"no
    response from ..." in whatever way fits its own address space (a
    port-and-baud pair locally, a resolved board-and-host:port remotely)
    without this function needing to know which case it is in.
    """
    from mbdeploy import console

    try:
        if not args.message:
            print(
                f"connected to {banner_target} — Ctrl-D or Ctrl-C to exit",
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
                f"Error: no response from {error_target} within {args.timeout:g}s.",
                file=sys.stderr,
            )
            return 1
        return 0
    finally:
        ser.close()


def _peek_remote_err(sock: socket.socket, timeout: float) -> str | None:
    """Return the message text of an immediate `ERR ...` line the daemon
    sent, if any, without consuming it off `sock`.

    Uses `socket.MSG_PEEK` deliberately: `serve_serial`'s raw byte pipe is
    unframed (the issue's own "No handshake" wire-protocol note), so
    anything read here that turns out *not* to be a pre-relay `ERR ...`
    line must be left exactly where `console.py`'s own reads will find
    it -- a real board's first bytes of ordinary output are not this
    function's to consume. A read timeout (nothing arrived within
    `timeout`) is the overwhelmingly common case -- every Nolanet board
    is silent, and no board speaks before it is spoken to -- and is not
    an error: returns `None` so the caller proceeds to a normal session.
    """
    sock.settimeout(timeout)
    try:
        peek = sock.recv(4096, socket.MSG_PEEK)
    except (socket.timeout, OSError):
        return None
    if not peek.startswith(b"ERR "):
        return None
    line = peek.split(b"\n", 1)[0]
    return line[len(b"ERR "):].decode("utf-8", "replace")


def _cmd_connect_remote(args: argparse.Namespace) -> int:
    """`connect --remote`: resolve a board over mDNS and open a TCP
    session instead of a local serial port.

    Resolves `args.target` against `_mbserial._tcp` via
    `remote.resolve_board`, opens a socket to the resolved
    `{host, port}`, and wraps it in `remote.SocketSerial` -- the adapter
    that lets `console.send_command()`/`console.interact()` run
    completely unmodified against a network connection instead of a real
    pyserial port. `--baud` is meaningless here -- the daemon already has
    the board's local serial port open at whatever baud `serve` was
    started with -- and is silently ignored, per this flag's own
    `--help` text.
    """
    from mbdeploy import remote as remote_mod

    try:
        board = remote_mod.resolve_board(args.target, remote_mod.SERIAL_SERVICE_TYPE)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    host, port = board["host"], board["port"]
    label = f"{board['name']} ({host}:{port})"

    try:
        sock = socket.create_connection((host, port), timeout=args.timeout)
    except OSError as exc:
        print(f"Error: cannot connect to {label}: {exc}", file=sys.stderr)
        return 1

    err = _peek_remote_err(sock, _REMOTE_ERR_PEEK_TIMEOUT)
    if err is not None:
        sock.close()
        print(f"Error: {label} refused the connection: {err}", file=sys.stderr)
        return 1

    ser = remote_mod.SocketSerial(sock)
    return _run_connect_session(ser, args, banner_target=label, error_target=label)


def _cmd_connect(args: argparse.Namespace) -> int:
    remote = getattr(args, "remote", False)
    if remote and (args.target.startswith("/dev/") or "/" in args.target):
        print(
            f"Error: --remote cannot be combined with a device path "
            f"('{args.target}').",
            file=sys.stderr,
        )
        return 1

    if remote:
        return _cmd_connect_remote(args)

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

    return _run_connect_session(
        ser, args,
        banner_target=f"{port} at {args.baud} baud",
        error_target=port,
    )


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

def _resolve_serve_token(args: argparse.Namespace) -> str | None:
    """Resolve `--token`/`--token-file` to a single secret string, or
    `None` if neither was given.

    `--token` is used verbatim. `--token-file` reads the file and strips
    trailing whitespace/newline before using the result; argparse's
    mutually-exclusive group guarantees at most one of `args.token`/
    `args.token_file` is set, so there is nothing to reconcile here.
    Neither given -> `None`, matching today's fully-open default: no
    `AUTH` is required by `serve_serial`/`serve_flash`.

    Raises `ValueError` (never touches stdout/stderr itself) on a
    missing `--token-file` or one that is empty after stripping -- the
    caller is responsible for turning that into a clean, non-zero-exit
    startup error rather than a silent "no auth."
    """
    if args.token is not None:
        return args.token
    if args.token_file is not None:
        try:
            text = Path(args.token_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"cannot read --token-file '{args.token_file}': {exc}"
            ) from exc
        token = text.rstrip()
        if not token:
            raise ValueError(f"--token-file '{args.token_file}' is empty")
        return token
    return None


def _build_serve_runtime(
    args: argparse.Namespace, config_path: Path, token: str | None
) -> tuple[Any, Any, Any]:
    """Construct `(advertiser, accept_loop, supervisor)` from parsed
    `serve` args.

    Pure construction only -- no thread is started and no signal handler
    is installed here -- so a test can inspect exactly what each
    component was built with (e.g. `--bind` reaching both the
    `Advertiser` constructor and `Supervisor.bind`) without ever running
    the blocking serve loop below.
    """
    from mbdeploy import mdns as mdns_mod
    from mbdeploy import server as server_mod

    advertiser = mdns_mod.Advertiser(bind_addr=args.bind or None)
    accept_loop = server_mod.AcceptLoop()
    supervisor = server_mod.Supervisor(
        accept_loop=accept_loop,
        advertiser=advertiser,
        config_path=config_path,
        base_port=args.base_port,
        bind=args.bind or "",
        target_mcu=args.target_mcu,
        token=token,
        no_flash=args.no_flash,
        service_name=args.service_name,
    )
    return advertiser, accept_loop, supervisor


class _ServeShutdown:
    """Signal handler (and manually-callable equivalent) for `serve`.

    Unregisters every mDNS advertisement and closes every listener
    socket, then signals the run loop to stop -- exactly once, no matter
    how many times it is invoked. SIGINT and SIGTERM both wire to the
    *same* instance, and systemd may deliver more than one signal during
    a slow shutdown, so a second (or concurrent) invocation must be a
    silent no-op, never a re-entrant close or a traceback on an
    already-closed socket -- that idempotency is the whole reason this
    is a class with a guarded `_done` flag rather than a plain function.

    Before touching the `Advertiser` or any listener socket, this joins
    `supervisor_thread` (bounded by `join_timeout`) if one was given.
    `stop_event` only prevents the Supervisor's poll loop from starting
    *another* tick -- it does not interrupt a tick already in flight, and
    a tick can be in the middle of `Advertiser.register()` (a board
    arriving) when a signal lands. Closing the `Advertiser` out from
    under that in-flight call, observed against a real `zeroconf`
    backend, corrupts its internal event loop instead of cleanly
    unregistering (``RuntimeError: Event loop is closed``) -- joining
    first lets that tick finish before anything is torn down.
    """

    def __init__(
        self,
        supervisor: Any,
        accept_loop: Any,
        advertiser: Any,
        stop_event: threading.Event,
        *,
        supervisor_thread: threading.Thread | None = None,
        join_timeout: float = _SERVE_JOIN_TIMEOUT,
    ) -> None:
        self._supervisor = supervisor
        self._accept_loop = accept_loop
        self._advertiser = advertiser
        self._stop_event = stop_event
        self._supervisor_thread = supervisor_thread
        self._join_timeout = join_timeout
        self._lock = threading.Lock()
        self._done = False

    def __call__(self, signum: int | None = None, frame: Any = None) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True

        self._stop_event.set()

        thread = self._supervisor_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=self._join_timeout)
            if thread.is_alive():
                print(
                    "mbdeploy serve: Supervisor thread did not exit within "
                    f"{self._join_timeout:g}s of shutdown; unregistering "
                    "anyway.",
                    file=sys.stderr,
                )

        for board in list(self._supervisor.boards.values()):
            for sock in (board.serial_listener, board.flash_listener):
                if sock is None:
                    continue
                try:
                    self._accept_loop.unregister(sock)
                except Exception:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass

        self._advertiser.close()
        self._accept_loop.close()


def _run_serve(
    supervisor: Any,
    accept_loop: Any,
    advertiser: Any,
    poll_interval: float,
) -> int:
    """Run `serve`'s accept loop and USB watcher until SIGINT/SIGTERM,
    then return 0.

    Foreground only -- no self-daemonizing, no pidfile; systemd captures
    stdout into the journal. SIGINT and SIGTERM both wire to the same
    `_ServeShutdown` instance (installed here, before the accept loop and
    the Supervisor's poll loop start on their own threads), so either
    signal unregisters every mDNS advertisement and closes every
    listener socket before this returns.
    """
    stop_event = threading.Event()

    # Built (but not started) before `_ServeShutdown` so its shutdown
    # handler can join `supervisor_thread` before touching the Advertiser
    # or any listener socket -- see `_ServeShutdown`'s docstring for why
    # that ordering matters.
    accept_thread = threading.Thread(
        target=accept_loop.run, name="mbdeploy-accept", daemon=True
    )
    supervisor_thread = threading.Thread(
        target=supervisor.run,
        kwargs={"poll_interval": poll_interval, "stop": stop_event},
        name="mbdeploy-supervisor",
        daemon=True,
    )

    shutdown = _ServeShutdown(
        supervisor, accept_loop, advertiser, stop_event,
        supervisor_thread=supervisor_thread,
    )
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    accept_thread.start()
    supervisor_thread.start()

    print(
        f"mbdeploy serve: running (poll every {poll_interval:g}s; "
        "Ctrl-C or SIGTERM to stop)",
        flush=True,
    )
    stop_event.wait()
    shutdown()  # idempotent -- a no-op if a signal already ran it

    accept_thread.join(timeout=_SERVE_JOIN_TIMEOUT)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    if args.print_service or args.install_service:
        return _cmd_serve_service(args)

    try:
        token = _resolve_serve_token(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    advertiser, accept_loop, supervisor = _build_serve_runtime(args, config_path, token)
    return _run_serve(supervisor, accept_loop, advertiser, args.poll_interval)


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
    list_p.add_argument(
        "--remote",
        action="store_true",
        help="List boards currently advertising on the LAN via mDNS instead "
             "of local USB devices; no local registry is used and no target "
             "argument is taken. --fast/--target-mcu are ignored in this "
             "mode (list --remote never reads a board name over SWD).",
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
        help=f"Serial baud rate (default: {_DEFAULT_BAUD}). Ignored by "
             "--remote -- the daemon already has the board's local port "
             "open at whatever baud it was started with.",
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
    connect_p.add_argument(
        "--remote",
        action="store_true",
        help="Resolve target as a board name currently advertising "
             "_mbserial._tcp on the LAN via mDNS, and connect to it over "
             "the network instead of a local serial port. Mutually "
             "exclusive with a /dev/... target -- rejected before any "
             "mDNS lookup or socket I/O. --baud is ignored in this mode.",
    )
    connect_p.set_defaults(func=_cmd_connect)

    # --- serve ---
    serve_p = subparsers.add_parser(
        "serve",
        help="Run the fleet daemon: watch USB and advertise each board's "
             "serial/flash services over mDNS.",
    )
    serve_p.add_argument("--config", metavar="PATH", help="Path to device config file.")
    serve_p.add_argument(
        "--poll-interval",
        type=float,
        default=_DEFAULT_POLL_INTERVAL,
        dest="poll_interval",
        metavar="SEC",
        help=f"Seconds between USB polls (default: {_DEFAULT_POLL_INTERVAL:g}).",
    )
    serve_p.add_argument(
        "--base-port",
        type=int,
        default=0,
        dest="base_port",
        metavar="N",
        help="First of a sequential port pair handed to each board "
             "(default: 0, meaning OS-assigned ephemeral ports).",
    )
    serve_p.add_argument(
        "--bind",
        default="",
        metavar="ADDR",
        help="Address to bind listener sockets and advertise via mDNS "
             "(default: all interfaces).",
    )
    token_group = serve_p.add_mutually_exclusive_group()
    token_group.add_argument(
        "--token",
        metavar="SECRET",
        help="Shared secret clients must send via 'AUTH <token>' before "
             "either service does anything else. Mutually exclusive with "
             "--token-file.",
    )
    token_group.add_argument(
        "--token-file",
        metavar="PATH",
        dest="token_file",
        help="Read the shared secret from PATH instead of the command line, "
             "so it never appears in 'systemctl cat's ExecStart output. "
             "Mutually exclusive with --token.",
    )
    serve_p.add_argument(
        "--no-flash",
        action="store_true",
        dest="no_flash",
        help="Reject every FLASH request with 'ERR flash disabled', "
             "without ever touching flash_hex.",
    )
    serve_p.add_argument(
        "--target-mcu",
        metavar="MCU",
        dest="target_mcu",
        default=_DEFAULT_MCU,
        help=f"Target MCU type (default: {_DEFAULT_MCU}).",
    )
    serve_p.add_argument(
        "--service-name",
        metavar="NAME",
        dest="service_name",
        help="Override the mDNS instance name for every board this process "
             "manages, bypassing the board_name/device_name/mb-<uid8> "
             "fallback chain. Only meaningful on a single-board host.",
    )
    serve_p.add_argument(
        "--print-service",
        action="store_true",
        dest="print_service",
        help="Render the systemd unit for this exact `serve` invocation "
             "(baking in the resolved --config path and this process's "
             "current directory as WorkingDirectory) to stdout, and exit "
             "without touching the filesystem or running the daemon.",
    )
    serve_p.add_argument(
        "--install-service",
        action="store_true",
        dest="install_service",
        help="Write the generated systemd unit to disk and exit without "
             "running the daemon. Defaults to the SYSTEM unit "
             "(/etc/systemd/system/mbdeploy.service, requires root/sudo) "
             "-- the binding deployment choice, since a Docker Swarm "
             "service can't reach a DAPLink (no --device/--privileged) "
             "and a systemd --user unit will not start at boot on a host "
             "with the default Linger=no (e.g. Nolanet nodes). Pass "
             "--user to opt into ~/.config/systemd/user/mbdeploy.service "
             "instead, for a workstation where that tradeoff is fine.",
    )
    service_scope_group = serve_p.add_mutually_exclusive_group()
    service_scope_group.add_argument(
        "--system",
        action="store_const",
        dest="service_scope",
        const="system",
        help="With --print-service/--install-service, target the system "
             "unit (default).",
    )
    service_scope_group.add_argument(
        "--user",
        action="store_const",
        dest="service_scope",
        const="user",
        help="With --print-service/--install-service, target a per-user "
             "unit instead of the system default. Needs "
             "'loginctl enable-linger <user>' on a host with Linger=no "
             "(the default) for the unit to start at boot or survive "
             "logout.",
    )
    serve_p.set_defaults(func=_cmd_serve, service_scope=None)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
