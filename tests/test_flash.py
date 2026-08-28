"""Unit tests for mbdeploy.flash.flash_hex.

Exercises flash_hex directly (not through the CLI/argparse layer), using
the same shared-subprocess-module monkeypatching technique as
tests/test_devices.py::TestMassEraseRecovery, so this is the mechanical
proof that the extraction from cli._cmd_deploy preserved pyocd's argv,
messages, and return codes.

Ticket 010 switched flash_hex's internals from a single blocking
``subprocess.run()`` per pyocd invocation to a streaming
``subprocess.Popen()`` (see flash.py::_run_streamed), so it could relay
pyocd's output line by line through ``log`` instead of only at fixed
transition points. Every fake here therefore patches ``subprocess.Popen``
(not ``subprocess.run``) and returns a fake process object exposing just
the two members ``_run_streamed`` touches: ``.stdout`` (an iterable of
already-``\\n``-terminated lines) and ``.wait()`` (the exit code) --
mirroring how a real ``Popen`` instance is used, without invoking pyocd
for real.

Ticket 001 added a pre-flight ``intelhex`` validation step ahead of any
pyocd invocation, so every test below that expects the faked
``subprocess.Popen`` step to be reached now uses the ``valid_hex``
fixture (a real, on-disk, minimal valid Intel HEX file) instead of the
literal, nonexistent ``_HEX_PATH`` constant.
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

#: A minimal, complete, valid Intel HEX file: just the EOF record.
_VALID_HEX_CONTENT = ":00000001FF\n"


@pytest.fixture
def valid_hex(tmp_path) -> str:
    """A real, on-disk, valid Intel HEX file's path.

    ``flash_hex`` now validates ``hex_path`` with ``intelhex`` before ever
    invoking pyocd (ticket 001), so every test below that expects the
    faked ``subprocess.Popen`` step to be reached needs a real file --
    the module-level ``_HEX_PATH`` literal ("MICROBIT.hex") does not
    exist on disk and would now fail validation before reaching the fake.
    """
    path = tmp_path / "valid.hex"
    path.write_text(_VALID_HEX_CONTENT)
    return str(path)


class _FakeProcess:
    """Stand-in for a ``subprocess.Popen`` instance.

    ``flash.py::_run_streamed`` only ever iterates ``.stdout`` for lines
    and calls ``.wait()`` for the exit code, so that's all this fake
    needs to provide.
    """

    def __init__(self, returncode: int, lines: tuple[str, ...] = ()):
        self.returncode = returncode
        self.stdout = iter(f"{line}\n" for line in lines)

    def wait(self) -> int:
        return self.returncode


def _result(rc: int, lines: tuple[str, ...] = ()):
    return _FakeProcess(rc, lines)


class TestArgvConstruction:
    """A successful first flash should invoke exactly flash + reset."""

    def test_flash_and_reset_argv(self, monkeypatch, valid_hex):
        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _result(0)

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        rc = flash_mod.flash_hex(_UID, valid_hex, target_mcu=_MCU)

        assert rc == 0
        assert len(calls) == 2
        flash_cmd, reset_cmd = calls

        assert flash_cmd == [
            *_PYOCD, "flash",
            "-t", _MCU,
            "--uid", _UID,
            valid_hex,
        ]
        assert reset_cmd == [
            *_PYOCD, "reset",
            "-t", _MCU,
            "--uid", _UID,
        ]
        # No mass erase on a clean first flash.
        assert not any("erase" in c for c in calls)

    def test_erase_argv_on_recovery(self, monkeypatch, valid_hex):
        calls: list[list[str]] = []
        state = {"flash": 0}

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "flash" in cmd:
                state["flash"] += 1
                return _result(1 if state["flash"] == 1 else 0)
            return _result(0)

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        rc = flash_mod.flash_hex(_UID, valid_hex, target_mcu=_MCU)

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

    def test_flash_retries_after_mass_erase(self, monkeypatch, valid_hex):
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

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        rc = flash_mod.flash_hex(_UID, valid_hex, target_mcu=_MCU)

        assert rc == 0
        assert state["flash"] == 2                      # flashed twice
        assert any("erase" in c and "--mass" in c for c in calls)

    def test_mass_erase_failure_aborts_without_retry(self, monkeypatch, capsys, valid_hex):
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

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        rc = flash_mod.flash_hex(_UID, valid_hex, target_mcu=_MCU)

        assert rc == 5
        assert state["flash"] == 1                      # no retry after erase failure
        assert "mass erase failed" in capsys.readouterr().err.lower()

    def test_successful_flash_skips_mass_erase(self, monkeypatch, valid_hex):
        """The normal path never mass-erases when the first flash succeeds."""
        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _result(0)

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        rc = flash_mod.flash_hex(_UID, valid_hex, target_mcu=_MCU)

        assert rc == 0
        assert not any("erase" in c for c in calls)

    def test_flash_still_failing_after_mass_erase_returns_flash_rc(self, monkeypatch, capsys, valid_hex):
        """Mass erase succeeds but the retried flash still fails: return its rc."""
        state = {"flash": 0}

        def fake_run(cmd, **kw):
            if "flash" in cmd:
                state["flash"] += 1
                rc = 7
            else:
                rc = 0  # erase succeeds
            return _result(rc)

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        rc = flash_mod.flash_hex(_UID, valid_hex, target_mcu=_MCU)

        assert rc == 7
        assert state["flash"] == 2
        assert "flash still failed after mass erase" in capsys.readouterr().err.lower()


class TestLogRouting:
    """log=None must print to stderr; a supplied log callable must intercept it."""

    def test_log_none_prints_to_stderr(self, monkeypatch, capsys, valid_hex):
        def fake_run(cmd, **kw):
            if "flash" in cmd:
                return _result(1)
            elif "erase" in cmd:
                return _result(5)
            return _result(0)

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        rc = flash_mod.flash_hex(_UID, valid_hex, target_mcu=_MCU)

        assert rc == 5
        err = capsys.readouterr().err
        assert "flash failed" in err.lower()
        assert "mass erase failed" in err.lower()

    def test_supplied_log_receives_messages_and_stderr_stays_clean(self, monkeypatch, capsys, valid_hex):
        messages: list[str] = []

        def fake_run(cmd, **kw):
            if "flash" in cmd:
                return _result(1)
            elif "erase" in cmd:
                return _result(5)
            return _result(0)

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        rc = flash_mod.flash_hex(
            _UID, valid_hex, target_mcu=_MCU, log=messages.append
        )

        assert rc == 5
        assert any("flash failed" in m.lower() for m in messages)
        assert any("mass erase failed" in m.lower() for m in messages)
        assert capsys.readouterr().err == ""


class TestStreamedOutputRelay:
    """Regression guard for ticket 010.

    Before ticket 010, ``flash_hex``'s ``log`` callback fired only at
    three fixed transition messages -- never during the pyocd subprocess
    itself -- because each pyocd invocation was a single blocking
    ``subprocess.run()`` with no output capture at all. That silence is
    exactly what let a real, multi-second flash's server-side success
    race past ``deploy --remote``'s client-side read timeout (see
    docs/acceptance/003-009-multi-node-acceptance.md, Finding 2).

    This proves the fix directly at the unit level: a single
    ``flash_hex`` call whose (faked) pyocd subprocess emits several
    lines of progress output must route every one of them through
    ``log`` individually -- not batched into one call, not dropped, not
    limited to the three fixed status messages -- so a regression back
    to "log only fires at fixed transitions" would fail this test
    without needing real hardware.
    """

    def test_multiple_pyocd_lines_are_each_relayed_to_log(self, monkeypatch, valid_hex):
        messages: list[str] = []
        flash_progress = (
            "Erasing...",
            "Programming...",
            "Erased 463872 bytes (114 sectors), "
            "programmed 463872 bytes (114 pages) at 13.96 kB/s",
        )
        reset_progress = ("Resetting target.",)

        def fake_run(cmd, **kw):
            if "flash" in cmd:
                return _result(0, flash_progress)
            return _result(0, reset_progress)

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        rc = flash_mod.flash_hex(
            _UID, valid_hex, target_mcu=_MCU, log=messages.append
        )

        assert rc == 0
        # Every streamed line arrived as its own log() call, in order,
        # with no coalescing and none dropped.
        assert messages == list(flash_progress) + list(reset_progress)

    def test_multiple_pyocd_lines_each_print_to_stderr_when_log_is_none(
        self, monkeypatch, capsys, valid_hex
    ):
        flash_progress = ("Erasing...", "Programming...", "Verifying...")

        def fake_run(cmd, **kw):
            if "flash" in cmd:
                return _result(0, flash_progress)
            return _result(0, ())

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        rc = flash_mod.flash_hex(_UID, valid_hex, target_mcu=_MCU)

        assert rc == 0
        err_lines = capsys.readouterr().err.splitlines()
        assert err_lines == list(flash_progress)


class TestHexValidation:
    """Ticket 001: a bad hex file must never reach pyocd at all."""

    def test_malformed_hex_rejected_with_zero_subprocess_calls(
        self, monkeypatch, tmp_path
    ):
        bad_path = tmp_path / "bad.hex"
        bad_path.write_text("this is not a valid hex file\n")

        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _result(0)

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        messages: list[str] = []
        rc = flash_mod.flash_hex(
            _UID, str(bad_path), target_mcu=_MCU, log=messages.append
        )

        assert rc != 0
        assert calls == []
        assert any("hex" in m.lower() for m in messages)

    def test_missing_hex_rejected_with_zero_subprocess_calls(
        self, monkeypatch, tmp_path
    ):
        missing_path = tmp_path / "does-not-exist.hex"

        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _result(0)

        monkeypatch.setattr(subprocess, "Popen", fake_run)

        messages: list[str] = []
        rc = flash_mod.flash_hex(
            _UID, str(missing_path), target_mcu=_MCU, log=messages.append
        )

        assert rc != 0
        assert calls == []
        assert any("hex" in m.lower() for m in messages)
