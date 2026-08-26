"""Tests for the `connect` subcommand: argument shape, port resolution, and I/O.

No hardware is touched — the serial port is a scripted fake and every
device-discovery call is monkeypatched.
"""

from __future__ import annotations

import argparse
import io
import sys
import time

import pytest

import mbdeploy.console as console_mod
import mbdeploy.devices as devices_mod
from mbdeploy.cli import _build_parser, _cmd_connect, _connect_port


_UID = "9906" + "c" * 36            # 40 hex chars
_ENTRY = {
    "uid": _UID,
    "enum": 2,
    "port": "/dev/cu.stale",
    "role": "Nezha2",
    "common_name": "gutov",
    "board_name": "tovez",
}


def _registry() -> dict[str, dict]:
    return {_UID: _ENTRY.copy()}


class FakeSerial:
    """Minimal pyserial stand-in: scripted ``readline`` output, recorded writes."""

    def __init__(self, replies=(), read_delay: float = 0.01):
        self._replies = list(replies)
        self._read_delay = read_delay
        self.written = b""
        self.closed = False
        self.resets = 0
        self.in_waiting = 0
        self.is_open = True

    def reset_input_buffer(self):
        self.resets += 1

    def write(self, data):
        self.written += data
        return len(data)

    def flush(self):
        pass

    def readline(self):
        if self._replies:
            return self._replies.pop(0)
        time.sleep(self._read_delay)      # stands in for the port's read timeout
        return b""

    def read(self, size=1):
        return self.readline()

    def close(self):
        self.closed = True


def _args(target, message=(), baud=115200, timeout=0.2, config=None):
    return argparse.Namespace(
        target=target,
        message=list(message),
        baud=baud,
        timeout=timeout,
        config=config,
    )


# ---------------------------------------------------------------------------
# Argument shape
# ---------------------------------------------------------------------------

class TestConnectArguments:
    """`connect` puts options between two positional groups; argparse needs help."""

    def _parse(self, argv):
        return _build_parser().parse_args(argv)

    def test_option_between_target_and_message(self):
        """The documented form: `connect tovez --baud 9600 "HELLO"`.

        Plain argparse rejects this — it matches positionals greedily in one
        pass and calls the trailing message unrecognised.
        """
        args = self._parse(["connect", "tovez", "--baud", "9600", "HELLO"])
        assert args.target == "tovez"
        assert args.message == ["HELLO"]
        assert args.baud == 9600

    def test_trailing_options(self):
        args = self._parse(["connect", "tovez", "HELLO", "--baud", "9600"])
        assert (args.target, args.message, args.baud) == ("tovez", ["HELLO"], 9600)

    def test_leading_options(self):
        args = self._parse(["connect", "--baud", "9600", "tovez", "HELLO"])
        assert (args.target, args.message, args.baud) == ("tovez", ["HELLO"], 9600)

    def test_bare_target_defaults_to_interactive(self):
        args = self._parse(["connect", "tovez"])
        assert args.message == []
        assert args.baud == 115200

    def test_multi_word_message_is_kept_in_order(self):
        args = self._parse(["connect", "tovez", "SET", "SPEED", "50"])
        assert args.message == ["SET", "SPEED", "50"]

    def test_timeout_is_a_float(self):
        assert self._parse(["connect", "tovez", "--timeout", "5.5"]).timeout == 5.5

    def test_target_is_required(self):
        with pytest.raises(SystemExit):
            self._parse(["connect"])

    def test_unknown_option_is_still_rejected(self):
        """Intermixed parsing must not turn typo'd flags into message text."""
        with pytest.raises(SystemExit):
            self._parse(["connect", "tovez", "--bogus"])

    def test_other_subcommands_still_parse(self):
        """The intermixed subparser class applies to every subcommand."""
        assert self._parse(["deploy", "gutov", "--clean"]).clean is True
        assert self._parse(["list", "--fast"]).fast is True
        assert self._parse(["probe", "--clear"]).clear is True


# ---------------------------------------------------------------------------
# Port resolution
# ---------------------------------------------------------------------------

