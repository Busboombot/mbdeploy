"""devices — micro:bit device discovery and persistent registry.

Registry invariants
-------------------
- Entries are **never deleted**: once a UID is recorded it stays in the JSON.
- ``port`` is **always refreshed** from the current ``port_serial_map()``
  result, even when the HELLO probe fails.
- Prior announcement fields (``role``, ``common_name``, ``device_name``,
  ``serial``, ``announcement``) are **preserved** when ``probe_type`` returns
  None (port busy or silent).  The last known identity is never cleared.
- ``enum`` is **assigned once** and never changes for a given UID.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

try:  # pyserial is optional — absent in CI without hardware
    import serial  # type: ignore
except Exception:  # pragma: no cover
    serial = None  # type: ignore

BAUD_RATE = 115200

DEFAULT_MCU = "nrf52833"

#: nRF5x ``FICR.DEVICEID[1]`` — the 32-bit word the micro:bit runtime hashes
#: into the board's five-letter friendly name.
FICR_DEVICEID1 = 0x10000064

_IOREG_SERIAL_RE = re.compile(r'"USB Serial Number"\s*=\s*"([^"]+)"')
_IOREG_CALLOUT_RE = re.compile(r'"IOCalloutDevice"\s*=\s*"([^"]+)"')

#: CODAL's friendly-name codebook: five base-5 digits, alternating
#: consonants and vowels, most-significant digit first in the printed name.
_NAME_CODEBOOK = (
    ("z", "v", "g", "p", "t"),
    ("u", "o", "i", "e", "a"),
    ("z", "v", "g", "p", "t"),
    ("u", "o", "i", "e", "a"),
    ("z", "v", "g", "p", "t"),
)


# ---------------------------------------------------------------------------
# Primitives (ported from scripts/lib/device_link.py)
# ---------------------------------------------------------------------------

def flashable_probes() -> list[dict[str, str]]:
    """Every connected CMSIS-DAP probe pyOCD could flash: [{uid, description}].

    Uses the pyOCD Python API; falls back to parsing ``pyocd list`` output.
    """
    try:
        from pyocd.core.helpers import ConnectHelper  # type: ignore

        probes = ConnectHelper.get_all_connected_probes(blocking=False)
        out = []
        for p in probes:
            uid = getattr(p, "unique_id", None)
            if uid:
                out.append({"uid": uid, "description": getattr(p, "description", "") or ""})
        return out
    except Exception:
        return _flashable_probes_cli()


def _flashable_probes_cli() -> list[dict[str, str]]:
    """Fallback: parse ``python -m pyocd list`` for UIDs."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pyocd", "list"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    uid_re = re.compile(r"\b([0-9a-fA-F]{40,52})\b")
    for line in proc.stdout.splitlines():
        m = uid_re.search(line)
        if m:
            out.append({"uid": m.group(1), "description": line.strip()})
    return out


