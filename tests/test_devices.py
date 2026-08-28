"""Unit tests for mbdeploy.devices logic and CLI relay/target guards.

All tests run without connected hardware — hardware-touching functions
(flashable_probes, load_devices, probe_all) are monkeypatched or
exercised via tmp_path fixtures.
"""

from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import mbdeploy.devices as devices_mod
from mbdeploy.devices import is_relay, resolve_target
from mbdeploy.cli import _cmd_deploy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RELAY_UID = "9906" + "b" * 36          # 40 hex chars
_DEVICE_UID = "9906" + "c" * 36         # 40 hex chars
_DEVICE2_UID = "9906" + "d" * 36        # 40 hex chars

_RELAY_ENTRY = {
    "uid": _RELAY_UID,
    "enum": 1,
    "port": "/dev/cu.relay1",
    "role": "RADIOBRIDGE",
    "common_name": "bridge1",
    "device_name": "relay1",
}

_DEVICE_ENTRY = {
    "uid": _DEVICE_UID,
    "enum": 2,
    "port": "/dev/cu.device1",
    "role": "Nezha2",
    "common_name": "gutov",
    "device_name": "gutov-main",
}

_DEVICE2_ENTRY = {
    "uid": _DEVICE2_UID,
    "enum": 3,
    "port": "/dev/cu.device2",
    "role": "Nezha2",
    "common_name": "alpha",
    "device_name": "alpha-main",
}


def _make_args(
    target: str | None = None,
    build: bool = False,
    clean: bool = False,
    jobs: int | None = None,
    force_relay: bool = False,
    hex_path: str | None = None,
    target_mcu: str = "nrf52833",
    config: str | None = None,
) -> argparse.Namespace:
    """Build a minimal Namespace that _cmd_deploy accepts."""
    return argparse.Namespace(
        target=target,
        build=build,
        clean=clean,
        jobs=jobs,
        force_relay=force_relay,
        hex=hex_path,
        target_mcu=target_mcu,
        config=config,
    )


#: A locked-device failure signature (see flash.py::_LOCKED_SIGNATURES).
#: Ticket 003 gates flash.py's mass-erase branch on this text appearing
#: in the failed flash's output, so every fake below that expects the
#: recovery path to fire emits this line instead of failing silently.
_LOCKED_SIGNATURE_LINES = ("flash erase sector failure (0x67)",)


@pytest.fixture
def valid_hex_path(tmp_path) -> str:
    """A real, on-disk, valid Intel HEX file's path.

    ``flash_hex`` validates ``hex_path`` with ``intelhex`` before ever
    invoking pyocd (ticket 001), so any ``_cmd_deploy`` test that expects
    the (faked) pyocd subprocess to run needs a real file on disk -- the
    default ``_DEFAULT_HEX`` ("MICROBIT.hex") does not exist here.
    """
    path = tmp_path / "valid.hex"
    path.write_text(":00000001FF\n")
    return str(path)


class _FakePyocdProcess:
    """Stand-in for a ``subprocess.Popen`` instance representing pyocd.

    Ticket 010 switched ``flash.py::flash_hex`` from a single blocking
    ``subprocess.run()`` per pyocd invocation to a streaming
    ``subprocess.Popen()`` (``flash.py::_run_streamed``), so every test
    below that used to fake ``subprocess.run`` now fakes
    ``subprocess.Popen`` instead -- pyocd is still never actually
    invoked. ``_run_streamed`` only ever iterates ``.stdout`` for lines
    and calls ``.wait()`` for the exit code, so that's all this fake
    needs to provide.
    """

    def __init__(self, returncode: int, lines: tuple[str, ...] = ()):
        self.returncode = returncode
        self.stdout = iter(f"{line}\n" for line in lines)

    def wait(self) -> int:
        return self.returncode


# ---------------------------------------------------------------------------
# is_relay truth table
# ---------------------------------------------------------------------------

class TestIsRelay:
    def test_radiobridge_is_relay(self):
        assert is_relay("RADIOBRIDGE") is True

    def test_radiorelay_is_relay(self):
        assert is_relay("RADIORELAY") is True

    def test_nezha2_is_not_relay(self):
        assert is_relay("Nezha2") is False

    def test_none_is_not_relay(self):
        assert is_relay(None) is False

    def test_empty_string_is_not_relay(self):
        assert is_relay("") is False


# ---------------------------------------------------------------------------
# port_serial_map — direct tests against fake comports() results
#
# No existing reference to port_serial_map in this file exercises it
# directly; every one of the ~10 references monkeypatches it away. These are
# the first-ever direct tests, against fake port objects (no real hardware,
# no real pyserial internals) exposing exactly the four attributes the
# implementation reads.
# ---------------------------------------------------------------------------

_DAPLINK_VID = 0x0D28
_DAPLINK_PID = 0x0204


def _fake_port(device, vid, pid, serial_number):
    """Stand-in for a ``serial.tools.list_ports_common.ListPortInfo``."""
    return SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial_number)


