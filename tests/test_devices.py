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

    def test_force_relay_passes_guard(self, monkeypatch, tmp_path):
        """--force-relay allows the deploy to proceed past the relay check.

        The test patches flashable_probes to confirm connection but does NOT
        patch subprocess.run (pyocd will fail or not be found), which is
        acceptable — we only test the guard logic, not the flash itself.
        """
        config = tmp_path / "devices.json"
        registry = self._registry_with_relay_only()

        monkeypatch.setattr(devices_mod, "load_devices", lambda _path: registry)
        # Relay IS in live probes — guard passes; pyocd step will follow
        monkeypatch.setattr(
            devices_mod, "flashable_probes",
            lambda: [{"uid": _RELAY_UID, "description": "relay"}],
        )

        # Patch subprocess.run so pyocd flash/reset don't actually run
        import subprocess
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: type("R", (), {"returncode": 0})(),
        )

        args = _make_args(target=_RELAY_UID, force_relay=True, config=str(config))
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
# deploy — auto-pick
# ---------------------------------------------------------------------------

class TestAutoPick:
    """Tests the 'no target' auto-pick logic."""

    def test_unique_non_relay_is_auto_picked(self, monkeypatch, tmp_path):
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
            subprocess, "run",
            lambda cmd, **kw: type("R", (), {"returncode": 0})(),
        )

        args = _make_args(target=None, config=str(config))
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
            return type("R", (), {"returncode": 0})()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)
        return calls

    @staticmethod
    def _flashed_uid(calls: list[list[str]]) -> str | None:
        for cmd in calls:
            if "flash" in cmd and "--uid" in cmd:
                return cmd[cmd.index("--uid") + 1]
        return None

    def test_stale_registry_port_does_not_pick_the_board(
        self, monkeypatch, tmp_path
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
            target="/dev/cu.device1", config=str(tmp_path / "devices.json")
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
            subprocess, "run",
            lambda cmd, **kw: (calls.append(cmd),
                               type("R", (), {"returncode": 0})())[1],
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

    def test_enum_resolution_is_unchanged(self, monkeypatch, tmp_path):
        _registry, calls = self._stale_fleet(monkeypatch)
        rc = _cmd_deploy(
            _make_args(target="2", config=str(tmp_path / "devices.json"))
        )
        assert rc == 0
        assert self._flashed_uid(calls) == _DEVICE_UID

    def test_uid_resolution_is_unchanged(self, monkeypatch, tmp_path):
        _registry, calls = self._stale_fleet(monkeypatch)
        rc = _cmd_deploy(
            _make_args(target=_DEVICE2_UID, config=str(tmp_path / "devices.json"))
        )
        assert rc == 0
        assert self._flashed_uid(calls) == _DEVICE2_UID

    def test_name_resolution_is_unchanged(self, monkeypatch, tmp_path):
        _registry, calls = self._stale_fleet(monkeypatch)
        rc = _cmd_deploy(
            _make_args(target="alpha-main", config=str(tmp_path / "devices.json"))
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

    def test_flash_retries_after_mass_erase(self, monkeypatch, tmp_path):
        """First flash fails, mass erase succeeds, second flash + reset succeed."""
        config = self._connect_one_device(monkeypatch, tmp_path)

        calls: list[list[str]] = []
        state = {"flash": 0}

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "flash" in cmd:
                state["flash"] += 1
                rc = 1 if state["flash"] == 1 else 0   # first flash fails
            else:
                rc = 0                                  # erase / reset succeed
            return type("R", (), {"returncode": rc})()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)

        args = _make_args(target=_DEVICE_UID, config=str(config))
        rc = _cmd_deploy(args)

        assert rc == 0
        assert state["flash"] == 2                      # flashed twice
        assert any("erase" in c and "--mass" in c for c in calls)

    def test_mass_erase_failure_aborts_without_retry(self, monkeypatch, tmp_path, capsys):
        """If the mass erase itself fails, deploy aborts and does not re-flash."""
        config = self._connect_one_device(monkeypatch, tmp_path)

        state = {"flash": 0}

        def fake_run(cmd, **kw):
            if "flash" in cmd:
                state["flash"] += 1
                rc = 1
            elif "erase" in cmd:
                rc = 5
            else:
                rc = 0
            return type("R", (), {"returncode": rc})()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)

        args = _make_args(target=_DEVICE_UID, config=str(config))
        rc = _cmd_deploy(args)

        assert rc == 5
        assert state["flash"] == 1                      # no retry after erase failure
        assert "mass erase failed" in capsys.readouterr().err.lower()

    def test_successful_flash_skips_mass_erase(self, monkeypatch, tmp_path):
        """The normal path never mass-erases when the first flash succeeds."""
        config = self._connect_one_device(monkeypatch, tmp_path)

        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return type("R", (), {"returncode": 0})()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)

        args = _make_args(target=_DEVICE_UID, config=str(config))
        rc = _cmd_deploy(args)

        assert rc == 0
        assert not any("erase" in c for c in calls)
