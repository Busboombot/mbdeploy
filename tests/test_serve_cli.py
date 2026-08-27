"""Tests for the `serve` subcommand's CLI wiring (ticket 007):
argument parsing, `--token`/`--token-file` resolution, component
construction (`--bind` propagation), and the SIGINT/SIGTERM shutdown
handler.

No hardware, no real mDNS, no long sleeps, per the ticket's own testing
plan:

- Argparse-level tests use `_build_parser()` directly.
- `--token-file` resolution is tested against real temp files (correct
  content, missing file, empty file) -- no sockets involved.
- Signal handling is tested by calling the registered handler
  (`_ServeShutdown`) directly with fakes, rather than sending a real
  process signal -- safer and more deterministic than `os.kill`, and
  avoids the fact that `signal.signal()` only works on the main thread
  (so a background-thread integration test couldn't register it at all).
- `--bind` propagation is tested two ways: that `_build_serve_runtime`
  constructs the (faked) `Advertiser` with the right `bind_addr`, and
  that a *real* `Supervisor`/`AcceptLoop` pair, built the same way,
  actually binds a listener socket to that address (inspected via
  `socket.getsockname()`).

`_cmd_serve`/`_run_serve` themselves are not run end-to-end here: past
the token-resolution error path, `_run_serve` blocks in
`stop_event.wait()` until a signal arrives, which is exactly the
blocking behavior the ticket says to avoid re-creating in a test.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import Any

import pytest

import mbdeploy.cli as cli_mod
import mbdeploy.server as server_mod

_UID = "9906" + "e" * 36


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeAdvertiser:
    """Stand-in for `mdns.Advertiser`: records the `bind_addr` it was
    constructed with and every register/unregister/close call. No real
    zeroconf, no real network traffic."""

    def __init__(self, bind_addr: str | None = None) -> None:
        self.bind_addr = bind_addr
        self.register_calls: list[tuple[str, str, int, dict]] = []
        self.unregister_calls: list[Any] = []
        self.close_calls = 0
        self._next_handle = 1

    def register(self, name: str, service_type: str, port: int, txt: dict) -> int:
        handle = self._next_handle
        self._next_handle += 1
        self.register_calls.append((name, service_type, port, txt))
        return handle

    def unregister(self, handle: Any) -> None:
        self.unregister_calls.append(handle)

    def close(self) -> None:
        self.close_calls += 1


class FakeSocket:
    """Minimal listener-socket stand-in: just records `close()`."""

    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class FakeBoard:
    """Minimal `Board` stand-in exposing only what `_ServeShutdown` reads."""

    def __init__(self, serial_listener: Any, flash_listener: Any) -> None:
        self.serial_listener = serial_listener
        self.flash_listener = flash_listener


class FakeAcceptLoop:
    """Stand-in for `server.AcceptLoop`: records unregister/close calls."""

    def __init__(self) -> None:
        self.unregister_calls: list[Any] = []
        self.close_calls = 0

    def unregister(self, sock: Any) -> None:
        self.unregister_calls.append(sock)

    def close(self) -> None:
        self.close_calls += 1


class FakeSupervisor:
    """Stand-in for `server.Supervisor`: records constructor kwargs and
    exposes a `boards` dict, exactly like the real thing, so
    `_ServeShutdown` can iterate it identically."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.boards: dict[str, Any] = {}


def _serve_args(**overrides: Any):
    """Parse a `serve` argv with sane defaults, then apply overrides --
    mirrors how `_cmd_serve` actually receives its `args` namespace."""
    parser = cli_mod._build_parser()
    args = parser.parse_args(["serve"])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