class TestPortSerialMap:
    def _patch_comports(self, monkeypatch, ports):
        monkeypatch.setattr(
            devices_mod.serial.tools.list_ports, "comports", lambda: ports
        )

    def test_uid_to_port_mapping(self, monkeypatch):
        ports = [
            _fake_port("/dev/cu.usbmodem1", _DAPLINK_VID, _DAPLINK_PID, "uid-aaa"),
            _fake_port("/dev/cu.usbmodem2", _DAPLINK_VID, _DAPLINK_PID, "uid-bbb"),
        ]
        self._patch_comports(monkeypatch, ports)

        assert devices_mod.port_serial_map() == {
            "uid-aaa": "/dev/cu.usbmodem1",
            "uid-bbb": "/dev/cu.usbmodem2",
        }

    def test_known_filters_to_listed_uids(self, monkeypatch):
        ports = [
            _fake_port("/dev/cu.usbmodem1", _DAPLINK_VID, _DAPLINK_PID, "uid-aaa"),
            _fake_port("/dev/cu.usbmodem2", _DAPLINK_VID, _DAPLINK_PID, "uid-bbb"),
        ]
        self._patch_comports(monkeypatch, ports)

        assert devices_mod.port_serial_map(known={"uid-aaa"}) == {
            "uid-aaa": "/dev/cu.usbmodem1",
        }

    def test_known_none_returns_every_microbit_port(self, monkeypatch):
        ports = [
            _fake_port("/dev/cu.usbmodem1", _DAPLINK_VID, _DAPLINK_PID, "uid-aaa"),
            _fake_port("/dev/cu.usbmodem2", _DAPLINK_VID, _DAPLINK_PID, "uid-bbb"),
        ]
        self._patch_comports(monkeypatch, ports)

        assert devices_mod.port_serial_map(known=None) == {
            "uid-aaa": "/dev/cu.usbmodem1",
            "uid-bbb": "/dev/cu.usbmodem2",
        }

    def test_non_microbit_vid_pid_is_excluded_even_on_serial_collision(self, monkeypatch):
        # Same serial number a real UID might use, wrong VID:PID -- must
        # never be mistaken for a micro:bit even if `known` would admit it.
        ports = [_fake_port("/dev/cu.usbserial", 0x0403, 0x6001, "uid-aaa")]
        self._patch_comports(monkeypatch, ports)

        assert devices_mod.port_serial_map(known={"uid-aaa"}) == {}

    def test_serial_number_none_is_skipped_without_raising(self, monkeypatch):
        # Three ports like this exist on the dev Mac (Bluetooth-Incoming-Port,
        # debug-console, wlan-debug). A naive dict build maps None as a key.
        ports = [
            _fake_port("/dev/cu.Bluetooth-Incoming-Port", _DAPLINK_VID, _DAPLINK_PID, None),
            _fake_port("/dev/cu.usbmodem1", _DAPLINK_VID, _DAPLINK_PID, "uid-aaa"),
        ]
        self._patch_comports(monkeypatch, ports)

        assert devices_mod.port_serial_map() == {"uid-aaa": "/dev/cu.usbmodem1"}

    def test_empty_comports_yields_empty_dict(self, monkeypatch):
        self._patch_comports(monkeypatch, [])

        assert devices_mod.port_serial_map() == {}

    def test_no_matching_ports_yields_empty_dict(self, monkeypatch):
        ports = [_fake_port("/dev/cu.usbserial", 0x0403, 0x6001, "some-serial")]
        self._patch_comports(monkeypatch, ports)

        assert devices_mod.port_serial_map() == {}


# ---------------------------------------------------------------------------
# resolve_target precedence
# ---------------------------------------------------------------------------

class TestResolveTarget:
    """Exercises all four resolution paths without touching hardware."""

    def _registry(self) -> dict[str, dict]:
        return {
            _RELAY_UID: _RELAY_ENTRY.copy(),
            _DEVICE_UID: _DEVICE_ENTRY.copy(),
        }

    def test_resolve_by_enum(self):
        """Numeric token matches by enum field."""
        result = resolve_target("2", self._registry())
        assert result["uid"] == _DEVICE_UID

    def test_port_path_is_refused(self):
        """A port path is never matched against the registry's recorded port.

        The recorded port goes stale the moment macOS renumbers the USB
        modem devices, so a match here would hand the caller the board that
        *used* to be on that path.  Callers that take a path resolve it
        themselves against the live mapping.
        """
        with pytest.raises(ValueError, match="port path"):
            resolve_target("/dev/cu.relay1", self._registry())

    def test_resolve_by_uid(self):
        """40-hex-char token matches by uid field."""
        result = resolve_target(_DEVICE_UID, self._registry())
        assert result["uid"] == _DEVICE_UID

    def test_resolve_by_device_name(self):
        """The announced five-letter name is a target."""
        result = resolve_target("gutov-main", self._registry())
        assert result["uid"] == _DEVICE_UID

    def test_resolve_by_board_name(self):
        """The five-letter name read from silicon is a target too."""
        registry = self._registry()
        registry[_DEVICE2_UID] = {
            **_DEVICE2_ENTRY, "device_name": "", "board_name": "tovez"
        }
        result = resolve_target("TOVEZ", registry)
        assert result["uid"] == _DEVICE2_UID

    def test_name_match_is_case_insensitive(self):
        assert resolve_target("GUTOV-MAIN", self._registry())["uid"] == _DEVICE_UID

    def test_common_name_is_never_a_target(self):
        """A common_name is a human label for a board — "Jane's robot".

        Whoever set the fleet up assigned it; two boards can wear the same
        one and it changes when a class is reassigned, so it identifies a
        role in a classroom, not the hardware in your hand.  `list` shows it;
        nothing resolves it.
        """
        registry = self._registry()
        assert registry[_DEVICE_UID]["common_name"] == "gutov"
        with pytest.raises(ValueError, match="No device found"):
            resolve_target("gutov", registry)

    def test_a_shared_common_name_cannot_silently_pick_a_board(self):
        """Two boards, one label — exactly what must not resolve to either."""
        registry = self._registry()
        registry[_DEVICE2_UID] = {**_DEVICE2_ENTRY, "common_name": "gutov"}
        with pytest.raises(ValueError, match="No device found"):
            resolve_target("gutov", registry)

    def test_device_name_wins_over_board_name(self):
        """Both name the same board; the announced one is checked first."""
        registry = self._registry()
        registry[_DEVICE_UID]["board_name"] = "alpha"
        registry[_DEVICE2_UID] = {**_DEVICE2_ENTRY, "device_name": "alpha"}
        assert resolve_target("alpha", registry)["uid"] == _DEVICE2_UID

    def test_resolve_unknown_raises(self):
        with pytest.raises(ValueError, match="No device found"):
            resolve_target("nonexistent", self._registry())


# ---------------------------------------------------------------------------
# deploy — relay guard
# ---------------------------------------------------------------------------

class TestRelayGuard:
    """Tests relay refusal and --force-relay override."""

    def _registry_with_relay_only(self) -> dict[str, dict]:
        return {_RELAY_UID: _RELAY_ENTRY.copy()}

    def test_relay_refused_without_force(self, monkeypatch, tmp_path):
        """deploy refuses a relay target unless --force-relay is given."""
        config = tmp_path / "devices.json"
        registry = self._registry_with_relay_only()

        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _RELAY_UID, "description": "relay"}],
        )

        args = _make_args(target=_RELAY_UID, force_relay=False, config=str(config))
        rc = _cmd_deploy(args)
        assert rc != 0

    def test_force_relay_passes_guard(self, monkeypatch, tmp_path, valid_hex_path):
        """--force-relay allows the deploy to proceed past the relay check.

        The test patches flashable_probes to confirm connection and stubs
        subprocess.Popen to a trivial always-succeeds fake, since this test
        only exercises the guard logic, not the flash itself.
        """
        config = tmp_path / "devices.json"
        registry = self._registry_with_relay_only()

        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)
        # Relay IS in live probes — guard passes; pyocd step will follow
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _RELAY_UID, "description": "relay"}],
        )

        # Patch subprocess.Popen so pyocd flash/reset don't actually run
        import subprocess
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda cmd, **kw: _FakePyocdProcess(0),
        )

        args = _make_args(
            target=_RELAY_UID, force_relay=True, config=str(config),
            hex_path=valid_hex_path,
        )
        rc = _cmd_deploy(args)
        # Guard passed; result depends on mock subprocess — we accept 0 here
        assert rc == 0


