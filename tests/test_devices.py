"""Unit tests for mbdeploy.devices logic and CLI relay/target guards.

All tests run without connected hardware — hardware-touching functions
(flashable_probes, load_devices, probe_all) are monkeypatched or
exercised via tmp_path fixtures.
"""

from __future__ import annotations

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

    def test_resolve_by_device_name(self):
        """Name token falls through to device_name when common_name differs."""
        # relay1 entry: common_name="bridge1", device_name="relay1"
        result = resolve_target("relay1", self._registry())
        assert result["uid"] == _RELAY_UID

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