class TestConnectPort:
    def test_name_resolves_to_the_live_port(self, monkeypatch):
        """A registered board's port is re-read live, not taken from the file."""
        monkeypatch.setattr(devices_mod, "connected_uids", lambda: {_UID})
        monkeypatch.setattr(
            devices_mod, "port_serial_map", lambda uids: {_UID: "/dev/cu.live"}
        )
        assert _connect_port("tovez", _registry()) == "/dev/cu.live"

    def test_board_name_and_enum_also_resolve(self, monkeypatch):
        monkeypatch.setattr(devices_mod, "connected_uids", lambda: {_UID})
        monkeypatch.setattr(
            devices_mod, "port_serial_map", lambda uids: {_UID: "/dev/cu.live"}
        )
        assert _connect_port("tovez", _registry()) == "/dev/cu.live"
        assert _connect_port("2", _registry()) == "/dev/cu.live"

    def test_recorded_port_is_the_fallback(self, monkeypatch):
        """ioreg gives nothing off macOS; the last known port is better than none."""
        monkeypatch.setattr(devices_mod, "connected_uids", lambda: {_UID})
        monkeypatch.setattr(devices_mod, "port_serial_map", lambda uids: {})
        assert _connect_port("tovez", _registry()) == "/dev/cu.stale"

    def test_disconnected_device_is_refused(self, monkeypatch):
        monkeypatch.setattr(devices_mod, "connected_uids", lambda: set())
        with pytest.raises(ValueError, match="not connected"):
            _connect_port("tovez", _registry())

    def test_unregistered_port_path_is_used_directly(self, monkeypatch):
        """A raw /dev path works before the board has ever been probed."""
        monkeypatch.setattr(devices_mod, "connected_uids", lambda: set())
        assert _connect_port("/dev/cu.usbmodem99", {}) == "/dev/cu.usbmodem99"

    def test_explicit_path_is_never_redirected(self, monkeypatch):
        """A path the registry knows must still open *that* port.

        The recorded port is only as fresh as the last probe, so matching it
        and then re-resolving that board's current port would open a different
        board than the one the user named.
        """
        def _boom(*a, **kw):
            raise AssertionError("an explicit path must not be re-resolved")

        monkeypatch.setattr(devices_mod, "port_serial_map", _boom)
        monkeypatch.setattr(devices_mod, "connected_uids", _boom)
        assert _connect_port("/dev/cu.stale", _registry()) == "/dev/cu.stale"

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="No device found"):
            _connect_port("nosuchboard", _registry())


# ---------------------------------------------------------------------------
# send_command
# ---------------------------------------------------------------------------

class TestSendCommand:
    def test_message_is_sent_as_one_newline_terminated_line(self):
        ser = FakeSerial([b"OK\n"])
        assert console_mod.send_command(ser, "HELLO", timeout=1.0, idle_gap=0.05) == ["OK"]
        assert ser.written == b"HELLO\n"

    def test_stale_input_is_cleared_before_writing(self):
        ser = FakeSerial([b"OK\n"])
        console_mod.send_command(ser, "HELLO", timeout=1.0, idle_gap=0.05)
        assert ser.resets == 1

    def test_multi_line_reply_is_returned_whole(self):
        ser = FakeSerial([b"first\n", b"second\n", b"third\n"])
        lines = console_mod.send_command(ser, "STATUS", timeout=1.0, idle_gap=0.05)
        assert lines == ["first", "second", "third"]

    def test_crlf_and_blank_lines_are_stripped(self):
        ser = FakeSerial([b"OK\r\n", b"\r\n", b"DONE\n"])
        lines = console_mod.send_command(ser, "GO", timeout=1.0, idle_gap=0.05)
        assert lines == ["OK", "DONE"]

    def test_silent_board_returns_no_lines(self):
        ser = FakeSerial([])
        assert console_mod.send_command(ser, "HELLO", timeout=0.15, idle_gap=0.05) == []

    def test_returns_early_once_the_board_goes_quiet(self):
        """The idle gap ends the read; the timeout is only the outer bound."""
        ser = FakeSerial([b"OK\n"])
        start = time.time()
        console_mod.send_command(ser, "HELLO", timeout=10.0, idle_gap=0.05)
        assert time.time() - start < 2.0

    def test_a_chatty_board_is_cut_off_at_the_timeout(self):
        """A board that never goes quiet must not hang the command."""
        ser = FakeSerial([b"tick\n"] * 10_000, read_delay=0.0)
        start = time.time()
        console_mod.send_command(ser, "HELLO", timeout=0.2, idle_gap=0.05)
        assert time.time() - start < 2.0