# ---------------------------------------------------------------------------
# list / probe display names
# ---------------------------------------------------------------------------

class TestDisplayNames:
    def test_list_shows_device_name(self, monkeypatch, capsys, tmp_path):
        config = tmp_path / "devices.json"
        registry = {_DEVICE_UID: _DEVICE_ENTRY.copy()}

        monkeypatch.setattr(devices_mod, "flashable_probes", lambda: [{"uid": _DEVICE_UID, "description": "dev"}])
        monkeypatch.setattr(devices_mod, "port_serial_map", lambda known: {_DEVICE_UID: "/dev/cu.device1"})
        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)

        from mbdeploy.cli import _cmd_list

        rc = _cmd_list(argparse.Namespace(config=str(config)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "DEVICE NAME" in out
        assert "gutov-main" in out
        assert "gutov\n" not in out


class TestFriendlyName:
    """The five-letter name CODAL derives from FICR.DEVICEID[1]."""

    def test_known_board(self):
        """Ground truth from a real board: DEVICEID[1] 2314287040 announces 'tovez'."""
        assert devices_mod.friendly_name(2314287040) == "tovez"

    def test_more_known_boards(self):
        # Read over SWD from three micro:bit V2 boards.
        assert devices_mod.friendly_name(2175407711) == "gopiv"
        assert devices_mod.friendly_name(1784514240) == "getez"
        assert devices_mod.friendly_name(1198504156) == "vevov"

    def test_name_is_always_five_letters(self):
        for device_id in (0, 1, 0xFFFFFFFF, 123456789):
            name = devices_mod.friendly_name(device_id)
            assert len(name) == 5
            assert name.isalpha()

    def test_zero_is_the_lowest_name(self):
        assert devices_mod.friendly_name(0) == "zuzuz"

    def test_read_board_name_returns_none_when_id_unreadable(self, monkeypatch):
        monkeypatch.setattr(devices_mod, "read_device_id", lambda uid, mcu: None)
        assert devices_mod.read_board_name(_DEVICE_UID, "nrf52833") is None


class TestConnectedColumn:
    """list shows every known board and whether it is plugged in."""

    def _run_list(self, monkeypatch, capsys, registry, live):
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": uid, "description": ""} for uid in live],
        )
        monkeypatch.setattr(devices_mod, "port_serial_map", lambda known: {})
        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)

        from mbdeploy.cli import _cmd_list

        rc = _cmd_list(argparse.Namespace(config=None, fast=True, target_mcu="nrf52833"))
        assert rc == 0
        return capsys.readouterr().out

    def test_disconnected_registry_entry_is_listed(self, monkeypatch, capsys):
        """A registered board that is unplugged still appears, marked 'no'."""
        out = self._run_list(
            monkeypatch, capsys, {_DEVICE_UID: _DEVICE_ENTRY.copy()}, live=[]
        )
        assert "CONN" in out
        row = next(line for line in out.splitlines() if _DEVICE_UID in line)
        assert " no " in row
        assert "gutov-main" in row

    def test_connected_entry_is_marked_yes(self, monkeypatch, capsys):
        out = self._run_list(
            monkeypatch, capsys, {_DEVICE_UID: _DEVICE_ENTRY.copy()}, live=[_DEVICE_UID]
        )
        row = next(line for line in out.splitlines() if _DEVICE_UID in line)
        assert " yes " in row

    def test_stale_port_is_hidden_for_disconnected_board(self, monkeypatch, capsys):
        """The remembered port is meaningless once the board is unplugged."""
        out = self._run_list(
            monkeypatch, capsys, {_DEVICE_UID: _DEVICE_ENTRY.copy()}, live=[]
        )
        assert "/dev/cu.device1" not in out

    def test_connected_boards_sort_before_disconnected(self, monkeypatch, capsys):
        registry = {
            _DEVICE_UID: _DEVICE_ENTRY.copy(),      # enum 2, unplugged
            _DEVICE2_UID: _DEVICE2_ENTRY.copy(),    # enum 3, plugged in
        }
        out = self._run_list(monkeypatch, capsys, registry, live=[_DEVICE2_UID])
        assert out.index(_DEVICE2_UID) < out.index(_DEVICE_UID)

    def test_unregistered_board_gets_its_name_read_over_swd(self, monkeypatch, capsys):
        """A connected board that was never probed still shows a name."""
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _DEVICE2_UID, "description": ""}],
        )
        monkeypatch.setattr(devices_mod, "port_serial_map", lambda known: {})
        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: {})
        monkeypatch.setattr(devices_mod, "read_board_name", lambda uid, mcu: "tovez")

        from mbdeploy.cli import _cmd_list

        rc = _cmd_list(
            argparse.Namespace(config=None, fast=False, target_mcu="nrf52833")
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "tovez" in out
        assert _DEVICE2_UID in out

    def test_fast_skips_the_swd_read(self, monkeypatch, capsys):
        def _boom(uid, mcu):
            raise AssertionError("--fast must not touch the debug probe")

        monkeypatch.setattr(devices_mod, "read_board_name", _boom)
        out = self._run_list(monkeypatch, capsys, {}, live=[_DEVICE2_UID])
        assert _DEVICE2_UID in out

    def test_no_devices_at_all(self, monkeypatch, capsys):
        out = self._run_list(monkeypatch, capsys, {}, live=[])
        assert "no devices found" in out


class TestProbeRecordsBoardName:
    """probe caches the hardware name so later lists are free."""

    def test_board_name_recorded_without_announcement(self, monkeypatch, tmp_path):
        config = tmp_path / "devices.json"

        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _DEVICE_UID, "description": "dev"}],
        )
        monkeypatch.setattr(devices_mod, "port_serial_map", lambda known: {})
        monkeypatch.setattr(devices_mod, "probe_type", lambda port: None)
        monkeypatch.setattr(devices_mod, "read_device_id", lambda uid, mcu: 2314287040)

        entries = devices_mod.probe_all(config)

        assert entries[0]["board_name"] == "tovez"
        assert entries[0]["device_id"] == 2314287040
        saved = devices_mod.load_devices(config)
        assert saved[_DEVICE_UID]["board_name"] == "tovez"

    def test_known_board_name_is_not_re_read(self, monkeypatch, tmp_path):
        """The name never changes for a UID, so a second probe skips the SWD read."""
        config = tmp_path / "devices.json"
        config.write_text(json.dumps({
            _DEVICE_UID: {"uid": _DEVICE_UID, "enum": 1, "board_name": "tovez"}
        }))

        def _boom(uid, mcu):
            raise AssertionError("cached board_name must not be re-read")

        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _DEVICE_UID, "description": "dev"}],
        )
        monkeypatch.setattr(devices_mod, "port_serial_map", lambda known: {})
        monkeypatch.setattr(devices_mod, "probe_type", lambda port: None)
        monkeypatch.setattr(devices_mod, "read_device_id", _boom)

        entries = devices_mod.probe_all(config)
        assert entries[0]["board_name"] == "tovez"

    def test_unreadable_device_id_leaves_entry_intact(self, monkeypatch, tmp_path):
        config = tmp_path / "devices.json"

        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _DEVICE_UID, "description": "dev"}],
        )
        monkeypatch.setattr(devices_mod, "port_serial_map", lambda known: {})
        monkeypatch.setattr(devices_mod, "probe_type", lambda port: None)
        monkeypatch.setattr(devices_mod, "read_device_id", lambda uid, mcu: None)

        entries = devices_mod.probe_all(config)
        assert "board_name" not in entries[0]
        assert entries[0]["enum"] == 1