class TestServeArgParsing:
    def test_serve_subcommand_exists_with_defaults(self):
        args = _serve_args()
        assert args.func is cli_mod._cmd_serve
        assert args.config is None
        assert args.poll_interval == cli_mod._DEFAULT_POLL_INTERVAL
        assert args.base_port == 0
        assert args.bind == ""
        assert args.token is None
        assert args.token_file is None
        assert args.no_flash is False
        assert args.target_mcu == cli_mod._DEFAULT_MCU
        assert args.service_name is None

    def test_every_flag_is_individually_settable(self):
        parser = cli_mod._build_parser()
        args = parser.parse_args([
            "serve",
            "--config", "/tmp/devices.json",
            "--poll-interval", "5",
            "--base-port", "9000",
            "--bind", "127.0.0.1",
            "--token", "secret123",
            "--no-flash",
            "--target-mcu", "nrf52840",
            "--service-name", "myboard",
        ])
        assert args.config == "/tmp/devices.json"
        assert args.poll_interval == 5.0
        assert args.base_port == 9000
        assert args.bind == "127.0.0.1"
        assert args.token == "secret123"
        assert args.token_file is None
        assert args.no_flash is True
        assert args.target_mcu == "nrf52840"
        assert args.service_name == "myboard"

    def test_token_and_token_file_are_mutually_exclusive(self):
        parser = cli_mod._build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["serve", "--token", "a", "--token-file", "/tmp/x"])
        assert exc.value.code != 0

    def test_token_file_flag_alone_parses(self):
        parser = cli_mod._build_parser()
        args = parser.parse_args(["serve", "--token-file", "/tmp/secret.txt"])
        assert args.token_file == "/tmp/secret.txt"
        assert args.token is None

    def test_help_documents_every_flag(self, capsys):
        parser = cli_mod._build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["serve", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        for flag in (
            "--config", "--poll-interval", "--base-port", "--bind",
            "--token", "--token-file", "--no-flash", "--target-mcu",
            "--service-name",
        ):
            assert flag in out, f"{flag} missing from `serve --help`"

    def test_existing_subcommands_still_parse(self):
        """`serve` is a new leaf in the subparser tree -- every existing
        subcommand's own argparse wiring must be unaffected."""
        parser = cli_mod._build_parser()
        assert parser.parse_args(["list"]).func is cli_mod._cmd_list
        assert parser.parse_args(["probe"]).func is cli_mod._cmd_probe
        assert parser.parse_args(["connect", "abcde"]).func is cli_mod._cmd_connect
        assert parser.parse_args(["deploy"]).func is cli_mod._cmd_deploy
        assert parser.parse_args(["build"]).func is cli_mod._cmd_build


# ---------------------------------------------------------------------------
# --token / --token-file resolution
# ---------------------------------------------------------------------------

class TestResolveServeToken:
    def test_neither_given_resolves_to_none(self):
        args = _serve_args(token=None, token_file=None)
        assert cli_mod._resolve_serve_token(args) is None

    def test_token_used_verbatim(self):
        args = _serve_args(token="s3cret", token_file=None)
        assert cli_mod._resolve_serve_token(args) == "s3cret"

    def test_token_file_reads_and_strips_trailing_newline(self, tmp_path: Path):
        token_file = tmp_path / "token.txt"
        token_file.write_text("s3cret\n")
        args = _serve_args(token=None, token_file=str(token_file))
        assert cli_mod._resolve_serve_token(args) == "s3cret"

    def test_token_file_strips_trailing_whitespace_and_crlf(self, tmp_path: Path):
        token_file = tmp_path / "token.txt"
        token_file.write_bytes(b"s3cret \r\n")
        args = _serve_args(token=None, token_file=str(token_file))
        assert cli_mod._resolve_serve_token(args) == "s3cret"

    def test_missing_token_file_raises(self, tmp_path: Path):
        args = _serve_args(token=None, token_file=str(tmp_path / "nope.txt"))
        with pytest.raises(ValueError, match="nope.txt"):
            cli_mod._resolve_serve_token(args)

    def test_empty_token_file_raises(self, tmp_path: Path):
        token_file = tmp_path / "empty.txt"
        token_file.write_text("\n")
        args = _serve_args(token=None, token_file=str(token_file))
        with pytest.raises(ValueError, match="empty"):
            cli_mod._resolve_serve_token(args)

    def test_cmd_serve_reports_missing_token_file_cleanly(self, tmp_path: Path, capsys):
        args = _serve_args(token=None, token_file=str(tmp_path / "nope.txt"))
        rc = cli_mod._cmd_serve(args)
        assert rc != 0
        err = capsys.readouterr().err
        assert "nope.txt" in err

    def test_cmd_serve_reports_empty_token_file_cleanly(self, tmp_path: Path, capsys):
        token_file = tmp_path / "empty.txt"
        token_file.write_text("   \n")
        args = _serve_args(token=None, token_file=str(token_file))
        rc = cli_mod._cmd_serve(args)
        assert rc != 0
        err = capsys.readouterr().err
        assert "empty" in err.lower()


# ---------------------------------------------------------------------------
# Component construction / --bind propagation
# ---------------------------------------------------------------------------

class TestBuildServeRuntime:
    def test_bind_propagates_to_advertiser_constructor(self, monkeypatch):
        monkeypatch.setattr("mbdeploy.mdns.Advertiser", FakeAdvertiser)
        args = _serve_args(bind="127.0.0.1")
        advertiser, accept_loop, supervisor = cli_mod._build_serve_runtime(
            args, Path("/fake/config.json"), token=None
        )
        try:
            assert isinstance(advertiser, FakeAdvertiser)
            assert advertiser.bind_addr == "127.0.0.1"
            assert supervisor.bind == "127.0.0.1"
        finally:
            accept_loop.selector.close()

    def test_no_bind_means_advertiser_gets_none(self, monkeypatch):
        monkeypatch.setattr("mbdeploy.mdns.Advertiser", FakeAdvertiser)
        args = _serve_args(bind="")
        advertiser, accept_loop, supervisor = cli_mod._build_serve_runtime(
            args, Path("/fake/config.json"), token=None
        )
        try:
            assert advertiser.bind_addr is None
            assert supervisor.bind == ""
        finally:
            accept_loop.selector.close()

    def test_every_option_threads_through_to_supervisor(self, monkeypatch):
        monkeypatch.setattr("mbdeploy.mdns.Advertiser", FakeAdvertiser)
        args = _serve_args(
            base_port=7000,
            bind="127.0.0.1",
            no_flash=True,
            target_mcu="nrf52840",
            service_name="myboard",
        )
        advertiser, accept_loop, supervisor = cli_mod._build_serve_runtime(
            args, Path("/some/config.json"), token="tok"
        )
        try:
            assert supervisor.config_path == Path("/some/config.json")
            assert supervisor.base_port == 7000
            assert supervisor.bind == "127.0.0.1"
            assert supervisor.token == "tok"
            assert supervisor.no_flash is True
            assert supervisor.target_mcu == "nrf52840"
            assert supervisor.service_name == "myboard"
        finally:
            accept_loop.selector.close()

    def test_bind_propagates_to_an_actually_bound_listener_socket(self, monkeypatch):
        """The fuller end-to-end check: a real `Supervisor`/`AcceptLoop`
        pair, built exactly the way `_cmd_serve` builds them, actually
        binds a board's listener socket to `--bind`'s address --
        inspected via `socket.getsockname()`, not just a recorded
        constructor kwarg."""
        monkeypatch.setattr("mbdeploy.mdns.Advertiser", FakeAdvertiser)

        entry = {"uid": _UID, "board_name": "tovez"}

        def _fake_probe_all(config_path, target_mcu="nrf52833", only_uids=None, clear=False):
            uids = {_UID} if only_uids is None else only_uids
            return [dict(entry) for u in uids if u == _UID]

        monkeypatch.setattr(server_mod.devices, "probe_all", _fake_probe_all)

        args = _serve_args(bind="127.0.0.1")
        advertiser, accept_loop, supervisor = cli_mod._build_serve_runtime(
            args, Path("/fake/config.json"), token=None
        )
        try:
            supervisor._tick([{"uid": _UID}])
            board = supervisor.boards[_UID]
            assert board.serial_listener.getsockname()[0] == "127.0.0.1"
            assert board.flash_listener.getsockname()[0] == "127.0.0.1"
        finally:
            for board in supervisor.boards.values():
                for sock in (board.serial_listener, board.flash_listener):
                    if sock is not None:
                        sock.close()
            accept_loop.selector.close()


# ---------------------------------------------------------------------------
# SIGINT/SIGTERM shutdown
# ---------------------------------------------------------------------------

class FakeSupervisorThread:
    """Stand-in for the real `threading.Thread` running `Supervisor.run`:
    records whether/when it was joined, so a test can prove
    `_ServeShutdown` waits for an in-flight tick *before* touching the
    Advertiser or any listener -- the fix for a real race observed
    against a live `zeroconf` backend (a tick mid-`Advertiser.register()`
    when a signal lands must finish before `Advertiser.close()` runs, or
    `zeroconf` raises `RuntimeError: Event loop is closed`)."""

    def __init__(self, alive: bool = True) -> None:
        self._alive = alive
        self.join_calls: list[float | None] = []

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)
        self._alive = False