# ---------------------------------------------------------------------------
# _cmd_connect
# ---------------------------------------------------------------------------

class TestCmdConnect:
    def _patch(self, monkeypatch, ser, port="/dev/cu.live"):
        import mbdeploy.cli as cli_mod

        monkeypatch.setattr(devices_mod, "load_devices", lambda _p: _registry())
        monkeypatch.setattr(cli_mod, "_connect_port", lambda t, r: port)
        monkeypatch.setattr(console_mod, "open_port", lambda p, b, **kw: ser)

    def test_one_shot_prints_the_reply_and_succeeds(self, monkeypatch, capsys):
        ser = FakeSerial([b"PONG\n"])
        self._patch(monkeypatch, ser)

        rc = _cmd_connect(_args("gutov", ["PING"], timeout=1.0))
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out == "PONG\n"      # reply only — no banner on stdout
        assert ser.written == b"PING\n"
        assert ser.closed

    def test_multiple_words_are_joined_with_spaces(self, monkeypatch, capsys):
        ser = FakeSerial([b"OK\n"])
        self._patch(monkeypatch, ser)

        assert _cmd_connect(_args("gutov", ["SET", "SPEED", "50"], timeout=1.0)) == 0
        assert ser.written == b"SET SPEED 50\n"

    def test_no_reply_is_a_failure(self, monkeypatch, capsys):
        ser = FakeSerial([])
        self._patch(monkeypatch, ser)

        rc = _cmd_connect(_args("gutov", ["PING"], timeout=0.15))
        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "no response" in captured.err
        assert ser.closed

    def test_unresolvable_target_fails_before_opening_a_port(self, monkeypatch, capsys):
        import mbdeploy.cli as cli_mod

        def _boom(*a, **kw):
            raise AssertionError("must not open a port for an unresolved target")

        monkeypatch.setattr(devices_mod, "load_devices", lambda _p: _registry())
        monkeypatch.setattr(console_mod, "open_port", _boom)
        monkeypatch.setattr(
            cli_mod, "_connect_port",
            lambda t, r: (_ for _ in ()).throw(ValueError("device not connected: x")),
        )

        assert _cmd_connect(_args("gutov", ["PING"])) == 1
        assert "not connected" in capsys.readouterr().err

    def test_unopenable_port_fails_cleanly(self, monkeypatch, capsys):
        import mbdeploy.cli as cli_mod

        monkeypatch.setattr(devices_mod, "load_devices", lambda _p: _registry())
        monkeypatch.setattr(cli_mod, "_connect_port", lambda t, r: "/dev/cu.live")

        def _refuse(port, baud, **kw):
            raise console_mod.ConsoleError(f"cannot open {port}: busy")

        monkeypatch.setattr(console_mod, "open_port", _refuse)

        assert _cmd_connect(_args("gutov", ["PING"])) == 1
        assert "cannot open" in capsys.readouterr().err

    def test_no_message_starts_an_interactive_session(self, monkeypatch, capsys):
        ser = FakeSerial([])
        self._patch(monkeypatch, ser)
        calls = []
        monkeypatch.setattr(
            console_mod, "interact", lambda s: (calls.append(s), 0)[1]
        )

        assert _cmd_connect(_args("gutov", [], baud=9600)) == 0
        assert calls == [ser]
        captured = capsys.readouterr()
        assert captured.out == ""            # the banner belongs on stderr
        assert "9600" in captured.err and "/dev/cu.live" in captured.err
        assert ser.closed


# ---------------------------------------------------------------------------
# open_port
# ---------------------------------------------------------------------------

