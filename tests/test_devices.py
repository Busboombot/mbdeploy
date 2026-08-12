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

    def test_resolve_by_port(self):
        """Port-like token (contains '/') matches by port field."""
        result = resolve_target("/dev/cu.relay1", self._registry())
        assert result["uid"] == _RELAY_UID

    def test_resolve_by_uid(self):
        """40-hex-char token matches by uid field."""
        result = resolve_target(_DEVICE_UID, self._registry())
        assert result["uid"] == _DEVICE_UID

    def test_resolve_by_common_name(self):
        """Name token matches case-insensitively on common_name."""
        result = resolve_target("gutov", self._registry())
        assert result["uid"] == _DEVICE_UID

    def test_resolve_by_device_name_is_not_supported(self):
        """Deploy target names should resolve via common_name only."""
        with pytest.raises(ValueError, match="No device found"):
            resolve_target("relay1", self._registry())

    def test_resolve_by_board_name(self):
        """The five-letter name list shows is usable as a deploy target."""
        registry = self._registry()
        registry[_DEVICE2_UID] = {**_DEVICE2_ENTRY, "common_name": "", "board_name": "tovez"}
        result = resolve_target("TOVEZ", registry)
        assert result["uid"] == _DEVICE2_UID

    def test_common_name_wins_over_board_name(self):
        registry = self._registry()
        registry[_DEVICE_UID]["board_name"] = "alpha"
        registry[_DEVICE2_UID] = {**_DEVICE2_ENTRY, "common_name": "alpha"}
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