class TestServeShutdown:
    def _make(self):
        advertiser = FakeAdvertiser()
        accept_loop = FakeAcceptLoop()
        supervisor = FakeSupervisor()
        serial_sock, flash_sock = FakeSocket(), FakeSocket()
        supervisor.boards[_UID] = FakeBoard(serial_sock, flash_sock)
        stop_event = threading.Event()
        shutdown = cli_mod._ServeShutdown(supervisor, accept_loop, advertiser, stop_event)
        return shutdown, supervisor, accept_loop, advertiser, stop_event, serial_sock, flash_sock

    def test_shutdown_joins_supervisor_thread_before_closing_advertiser(self):
        """The race fix: `_ServeShutdown` must join a live
        `supervisor_thread` -- letting an in-flight tick's
        `Advertiser.register()` finish -- before it ever calls
        `Advertiser.close()`."""
        order: list[str] = []

        class OrderedAdvertiser(FakeAdvertiser):
            def close(self) -> None:
                order.append("advertiser.close")
                super().close()

        class OrderedThread(FakeSupervisorThread):
            def join(self, timeout: float | None = None) -> None:
                order.append("thread.join")
                super().join(timeout)

        advertiser = OrderedAdvertiser()
        accept_loop = FakeAcceptLoop()
        supervisor = FakeSupervisor()
        stop_event = threading.Event()
        thread = OrderedThread(alive=True)
        shutdown = cli_mod._ServeShutdown(
            supervisor, accept_loop, advertiser, stop_event, supervisor_thread=thread
        )

        shutdown()

        assert order == ["thread.join", "advertiser.close"]
        assert thread.join_calls == [cli_mod._SERVE_JOIN_TIMEOUT]
        assert stop_event.is_set()

    def test_shutdown_skips_join_when_thread_already_finished(self):
        thread = FakeSupervisorThread(alive=False)
        advertiser = FakeAdvertiser()
        accept_loop = FakeAcceptLoop()
        supervisor = FakeSupervisor()
        stop_event = threading.Event()
        shutdown = cli_mod._ServeShutdown(
            supervisor, accept_loop, advertiser, stop_event, supervisor_thread=thread
        )

        shutdown()  # must not raise even though the thread was never "started"

        assert thread.join_calls == []
        assert advertiser.close_calls == 1

    def test_shutdown_unregisters_mdns_and_closes_every_listener(self):
        shutdown, supervisor, accept_loop, advertiser, stop_event, serial_sock, flash_sock = self._make()

        shutdown()

        assert advertiser.close_calls == 1
        assert serial_sock.closed == 1
        assert flash_sock.closed == 1
        assert set(accept_loop.unregister_calls) == {serial_sock, flash_sock}
        assert accept_loop.close_calls == 1
        assert stop_event.is_set()

    def test_sigint_produces_the_same_shutdown_as_sigterm(self):
        import signal as signal_mod

        sigint_shutdown, _, sigint_loop, sigint_adv, sigint_event, s1, f1 = self._make()
        sigint_shutdown(signal_mod.SIGINT, None)

        sigterm_shutdown, _, sigterm_loop, sigterm_adv, sigterm_event, s2, f2 = self._make()
        sigterm_shutdown(signal_mod.SIGTERM, None)

        for adv, loop, sock_a, sock_b, event in (
            (sigint_adv, sigint_loop, s1, f1, sigint_event),
            (sigterm_adv, sigterm_loop, s2, f2, sigterm_event),
        ):
            assert adv.close_calls == 1
            assert sock_a.closed == 1
            assert sock_b.closed == 1
            assert loop.close_calls == 1
            assert event.is_set()

    def test_shutdown_is_idempotent_under_a_repeated_signal(self):
        shutdown, supervisor, accept_loop, advertiser, stop_event, serial_sock, flash_sock = self._make()

        shutdown()
        shutdown()   # simulates a second SIGTERM arriving mid/after shutdown
        shutdown(None, None)

        assert advertiser.close_calls == 1
        assert serial_sock.closed == 1
        assert flash_sock.closed == 1
        assert accept_loop.close_calls == 1

    def test_shutdown_tolerates_a_board_with_no_listeners(self):
        """A board whose listeners were already `None` (e.g. a fake used
        by another test suite) must not raise -- shutdown best-effort
        closes whatever is actually there."""
        advertiser = FakeAdvertiser()
        accept_loop = FakeAcceptLoop()
        supervisor = FakeSupervisor()
        supervisor.boards[_UID] = FakeBoard(None, None)
        stop_event = threading.Event()
        shutdown = cli_mod._ServeShutdown(supervisor, accept_loop, advertiser, stop_event)

        shutdown()  # must not raise

        assert advertiser.close_calls == 1
        assert accept_loop.close_calls == 1

    def test_shutdown_tolerates_no_boards_at_all(self):
        advertiser = FakeAdvertiser()
        accept_loop = FakeAcceptLoop()
        supervisor = FakeSupervisor()
        stop_event = threading.Event()
        shutdown = cli_mod._ServeShutdown(supervisor, accept_loop, advertiser, stop_event)

        shutdown()

        assert advertiser.close_calls == 1
        assert accept_loop.close_calls == 1