class TestProbeClear:
    def test_probe_clear_rebuilds_registry_from_live_devices(self, monkeypatch, tmp_path):
        config = tmp_path / "devices.json"
        config.write_text(json.dumps({_RELAY_UID: {"uid": _RELAY_UID, "enum": 1, "common_name": "stale"}}))

        monkeypatch.setattr(devices_mod, "flashable_probes", lambda: [{"uid": _DEVICE_UID, "description": "dev"}])
        monkeypatch.setattr(devices_mod, "port_serial_map", lambda known: {_DEVICE_UID: "/dev/cu.device1"})
        monkeypatch.setattr(
            devices_mod,
            "probe_type",
            lambda port: {
                "role": "Nezha2",
                "common_name": "gutov",
                "device_name": "gutov-main",
                "serial": "SERIAL",
                "raw": "DEVICE:Nezha2:gutov:gutov-main:SERIAL",
            },
        )

        entries = devices_mod.probe_all(config, clear=True)

        assert [entry["uid"] for entry in entries] == [_DEVICE_UID]
        saved = devices_mod.load_devices(config)
        assert set(saved) == {_DEVICE_UID}
        assert saved[_DEVICE_UID]["device_name"] == "gutov-main"

    def test_probe_shows_device_name(self, monkeypatch, capsys, tmp_path):
        config = tmp_path / "devices.json"
        registry = {_DEVICE_UID: _DEVICE_ENTRY.copy()}

        monkeypatch.setattr(
            devices_mod,
            "probe_all",
            lambda _path, clear=False, target_mcu=None: [registry[_DEVICE_UID]],
        )
        monkeypatch.setattr(devices_mod, "connected_uids", lambda: {_DEVICE_UID})

        from mbdeploy.cli import _cmd_probe

        rc = _cmd_probe(argparse.Namespace(config=str(config)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "DEVICE NAME" in out
        assert "gutov-main" in out


# ---------------------------------------------------------------------------
# probe_type — announcement dialect parsing (ticket 001/003)
#
# Commit 2e19088 taught probe_type a second, space-delimited announcement
# dialect alongside the original colon form, and shipped with zero tests
# (suite was 92 before and 92 after). Every prior reference to probe_type
# in this file monkeypatches it away, so neither dialect had ever actually
# been exercised. These tests drive the real function against a fake
# serial.Serial instead.
# ---------------------------------------------------------------------------

class _ScriptedProbePort:
    """Minimal serial.Serial stand-in for exactly the calls probe_type makes.

    test_connect.py's FakeSerial is built for the interactive `connect` read
    loop and doesn't implement `.open()` or pre-exist the modem-control
    attributes (`.port`, `.dtr`, `.rts`) that probe_type sets *before*
    opening -- so this is a second, smaller fake local to this file rather
    than a fork of that one.
    """

    def __init__(self, lines: tuple[bytes, ...] = ()):
        self._lines = list(lines)
        self.is_open = False
        self.port = None
        self.dtr = None
        self.rts = None
        self.written = b""
        self.reset_count = 0

    def open(self):
        self.is_open = True

    def reset_input_buffer(self):
        self.reset_count += 1

    def write(self, data):
        self.written += data
        return len(data)

    def flush(self):
        pass

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""

    def close(self):
        self.is_open = False


def _patch_serial(monkeypatch, *lines: bytes) -> _ScriptedProbePort:
    """Monkeypatch devices_mod.serial.Serial to hand back a scripted port."""
    port = _ScriptedProbePort(lines)
    monkeypatch.setattr(devices_mod.serial, "Serial", lambda **kwargs: port)
    return port


class TestProbeType:
    """probe_type against a fake serial port, both announcement dialects."""

    @pytest.fixture(autouse=True)
    def _no_powerup_delay(self, monkeypatch):
        # probe_type sleeps 0.3s after open() to let real hardware settle
        # before reading. That's not what these tests exercise, so skip the
        # wall-clock wait -- time.time() (and therefore the read deadline)
        # is left untouched.
        monkeypatch.setattr(devices_mod.time, "sleep", lambda _s: None)

    def test_colon_dialect_parses_five_fields(self, monkeypatch):
        _patch_serial(monkeypatch, b"DEVICE:RADIOBRIDGE:relay:getez:1779042496\n")
        result = devices_mod.probe_type("/dev/fake")
        assert result == {
            "role": "RADIOBRIDGE",
            "common_name": "relay",
            "device_name": "getez",
            "serial": "1779042496",
            "raw": "DEVICE:RADIOBRIDGE:relay:getez:1779042496",
        }

    def test_space_dialect_parses_same_five_fields(self, monkeypatch):
        _patch_serial(monkeypatch, b"device NEZHA2 robot vevov 1198504156\n")
        result = devices_mod.probe_type("/dev/fake")
        assert result == {
            "role": "NEZHA2",
            "common_name": "robot",
            "device_name": "vevov",
            "serial": "1198504156",
            "raw": "device NEZHA2 robot vevov 1198504156",
        }

    def test_colon_dialect_rejoins_serial_containing_colons(self, monkeypatch):
        # Serial itself contains ':' -- 8 colon-delimited parts total, so
        # the parser must rejoin everything past the 4th on ':'.
        line = b"DEVICE:RADIOBRIDGE:relay:getez:17:79:04:2496\n"
        _patch_serial(monkeypatch, line)
        result = devices_mod.probe_type("/dev/fake")
        assert result == {
            "role": "RADIOBRIDGE",
            "common_name": "relay",
            "device_name": "getez",
            "serial": "17:79:04:2496",
            "raw": "DEVICE:RADIOBRIDGE:relay:getez:17:79:04:2496",
        }

    def test_space_dialect_ignores_extra_trailing_tokens(self, monkeypatch):
        line = b"device NEZHA2 robot vevov 1198504156 extra_token\n"
        _patch_serial(monkeypatch, line)
        result = devices_mod.probe_type("/dev/fake")
        assert result == {
            "role": "NEZHA2",
            "common_name": "robot",
            "device_name": "vevov",
            "serial": "1198504156",
            "raw": "device NEZHA2 robot vevov 1198504156 extra_token",
        }

    @pytest.mark.parametrize(
        "line",
        [
            b"DEVICE:RADIOBRIDGE:relay:getez\n",   # colon: 4 fields, no serial
            b"DEVICE:RADIOBRIDGE\n",                # colon: 2 fields
            b"device NEZHA2 robot vevov\n",          # space: 4 tokens, no serial
            b"device NEZHA2\n",                      # space: 2 tokens
        ],
    )
    def test_incomplete_banner_returns_none(self, monkeypatch, line):
        _patch_serial(monkeypatch, line)
        assert devices_mod.probe_type("/dev/fake", timeout_s=0.05) is None

    @pytest.mark.parametrize(
        "line",
        [
            b"ver 1.2.3\n",   # a ver reply, not an announcement
            b"status OK\n",   # a status reply, not an announcement
            b"\n",            # an empty line
        ],
    )
    def test_non_announcement_reply_returns_none(self, monkeypatch, line):
        _patch_serial(monkeypatch, line)
        assert devices_mod.probe_type("/dev/fake", timeout_s=0.05) is None

    def test_silent_port_returns_none(self, monkeypatch):
        """No reply at all (port busy / no firmware / timed out)."""
        _patch_serial(monkeypatch)  # readline() always yields b""
        assert devices_mod.probe_type("/dev/fake", timeout_s=0.05) is None


# ---------------------------------------------------------------------------
# probe_all -- preserve prior announcement fields when probe_type -> None
# ---------------------------------------------------------------------------

class TestProbeAllPreservesAnnouncementOnNone:
    """probe_all must not clobber role/common_name/device_name/serial/
    announcement when probe_type can't produce a fresh reading.

    This is the exact gap that let a robot announcement silently fail to
    update `role`: before the space dialect was accepted, a robot's HELLO
    reply always failed to parse, probe_type always returned None, and
    probe_all always took this preserve-existing-fields branch -- so a
    board reflashed from relay to robot firmware kept showing its old
    RADIOBRIDGE role indefinitely (see the incident note in probe_type's
    docstring comment block, devices.py:160-169).
    """

    _STALE_ENTRY = {
        "uid": _DEVICE_UID,
        "enum": 2,
        "port": "/dev/cu.device1",
        "role": "RADIOBRIDGE",
        "common_name": "relay1",
        "device_name": "vevov",
        "serial": "1779042496",
        "announcement": "DEVICE:RADIOBRIDGE:relay1:vevov:1779042496",
    }

    def _run(self, monkeypatch, tmp_path):
        config = tmp_path / "devices.json"
        config.write_text(json.dumps({_DEVICE_UID: self._STALE_ENTRY.copy()}))

        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _DEVICE_UID, "description": "dev"}],
        )
        monkeypatch.setattr(
            devices_mod, "port_serial_map",
            lambda known: {_DEVICE_UID: "/dev/cu.device1"},
        )
        monkeypatch.setattr(devices_mod, "read_device_id", lambda uid, mcu: None)

        entries = devices_mod.probe_all(config)
        return entries[0]

    def _assert_preserved(self, entry):
        assert entry["role"] == "RADIOBRIDGE"
        assert entry["common_name"] == "relay1"
        assert entry["device_name"] == "vevov"
        assert entry["serial"] == "1779042496"
        assert entry["announcement"] == "DEVICE:RADIOBRIDGE:relay1:vevov:1779042496"

    def test_preserved_when_port_gives_no_reply_at_all(self, monkeypatch, tmp_path):
        """Silent port (timed out / no firmware) -- probe_type returns None."""
        monkeypatch.setattr(devices_mod, "probe_type", lambda port: None)
        self._assert_preserved(self._run(monkeypatch, tmp_path))

    def test_preserved_when_reply_does_not_parse(self, monkeypatch, tmp_path):
        """A reply arrived but wasn't a DEVICE:/device announcement (e.g. a
        'ver' or 'status' line, or a truncated banner) -- probe_type also
        returns None here, indistinguishable from silence at this seam.
        """
        monkeypatch.setattr(devices_mod, "probe_type", lambda port: None)
        self._assert_preserved(self._run(monkeypatch, tmp_path))