class TestOpenPort:
    def test_missing_pyserial_is_reported(self, monkeypatch):
        monkeypatch.setattr(console_mod, "serial", None)
        with pytest.raises(console_mod.ConsoleError, match="pyserial"):
            console_mod.open_port("/dev/cu.live", 115200)

    def test_modem_lines_are_low_before_the_port_opens(self, monkeypatch):
        """Asserting DTR resets the target through DAPLink — connecting must not."""
        events = []

        class Recorder:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def __setattr__(self, name, value):
                if name in ("dtr", "rts", "port"):
                    events.append((name, value))
                object.__setattr__(self, name, value)

            def open(self):
                events.append(("open", True))

        monkeypatch.setattr(
            console_mod, "serial", type("S", (), {"Serial": Recorder})
        )
        console_mod.open_port("/dev/cu.live", 9600, settle=0)

        assert events.index(("dtr", False)) < events.index(("open", True))
        assert events.index(("rts", False)) < events.index(("open", True))


# ---------------------------------------------------------------------------
# Name resolution widened for connect
# ---------------------------------------------------------------------------

class TestNameResolutionMatchesDeploy:
    """`connect` and `deploy` address boards identically.

    They used to differ: `connect` matched `device_name` and `deploy` did not,
    so the same word reached one command and not the other. Both now match the
    board's own five-letter name and neither matches `common_name`.
    """

    def _registry(self) -> dict[str, dict]:
        # A board whose DEVICE: announcement names it 'tovez', labelled
        # 'robot' by whoever set the fleet up.
        return {
            _UID: {**_ENTRY, "device_name": "tovez",
                   "board_name": None, "common_name": "robot"}
        }

    def _live(self, monkeypatch):
        monkeypatch.setattr(devices_mod, "connected_uids", lambda: {_UID})
        monkeypatch.setattr(
            devices_mod, "port_serial_map", lambda uids: {_UID: "/dev/cu.live"}
        )

    def test_device_name_resolves_for_connect(self, monkeypatch):
        self._live(monkeypatch)
        assert _connect_port("tovez", self._registry()) == "/dev/cu.live"

    def test_device_name_resolves_for_deploy_too(self):
        """The asymmetry is gone — the same word reaches both commands."""
        entry = devices_mod.resolve_target("tovez", self._registry())
        assert entry["uid"] == _UID

    def test_common_name_resolves_for_neither(self, monkeypatch):
        """'robot' is a classroom label, not an address, for either command."""
        self._live(monkeypatch)
        with pytest.raises(ValueError, match="No device found"):
            _connect_port("robot", self._registry())
        with pytest.raises(ValueError, match="No device found"):
            devices_mod.resolve_target("robot", self._registry())


# ---------------------------------------------------------------------------
# interact
# ---------------------------------------------------------------------------

class TestInteract:
    def test_stdin_is_relayed_and_board_output_printed(self, monkeypatch, capsys):
        ser = FakeSerial([b"hi\n"])
        monkeypatch.setattr(console_mod, "EOF_DRAIN", 0.05)
        monkeypatch.setattr(sys, "stdin", io.StringIO("PING\nSTOP\n"))

        assert console_mod.interact(ser) == 0
        assert ser.written == b"PING\nSTOP\n"
        assert "hi" in capsys.readouterr().out

    def test_empty_stdin_exits_cleanly(self, monkeypatch, capsys):
        """Ctrl-D with nothing typed is a normal end of session, not an error."""
        ser = FakeSerial([])
        monkeypatch.setattr(console_mod, "EOF_DRAIN", 0.05)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))

        assert console_mod.interact(ser) == 0
        assert ser.written == b""

    def test_ctrl_c_stops_without_draining(self, monkeypatch):
        """Ctrl-C means stop now, so the EOF grace period is skipped."""
        ser = FakeSerial([])

        class Interrupting:
            def readline(self):
                raise KeyboardInterrupt

        monkeypatch.setattr(console_mod, "EOF_DRAIN", 5.0)
        monkeypatch.setattr(sys, "stdin", Interrupting())

        start = time.time()
        assert console_mod.interact(ser) == 0
        assert time.time() - start < 2.0
