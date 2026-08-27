"""Unit tests for mbdeploy.flash.flash_hex.

Exercises flash_hex directly (not through the CLI/argparse layer), using
the same shared-subprocess-module monkeypatching technique as
tests/test_devices.py::TestMassEraseRecovery, so this is the mechanical
proof that the extraction from cli._cmd_deploy preserved pyocd's argv,
messages, and return codes.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from mbdeploy import flash as flash_mod

_UID = "9906" + "c" * 36  # 40 hex chars, matches the style used elsewhere
_HEX_PATH = "MICROBIT.hex"
_MCU = "nrf52833"

_PYOCD = [sys.executable, "-m", "pyocd"]


def _result(rc: int):
    return type("R", (), {"returncode": rc})()


class TestArgvConstruction:
    """A successful first flash should invoke exactly flash + reset."""

    def test_flash_and_reset_argv(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _result(0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = flash_mod.flash_hex(_UID, _HEX_PATH, target_mcu=_MCU)

        assert rc == 0
        assert len(calls) == 2
        flash_cmd, reset_cmd = calls

        assert flash_cmd == [
            *_PYOCD, "flash",
            "-t", _MCU,
            "--uid", _UID,
            _HEX_PATH,
        ]
        assert reset_cmd == [
            *_PYOCD, "reset",
            "-t", _MCU,
            "--uid", _UID,
        ]
        # No mass erase on a clean first flash.
        assert not any("erase" in c for c in calls)

    def test_erase_argv_on_recovery(self, monkeypatch):
        calls: list[list[str]] = []
        state = {"flash": 0}

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "flash" in cmd:
                state["flash"] += 1
                return _result(1 if state["flash"] == 1 else 0)
            return _result(0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = flash_mod.flash_hex(_UID, _HEX_PATH, target_mcu=_MCU)

        assert rc == 0
        erase_calls = [c for c in calls if "erase" in c]
        assert len(erase_calls) == 1
        assert erase_calls[0] == [
            *_PYOCD, "erase",
            "-t", _MCU,
            "--uid", _UID,
            "--mass",
        ]


class TestMassEraseRecovery:
    """Direct-call equivalents of tests/test_devices.py::TestMassEraseRecovery."""

    def test_flash_retries_after_mass_erase(self, monkeypatch):
        """First flash fails, mass erase succeeds, second flash + reset succeed."""
        calls: list[list[str]] = []
        state = {"flash": 0}

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "flash" in cmd:
                state["flash"] += 1
                rc = 1 if state["flash"] == 1 else 0   # first flash fails
            else:
                rc = 0                                  # erase / reset succeed
            return _result(rc)

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = flash_mod.flash_hex(_UID, _HEX_PATH, target_mcu=_MCU)

        assert rc == 0
        assert state["flash"] == 2                      # flashed twice
        assert any("erase" in c and "--mass" in c for c in calls)

    def test_mass_erase_failure_aborts_without_retry(self, monkeypatch, capsys):
        """If the mass erase itself fails, flash_hex aborts and does not re-flash."""
        state = {"flash": 0}

        def fake_run(cmd, **kw):
            if "flash" in cmd:
                state["flash"] += 1
                rc = 1
            elif "erase" in cmd:
                rc = 5
            else:
                rc = 0
            return _result(rc)

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = flash_mod.flash_hex(_UID, _HEX_PATH, target_mcu=_MCU)

        assert rc == 5
        assert state["flash"] == 1                      # no retry after erase failure
        assert "mass erase failed" in capsys.readouterr().err.lower()

    def test_successful_flash_skips_mass_erase(self, monkeypatch):
        """The normal path never mass-erases when the first flash succeeds."""
        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _result(0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = flash_mod.flash_hex(_UID, _HEX_PATH, target_mcu=_MCU)

        assert rc == 0
        assert not any("erase" in c for c in calls)

    def test_flash_still_failing_after_mass_erase_returns_flash_rc(self, monkeypatch, capsys):
        """Mass erase succeeds but the retried flash still fails: return its rc."""
        state = {"flash": 0}

        def fake_run(cmd, **kw):
            if "flash" in cmd:
                state["flash"] += 1
                rc = 7
            else:
                rc = 0  # erase succeeds
            return _result(rc)

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = flash_mod.flash_hex(_UID, _HEX_PATH, target_mcu=_MCU)

        assert rc == 7
        assert state["flash"] == 2
        assert "flash still failed after mass erase" in capsys.readouterr().err.lower()


class TestLogRouting:
    """log=None must print to stderr; a supplied log callable must intercept it."""

    def test_log_none_prints_to_stderr(self, monkeypatch, capsys):
        def fake_run(cmd, **kw):
            if "flash" in cmd:
                return _result(1)
            elif "erase" in cmd:
                return _result(5)
            return _result(0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = flash_mod.flash_hex(_UID, _HEX_PATH, target_mcu=_MCU)

        assert rc == 5
        err = capsys.readouterr().err
        assert "flash failed" in err.lower()
        assert "mass erase failed" in err.lower()

    def test_supplied_log_receives_messages_and_stderr_stays_clean(self, monkeypatch, capsys):
        messages: list[str] = []

        def fake_run(cmd, **kw):
            if "flash" in cmd:
                return _result(1)
            elif "erase" in cmd:
                return _result(5)
            return _result(0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        rc = flash_mod.flash_hex(
            _UID, _HEX_PATH, target_mcu=_MCU, log=messages.append
        )

        assert rc == 5
        assert any("flash failed" in m.lower() for m in messages)
        assert any("mass erase failed" in m.lower() for m in messages)
        assert capsys.readouterr().err == ""