# ---------------------------------------------------------------------------
# probe_all -- only_uids scoping (Ticket 002 / Design Problem 2, sprint 002)
# ---------------------------------------------------------------------------

class _RecordingProbePort:
    """Fake serial.Serial that records which port was opened/written to.

    Proves probe_all's ``only_uids`` scoping never opens a serial port, nor
    writes HELLO, for an excluded UID. ``readline`` raises immediately after
    ``write`` so the real ``probe_type`` returns via its outer
    ``except Exception: return None`` instead of busy-spinning for its full
    1.6s read-timeout window (there is no ``sleep`` in that loop to patch
    away).
    """

    def __init__(self):
        self.port = None
        self.is_open = False
        self.opened_ports: list[str] = []
        self.written_ports: list[str] = []

    def open(self):
        self.is_open = True
        self.opened_ports.append(self.port)

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.written_ports.append(self.port)
        return len(data)

    def flush(self):
        pass

    def readline(self):
        raise RuntimeError("probe_type stops here in this fake")

    def close(self):
        self.is_open = False


class TestProbeAllOnlyUids:
    """``only_uids`` narrows probe_all's expensive per-board work (port
    refresh's HELLO write, SWD board-name read) to exactly the named UIDs,
    so a hotplug watcher can refresh one arriving board without disturbing
    every other board already connected.
    """

    _UID_A = _DEVICE_UID
    _UID_B = _DEVICE2_UID
    _PORT_A = "/dev/cu.device1"
    _PORT_B = "/dev/cu.device2"

    def _both_connected(self, monkeypatch):
        monkeypatch.setattr(devices_mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [
                {"uid": self._UID_A, "description": "a"},
                {"uid": self._UID_B, "description": "b"},
            ],
        )

    def test_only_uids_none_probes_everything(self, monkeypatch, tmp_path):
        """Default (None) is today's unscoped behavior: both boards probed."""
        self._both_connected(monkeypatch)
        seen_known = []

        def _ports(known):
            seen_known.append(set(known))
            return {self._UID_A: self._PORT_A, self._UID_B: self._PORT_B}

        monkeypatch.setattr(devices_mod, "port_serial_map", _ports)
        probed_ports = []
        monkeypatch.setattr(
            devices_mod, "probe_type",
            lambda port: probed_ports.append(port) or None,
        )
        monkeypatch.setattr(devices_mod, "read_device_id", lambda uid, mcu: None)

        config = tmp_path / "devices.json"
        entries = devices_mod.probe_all(config)

        assert seen_known == [{self._UID_A, self._UID_B}]
        assert set(probed_ports) == {self._PORT_A, self._PORT_B}
        assert {e["uid"] for e in entries} == {self._UID_A, self._UID_B}

    def test_only_uids_narrows_to_named_board(self, monkeypatch, tmp_path):
        """only_uids={uid_a} touches uid_a only: uid_b is never passed to
        port_serial_map, probe_type (HELLO), or read_device_id, and its
        existing registry entry is left byte-for-byte unchanged.
        """
        self._both_connected(monkeypatch)
        config = tmp_path / "devices.json"
        prior_b = {
            "uid": self._UID_B, "enum": 2, "port": "/dev/stale-b",
            "role": "Nezha2", "common_name": "gutov", "device_name": "gutov-main",
        }
        config.write_text(json.dumps({self._UID_B: prior_b}))

        seen_known = []

        def _ports(known):
            seen_known.append(set(known))
            return {self._UID_A: self._PORT_A, self._UID_B: self._PORT_B}

        monkeypatch.setattr(devices_mod, "port_serial_map", _ports)

        recorder = _RecordingProbePort()
        monkeypatch.setattr(devices_mod.serial, "Serial", lambda **kwargs: recorder)

        read_device_id_calls = []
        monkeypatch.setattr(
            devices_mod, "read_device_id",
            lambda uid, mcu: read_device_id_calls.append(uid) or None,
        )

        entries = devices_mod.probe_all(config, only_uids={self._UID_A})

        # uid_b was never even named to port_serial_map.
        assert seen_known == [{self._UID_A}]

        # The real probe_type ran only for uid_a's port -- HELLO never
        # reached uid_b's port because probe_type was never invoked for it.
        assert recorder.opened_ports == [self._PORT_A]
        assert recorder.written_ports == [self._PORT_A]

        # The SWD board-name read also only ever named uid_a.
        assert read_device_id_calls == [self._UID_A]

        by_uid = {e["uid"]: e for e in entries}
        assert by_uid[self._UID_A]["port"] == self._PORT_A
        assert by_uid[self._UID_B] == prior_b  # untouched, byte-for-byte

        saved = devices_mod.load_devices(config)
        assert saved[self._UID_B] == prior_b

    def test_only_uids_empty_set_probes_nothing(self, monkeypatch, tmp_path):
        """An empty set narrows to nothing -- a no-op refresh, distinct from
        None (which means "don't narrow"). Every entry is preserved as-is.
        """
        self._both_connected(monkeypatch)
        config = tmp_path / "devices.json"
        prior = {
            self._UID_A: {"uid": self._UID_A, "enum": 1, "port": "/dev/stale-a"},
            self._UID_B: {"uid": self._UID_B, "enum": 2, "port": "/dev/stale-b"},
        }
        config.write_text(json.dumps(prior))

        seen_known = []
        monkeypatch.setattr(
            devices_mod, "port_serial_map",
            lambda known: seen_known.append(set(known)) or {},
        )

        def _boom_probe(port):
            raise AssertionError("probe_type must not run when only_uids is empty")

        def _boom_read(uid, mcu):
            raise AssertionError("read_device_id must not run when only_uids is empty")

        monkeypatch.setattr(devices_mod, "probe_type", _boom_probe)
        monkeypatch.setattr(devices_mod, "read_device_id", _boom_read)

        entries = devices_mod.probe_all(config, only_uids=set())

        assert seen_known == [set()]
        # No board was probed, but existing registry entries are still
        # loaded and returned untouched -- this is a no-op refresh, not a
        # wipe (that guard is `clear`, tested separately).
        assert {e["uid"] for e in entries} == {self._UID_A, self._UID_B}
        saved = devices_mod.load_devices(config)
        assert saved == prior

    def test_only_uids_with_disconnected_uid_is_ignored(self, monkeypatch, tmp_path):
        """A UID named in only_uids that isn't currently connected has
        nothing to filter down to -- it's simply absent from the result,
        with no error and no effect on the UIDs that are connected.
        """
        self._both_connected(monkeypatch)
        config = tmp_path / "devices.json"

        seen_known = []

        def _ports(known):
            seen_known.append(set(known))
            return {self._UID_A: self._PORT_A}

        monkeypatch.setattr(devices_mod, "port_serial_map", _ports)
        monkeypatch.setattr(devices_mod, "probe_type", lambda port: None)
        monkeypatch.setattr(devices_mod, "read_device_id", lambda uid, mcu: None)

        not_connected_uid = "9906" + "e" * 36
        entries = devices_mod.probe_all(
            config, only_uids={self._UID_A, not_connected_uid}
        )

        assert seen_known == [{self._UID_A}]
        assert {e["uid"] for e in entries} == {self._UID_A}

    def test_only_uids_with_clear_raises_before_touching_registry(
        self, monkeypatch, tmp_path
    ):
        """clear wipes the registry down to what this call sees, which
        would silently erase every non-only_uids board's entry -- so the
        combination is rejected outright, before the registry file (or any
        hardware) is touched at all.
        """
        config = tmp_path / "devices.json"
        config.write_text(json.dumps({self._UID_A: {"uid": self._UID_A, "enum": 1}}))
        original_text = config.read_text()

        def _boom(*args, **kwargs):
            raise AssertionError(
                "nothing should run once only_uids+clear=True is rejected"
            )

        monkeypatch.setattr(devices_mod, "load_devices", _boom)
        monkeypatch.setattr(devices_mod, "flashable_probes", _boom)
        monkeypatch.setattr(devices_mod, "port_serial_map", _boom)
        monkeypatch.setattr(devices_mod, "probe_type", _boom)
        monkeypatch.setattr(devices_mod, "read_device_id", _boom)
        monkeypatch.setattr(devices_mod, "save_devices", _boom)

        with pytest.raises(ValueError):
            devices_mod.probe_all(config, clear=True, only_uids={self._UID_A})

        assert config.read_text() == original_text