def port_serial_map(known: set[str] | None = None) -> dict[str, str]:
    """Map USB serial (== pyOCD UID) -> /dev/cu.* port via ``ioreg`` (macOS only).

    When ``known`` is given, only those UIDs are recorded so a non-micro:bit
    serial port can never be mis-attributed.  Returns ``{}`` off macOS or if
    ``ioreg`` is unavailable.
    """
    try:
        proc = subprocess.run(
            ["ioreg", "-r", "-c", "IOUSBHostDevice", "-l"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}

    out: dict[str, str] = {}
    current_serial: str | None = None
    for line in proc.stdout.splitlines():
        sm = _IOREG_SERIAL_RE.search(line)
        if sm:
            current_serial = sm.group(1)
            continue
        cm = _IOREG_CALLOUT_RE.search(line)
        if cm and current_serial:
            if known is not None and current_serial not in known:
                continue
            out.setdefault(current_serial, cm.group(1))
    return out


def probe_type(port: str, timeout_s: float = 1.6) -> dict[str, str] | None:
    """Open ``port``, send HELLO, and parse the ``DEVICE:`` announcement.

    Returns ``{role, common_name, device_name, serial, raw}`` or ``None`` if no
    announcement arrived (port busy, no firmware, or timed out).
    """
    if serial is None:
        return None
    ser = None
    try:
        ser = serial.Serial(baudrate=BAUD_RATE, timeout=0.12, dsrdtr=False, rtscts=False)
        ser.port = port
        ser.dtr = False
        ser.rts = False
        ser.open()
        time.sleep(0.3)
        ser.reset_input_buffer()
        ser.write(b"HELLO\n")
        ser.flush()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            raw = ser.readline()
            if not raw:
                continue
            text = raw.decode("utf-8", "ignore").strip()
            # TWO announcement dialects, same five fields in the same
            # order -- sentinel, role, common_name, device_name, serial:
            #
            #   relay  DEVICE:RADIOBRIDGE:relay:getez:1779042496
            #          (microbit-radio-relay/docs/announce.md)
            #   robot  device NEZHA2 robot vevov 1198504156
            #          (radio-robot-lib/docs/design/protocol.md S6)
            #
            # Only the colon dialect was accepted until 2026-08-27, so
            # every ROBOT failed to type: probe_type returned None,
            # probe_all took its preserve-existing-fields branch, and
            # role/common_name/device_name/serial were never written.
            # The DEVICE NAME column still filled in, because board_name
            # comes from the separate SWD path (read_device_id ->
            # friendly_name), which masked the gap.
            #
            # Consequence in the field: a robot kept whatever stale role
            # its registry entry last held. vevov, reflashed from relay
            # to robot firmware, stayed labelled RADIOBRIDGE, and
            # `mbdeploy deploy vevov` refused with "relay is a relay"
            # on a board that had been a robot for days.
            #
            # The robot dialect is space-delimited and lowercase because
            # the v6 wire protocol dropped ':' as a field separator when
            # it retired v5 (pxt-nezha-diffdrive dfca4f8, 2026-08-23);
            # the announcement line went with it. This parser was never
            # updated to follow.
            fields = None
            if text.startswith("DEVICE:"):
                parts = text.split(":")
                if len(parts) >= 5:
                    # Serial may itself contain ':' -- rejoin the tail.
                    fields = (parts[1], parts[2], parts[3], ":".join(parts[4:]))
            elif text.startswith("device "):
                parts = text.split()
                if len(parts) >= 5:
                    # Space-delimited: the serial is a single bare token
                    # (the decimal FICR.DEVICEID[1]), so any extra
                    # trailing tokens are not part of it and are ignored.
                    fields = (parts[1], parts[2], parts[3], parts[4])
            if fields is not None:
                return {
                    "role": fields[0],
                    "common_name": fields[1],
                    "device_name": fields[2],
                    "serial": fields[3],
                    "raw": text,
                }
        return None
    except Exception:
        return None
    finally:
        if ser is not None and ser.is_open:
            ser.close()


def friendly_name(device_id: int) -> str:
    """Return the micro:bit five-letter name encoded by ``FICR.DEVICEID[1]``.

    This is CODAL's ``microbit_friendly_name()``: the 32-bit word is written
    out as five base-5 digits, and digit *i* (counting from the least
    significant) selects a letter from column *i* of the codebook, landing at
    position ``4 - i`` of the name.  Example: ``2314287040 -> "tovez"``.
    """
    n = device_id & 0xFFFFFFFF
    letters = [""] * 5
    for i in range(5):
        letters[4 - i] = _NAME_CODEBOOK[i][n % 5]
        n //= 5
    return "".join(letters)


@contextlib.contextmanager
def _quiet_pyocd():
    """Silence pyOCD's logging so it can't interleave with table output."""
    logger = logging.getLogger("pyocd")
    previous = logger.level
    logger.setLevel(logging.CRITICAL + 1)
    try:
        yield
    finally:
        logger.setLevel(previous)


def read_device_id(uid: str, target_mcu: str = DEFAULT_MCU) -> int | None:
    """Read ``FICR.DEVICEID[1]`` from the board behind ``uid`` over SWD.

    The board's name is a property of the *target* nRF, not of the debug
    probe, so it cannot be computed from ``uid`` alone — but it can be read
    through the probe the UID names.  Attaches without halting or resetting,
    and needs no serial port and no cooperating firmware.

    Returns ``None`` if pyOCD is unavailable, the probe is busy (e.g. mid
    flash), or the target refuses the connection (locked part).
    """
    try:
        from pyocd.core.helpers import ConnectHelper  # type: ignore
    except Exception:  # pragma: no cover - pyocd is a declared dependency
        return None
    # DEVICEID lives at the same address on nRF51 and nRF52, so the retry
    # without an override lets pyOCD auto-detect a board (a V1 micro:bit)
    # that the caller's target guess doesn't fit.
    for override in (target_mcu, None):
        try:
            with _quiet_pyocd():
                with ConnectHelper.session_with_chosen_probe(
                    unique_id=uid,
                    target_override=override,
                    connect_mode="attach",
                    blocking=False,
                    auto_unlock=False,
                ) as session:
                    return session.target.read32(FICR_DEVICEID1)
        except Exception:
            continue
    return None


def read_board_name(uid: str, target_mcu: str = DEFAULT_MCU) -> str | None:
    """Return the five-letter micro:bit name of the board behind ``uid``.

    Convenience wrapper over :func:`read_device_id` + :func:`friendly_name`;
    ``None`` when the device id could not be read.
    """
    device_id = read_device_id(uid, target_mcu)
    return friendly_name(device_id) if device_id is not None else None


def is_relay(role: str | None) -> bool:
    """True if a DEVICE: role/type names a radio relay/bridge.

    Matches both ``RADIORELAY`` and the firmware's actual ``RADIOBRIDGE``
    by looking for either token case-insensitively.
    """
    if not role:
        return False
    r = role.upper()
    return "RELAY" in r or "BRIDGE" in r


# ---------------------------------------------------------------------------
# Registry layer
# ---------------------------------------------------------------------------

def load_devices(config_path: Path) -> dict[str, dict]:
    """Read the device registry JSON; return ``{}`` on missing or invalid file."""
    try:
        return json.loads(config_path.read_text())
    except (OSError, ValueError):
        return {}


def save_devices(devices: dict[str, dict], config_path: Path) -> None:
    """Write the device registry JSON, creating parent directories as needed."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(devices, indent=2, sort_keys=True) + "\n")


def assign_enum(devices: dict[str, dict], uid: str) -> int:
    """Return the existing enum for ``uid``, or assign the next available integer.

    The minimum assigned value is 1.  New enums are ``max(existing) + 1``.
    """
    if uid in devices and "enum" in devices[uid]:
        return devices[uid]["enum"]
    existing = [e["enum"] for e in devices.values() if "enum" in e]
    return max(existing, default=0) + 1


def connected_uids() -> set[str]:
    """Return the UID of every micro:bit currently attached over USB."""
    return {p["uid"] for p in flashable_probes()}


def probe_all(
    config_path: Path, clear: bool = False, target_mcu: str = DEFAULT_MCU
) -> list[dict]:
    """Discover all connected probes, probe each port, update and save the registry.

    Merge logic
    -----------
    - ``port`` is always refreshed from ``port_serial_map()``.
    - If ``probe_type`` returns a result, announcement fields are updated.
    - If ``probe_type`` returns None, existing announcement fields are kept.
    - A board that has never yielded a ``board_name`` gets one read over SWD,
      so silent and unflashed boards are still identifiable by name.
    - Boards with no serial port get an entry with ``port: null``.
    - Entries are never deleted.

    Returns the updated list of device dicts.
    """
    devices = {} if clear else load_devices(config_path)
    probes = flashable_probes()
    uids = {p["uid"] for p in probes}
    ports = port_serial_map(uids)

    for p in probes:
        uid = p["uid"]
        entry = devices.get(uid, {})
        entry["uid"] = uid
        entry["port"] = ports.get(uid)           # always refresh
        if "enum" not in entry:
            entry["enum"] = assign_enum(devices, uid)
        port = entry.get("port")
        info = probe_type(port) if port else None
        if info:
            entry["announcement"] = info["raw"]
            entry["role"]         = info["role"]
            entry["common_name"]  = info["common_name"]
            entry["device_name"]  = info["device_name"]
            entry["serial"]       = info["serial"]
        # else: preserve existing announcement fields unchanged
        if not entry.get("board_name"):
            device_id = read_device_id(uid, target_mcu)
            if device_id is not None:
                entry["device_id"] = device_id
                entry["board_name"] = friendly_name(device_id)
        devices[uid] = entry

    save_devices(devices, config_path)
    return list(devices.values())


#: Name fields ``resolve_target`` matches, in order, for a non-numeric,
#: non-path, non-UID token.  Both are the board's own five-letter micro:bit
#: name, reached two ways: ``device_name`` from its ``DEVICE:`` announcement,
#: ``board_name`` read from silicon over SWD.  They agree when both are known.
#:
#: ``common_name`` is deliberately absent.  It is a human label for a board —
#: "Jane's robot" — assigned by whoever set the fleet up, not an identity the
#: board carries.  Two boards can wear the same one, it changes when a class
#: is reassigned, and it says nothing about which hardware is in your hand.
#: It is shown by ``list`` and never matched as a target.
NAME_FIELDS = ("device_name", "board_name")


def resolve_target(token: str, devices: dict[str, dict]) -> dict:
    """Resolve a user-supplied token to a device entry.

    Precedence
    ----------
    1. Pure digits → match by ``enum`` field.
    2. Starts with ``/dev/`` or contains ``/`` → **refused**; see below.
    3. 40–52 hex characters → match by ``uid`` field.
    4. Otherwise → case-insensitive match against :data:`NAME_FIELDS`: the
       board's own five-letter micro:bit name, from its announcement
       (``device_name``) or read from silicon (``board_name``).  This is the
       name the DEVICE NAME column of ``list`` shows.  A ``common_name`` is
       never matched — see :data:`NAME_FIELDS`.

    Why a port path is refused
    --------------------------
    The only port this function could match is the ``port`` recorded in the
    registry, and that is no fresher than the last :func:`probe_all` — macOS
    re-issues ``/dev/cu.usbmodem*`` names on every reconnect, so two boards
    routinely swap paths between probes.  Returning the entry that *used to*
    sit on a path is worse than returning nothing, because the caller goes on
    to act on that entry's UID: a different board than the path names.  That
    was a real wrong-board flash in ``deploy``.

    A caller that accepts a path must therefore resolve it itself: against the
    live :func:`port_serial_map` when it needs a UID (``deploy``), or by using
    the path verbatim when it needs a port (``connect``).

    Raises ``ValueError`` with a descriptive message if no match is found.
    """
    # 1. Numeric enum
    if token.isdigit():
        target_enum = int(token)
        for entry in devices.values():
            if entry.get("enum") == target_enum:
                return entry
        raise ValueError(f"No device found with enum {target_enum}")

    # 2. Port path — never matched against the recorded port (see docstring).
    if token.startswith("/dev/") or "/" in token:
        raise ValueError(
            f"'{token}' is a port path and cannot be resolved from the "
            "registry, whose recorded port may name a different board than "
            "the one on that path now. Resolve it against the live "
            "port_serial_map(), or target by enum, name, or UID."
        )

    # 3. UID (40–52 hex chars)
    if re.fullmatch(r"[0-9a-fA-F]{40,52}", token):
        for entry in devices.values():
            if entry.get("uid") == token:
                return entry
        raise ValueError(f"No device found with uid '{token}'")

    # 4. The board's own five-letter name, announced or read from silicon.
    token_lower = token.lower()
    for field in NAME_FIELDS:
        for entry in devices.values():
            if (entry.get(field) or "").lower() == token_lower:
                return entry
    raise ValueError(f"No device found matching '{token}'")