# ---------------------------------------------------------------------------
# deploy — auto-pick
# ---------------------------------------------------------------------------

class TestAutoPick:
    """Tests the 'no target' auto-pick logic."""

    def test_unique_non_relay_is_auto_picked(self, monkeypatch, tmp_path, valid_hex_path):
        """When exactly one non-relay device exists, it is auto-picked."""
        config = tmp_path / "devices.json"
        registry = {
            _RELAY_UID: _RELAY_ENTRY.copy(),
            _DEVICE_UID: _DEVICE_ENTRY.copy(),
        }

        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)
        # Device IS connected
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _DEVICE_UID, "description": "dev"}],
        )

        import subprocess
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda cmd, **kw: _FakePyocdProcess(0),
        )

        args = _make_args(target=None, config=str(config), hex_path=valid_hex_path)
        rc = _cmd_deploy(args)
        assert rc == 0

    def test_ambiguous_auto_pick_errors(self, monkeypatch, tmp_path, capsys):
        """When two non-relay devices are in registry, auto-pick errors."""
        config = tmp_path / "devices.json"
        registry = {
            _DEVICE_UID: _DEVICE_ENTRY.copy(),
            _DEVICE2_UID: _DEVICE2_ENTRY.copy(),
        }

        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [
                {"uid": _DEVICE_UID, "description": "dev"},
                {"uid": _DEVICE2_UID, "description": "dev2"},
            ],
        )

        args = _make_args(target=None, config=str(config))
        rc = _cmd_deploy(args)
        assert rc != 0
        captured = capsys.readouterr()
        assert "ambiguous" in captured.err.lower()


# ---------------------------------------------------------------------------
# deploy — a port path names the board that is on it *now*
# ---------------------------------------------------------------------------

class TestDeployPortTarget:
    """``deploy /dev/...`` must follow the live ioreg map, not the registry.

    The registry's ``port`` is only as fresh as the last ``probe``, and macOS
    hands out ``/dev/cu.usbmodem*`` names anew on every reconnect, so two
    boards routinely swap paths.  Trusting the recorded port flashes a
    different, connected board than the path names.
    """

    def _setup(self, monkeypatch, registry, live_ports):
        """Wire up a fake fleet and capture every pyocd command deploy runs."""
        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": uid, "description": "dev"} for uid in live_ports],
        )
        monkeypatch.setattr(
            devices_mod, "port_serial_map", lambda known=None: dict(live_ports)
        )

        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _FakePyocdProcess(0)

        import subprocess
        monkeypatch.setattr(subprocess, "Popen", fake_run)
        return calls

    @staticmethod
    def _flashed_uid(calls: list[list[str]]) -> str | None:
        for cmd in calls:
            if "flash" in cmd and "--uid" in cmd:
                return cmd[cmd.index("--uid") + 1]
        return None

    def test_stale_registry_port_does_not_pick_the_board(
        self, monkeypatch, tmp_path, valid_hex_path
    ):
        """The core regression guard: the ports in the registry are swapped.

        The registry still records device1 on ``/dev/cu.device1``, but the two
        boards have since traded paths.  Deploying to ``/dev/cu.device1`` must
        flash device2 — the board actually on it — not the stale match.
        """
        registry = {
            _DEVICE_UID: _DEVICE_ENTRY.copy(),      # port: /dev/cu.device1
            _DEVICE2_UID: _DEVICE2_ENTRY.copy(),    # port: /dev/cu.device2
        }
        live_ports = {                              # ...but they have swapped
            _DEVICE_UID: "/dev/cu.device2",
            _DEVICE2_UID: "/dev/cu.device1",
        }
        calls = self._setup(monkeypatch, registry, live_ports)

        args = _make_args(
            target="/dev/cu.device1", config=str(tmp_path / "devices.json"),
            hex_path=valid_hex_path,
        )
        rc = _cmd_deploy(args)

        assert rc == 0
        assert self._flashed_uid(calls) == _DEVICE2_UID

    def test_relay_guard_reads_the_live_board_role(
        self, monkeypatch, tmp_path, capsys
    ):
        """The role guarding the flash is the live board's, not the stale one's.

        The registry puts the ordinary board on ``/dev/cu.device1``; the relay
        is there now.  The relay guard must fire.
        """
        registry = {
            _DEVICE_UID: _DEVICE_ENTRY.copy(),      # port: /dev/cu.device1
            _RELAY_UID: _RELAY_ENTRY.copy(),        # port: /dev/cu.relay1
        }
        live_ports = {
            _DEVICE_UID: "/dev/cu.relay1",
            _RELAY_UID: "/dev/cu.device1",
        }
        calls = self._setup(monkeypatch, registry, live_ports)

        args = _make_args(
            target="/dev/cu.device1", config=str(tmp_path / "devices.json")
        )
        rc = _cmd_deploy(args)

        assert rc != 0
        assert "relay" in capsys.readouterr().err.lower()
        assert calls == []                          # nothing was flashed

    def test_unregistered_live_uid_is_refused(self, monkeypatch, tmp_path, capsys):
        """A board on the path but absent from the registry has no known role.

        Flashing it would mean flashing with no relay guard at all, so deploy
        refuses and sends the user to ``probe``.
        """
        registry = {_DEVICE_UID: _DEVICE_ENTRY.copy()}
        live_ports = {_DEVICE2_UID: "/dev/cu.device1"}
        calls = self._setup(monkeypatch, registry, live_ports)

        args = _make_args(
            target="/dev/cu.device1", config=str(tmp_path / "devices.json")
        )
        rc = _cmd_deploy(args)

        assert rc != 0
        err = capsys.readouterr().err
        assert "not in the registry" in err
        assert "probe" in err
        assert calls == []

    def test_path_with_no_microbit_on_it_is_refused(
        self, monkeypatch, tmp_path, capsys
    ):
        """A path that no connected micro:bit occupies never falls back."""
        registry = {_DEVICE_UID: _DEVICE_ENTRY.copy()}
        live_ports = {_DEVICE_UID: "/dev/cu.device9"}
        calls = self._setup(monkeypatch, registry, live_ports)

        args = _make_args(
            target="/dev/cu.device1", config=str(tmp_path / "devices.json")
        )
        rc = _cmd_deploy(args)

        assert rc != 0
        err = capsys.readouterr().err
        assert "/dev/cu.device1" in err
        assert calls == []

    def test_no_live_map_is_refused_not_guessed(self, monkeypatch, tmp_path, capsys):
        """Off macOS the mapping is empty; deploy errors instead of guessing.

        The registry would happily match the recorded port here — that is the
        pre-existing wrong-board behaviour, so it must not be a fallback.
        """
        registry = {_DEVICE_UID: _DEVICE_ENTRY.copy()}
        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _DEVICE_UID, "description": "dev"}],
        )
        monkeypatch.setattr(devices_mod, "port_serial_map", lambda known=None: {})

        calls: list[list[str]] = []
        import subprocess
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda cmd, **kw: (calls.append(cmd),
                               _FakePyocdProcess(0))[1],
        )

        args = _make_args(
            target="/dev/cu.device1", config=str(tmp_path / "devices.json")
        )
        rc = _cmd_deploy(args)

        assert rc != 0
        err = capsys.readouterr().err
        assert "no micro:bit serial port was found" in err
        assert "plugdev" in err and "dialout" in err
        assert calls == []

    def test_no_boards_connected_at_all(self, monkeypatch, tmp_path, capsys):
        """With nothing plugged in, the path error says so plainly."""
        registry = {_DEVICE_UID: _DEVICE_ENTRY.copy()}
        calls = self._setup(monkeypatch, registry, {})

        args = _make_args(
            target="/dev/cu.device1", config=str(tmp_path / "devices.json")
        )
        rc = _cmd_deploy(args)

        assert rc != 0
        assert "no micro:bit is connected" in capsys.readouterr().err.lower()
        assert calls == []

    # --- the other resolution paths are untouched by the port fix ---

    def _stale_fleet(self, monkeypatch):
        """Registry ports deliberately disagree with the live mapping."""
        registry = {
            _DEVICE_UID: _DEVICE_ENTRY.copy(),
            _DEVICE2_UID: _DEVICE2_ENTRY.copy(),
        }
        live_ports = {
            _DEVICE_UID: "/dev/cu.device2",
            _DEVICE2_UID: "/dev/cu.device1",
        }
        return registry, self._setup(monkeypatch, registry, live_ports)

    def test_enum_resolution_is_unchanged(self, monkeypatch, tmp_path, valid_hex_path):
        _registry, calls = self._stale_fleet(monkeypatch)
        rc = _cmd_deploy(
            _make_args(
                target="2", config=str(tmp_path / "devices.json"),
                hex_path=valid_hex_path,
            )
        )
        assert rc == 0
        assert self._flashed_uid(calls) == _DEVICE_UID

    def test_uid_resolution_is_unchanged(self, monkeypatch, tmp_path, valid_hex_path):
        _registry, calls = self._stale_fleet(monkeypatch)
        rc = _cmd_deploy(
            _make_args(
                target=_DEVICE2_UID, config=str(tmp_path / "devices.json"),
                hex_path=valid_hex_path,
            )
        )
        assert rc == 0
        assert self._flashed_uid(calls) == _DEVICE2_UID

    def test_name_resolution_is_unchanged(self, monkeypatch, tmp_path, valid_hex_path):
        _registry, calls = self._stale_fleet(monkeypatch)
        rc = _cmd_deploy(
            _make_args(
                target="alpha-main", config=str(tmp_path / "devices.json"),
                hex_path=valid_hex_path,
            )
        )
        assert rc == 0
        assert self._flashed_uid(calls) == _DEVICE2_UID


# ---------------------------------------------------------------------------
# deploy — device not connected
# ---------------------------------------------------------------------------

class TestDeviceNotConnected:
    """Registry has UID but flashable_probes returns nothing."""

    def test_device_not_connected_exits_nonzero(self, monkeypatch, tmp_path, capsys):
        config = tmp_path / "devices.json"
        registry = {_DEVICE_UID: _DEVICE_ENTRY.copy()}

        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)
        # Device is NOT in live probes
        monkeypatch.setattr(devices_mod, "flashable_probes", lambda: [])

        args = _make_args(target=_DEVICE_UID, config=str(config))
        rc = _cmd_deploy(args)
        assert rc != 0
        captured = capsys.readouterr()
        assert "device not connected" in captured.err.lower()
        assert _DEVICE_UID in captured.err


# ---------------------------------------------------------------------------
# deploy — mass-erase recovery for locked devices
# ---------------------------------------------------------------------------

class TestMassEraseRecovery:
    """A locked nRF makes the first flash fail; deploy must mass-erase and retry."""

    def _connect_one_device(self, monkeypatch, tmp_path):
        config = tmp_path / "devices.json"
        registry = {_DEVICE_UID: _DEVICE_ENTRY.copy()}
        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _DEVICE_UID, "description": "dev"}],
        )
        return config

    def test_flash_retries_after_mass_erase(self, monkeypatch, tmp_path, valid_hex_path):
        """First flash fails, mass erase succeeds, second flash + reset succeed."""
        config = self._connect_one_device(monkeypatch, tmp_path)

        calls: list[list[str]] = []
        state = {"flash": 0}

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "flash" in cmd:
                state["flash"] += 1
                if state["flash"] == 1:                 # first flash fails
                    return _FakePyocdProcess(1, _LOCKED_SIGNATURE_LINES)
                return _FakePyocdProcess(0)
            return _FakePyocdProcess(0)                  # erase / reset succeed

        import subprocess
        monkeypatch.setattr(subprocess, "Popen", fake_run)

        args = _make_args(target=_DEVICE_UID, config=str(config), hex_path=valid_hex_path)
        rc = _cmd_deploy(args)

        assert rc == 0
        assert state["flash"] == 2                      # flashed twice
        assert any("erase" in c and "--mass" in c for c in calls)

    def test_mass_erase_failure_aborts_without_retry(self, monkeypatch, tmp_path, capsys, valid_hex_path):
        """If the mass erase itself fails, deploy aborts and does not re-flash."""
        config = self._connect_one_device(monkeypatch, tmp_path)

        state = {"flash": 0}

        def fake_run(cmd, **kw):
            if "flash" in cmd:
                state["flash"] += 1
                return _FakePyocdProcess(1, _LOCKED_SIGNATURE_LINES)
            elif "erase" in cmd:
                return _FakePyocdProcess(5)
            return _FakePyocdProcess(0)

        import subprocess
        monkeypatch.setattr(subprocess, "Popen", fake_run)

        args = _make_args(target=_DEVICE_UID, config=str(config), hex_path=valid_hex_path)
        rc = _cmd_deploy(args)

        assert rc == 5
        assert state["flash"] == 1                      # no retry after erase failure
        assert "mass erase failed" in capsys.readouterr().err.lower()

    def test_successful_flash_skips_mass_erase(self, monkeypatch, tmp_path, valid_hex_path):
        """The normal path never mass-erases when the first flash succeeds."""
        config = self._connect_one_device(monkeypatch, tmp_path)

        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _FakePyocdProcess(0)

        import subprocess
        monkeypatch.setattr(subprocess, "Popen", fake_run)

        args = _make_args(target=_DEVICE_UID, config=str(config), hex_path=valid_hex_path)
        rc = _cmd_deploy(args)

        assert rc == 0
        assert not any("erase" in c for c in calls)
