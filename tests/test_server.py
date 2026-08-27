"""Tests for `mbdeploy.server`: Board/Session occupancy, the accept loop,
and the `serve_serial`/`serve_flash` wire protocols.

No hardware is touched: serial ports are the scripted `FakeSerial` (reused
from `tests/test_connect.py`), and `flash_hex` is always monkeypatched to a
stub that records its call args and never shells out to pyocd.

Two layers of test, per the ticket's own testing plan:

- `TestBoardOccupancy` / `TestSessionTerminateAndJoin` construct `Board`
  and `Session` objects directly, with no sockets at all, so a regression
  in the claim/preempt/release state machine (Design Problem 1's fix)
  fails in milliseconds rather than only showing up as a flaky
  timing-dependent integration failure.
- Everything else runs the real `AcceptLoop` against real loopback TCP
  listeners and real client sockets, which is what actually proves the
  wire protocol and the preemption path are deadlock-free end to end.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import socket
import threading
import time
from pathlib import Path

import pytest

import mbdeploy.server as server_mod
from tests.test_connect import FakeSerial

_UID = "9906" + "d" * 36            # 40 hex chars, matches the style used elsewhere
_ENTRY = {"uid": _UID, "port": "/dev/fake0", "role": ""}


def make_board(uid: str = _UID, name: str = "tovez", entry: dict | None = None) -> server_mod.Board:
    return server_mod.Board(uid, name, dict(entry) if entry is not None else dict(_ENTRY))


class DummyConn:
    """Minimal socket stand-in for state-machine-only tests that never
    actually send bytes -- just records `shutdown()`/`close()` calls."""

    def __init__(self) -> None:
        self.shutdown_calls: list[int] = []
        self.closed = False

    def shutdown(self, how: int) -> None:
        self.shutdown_calls.append(how)

    def close(self) -> None:
        self.closed = True

    def sendall(self, data: bytes) -> None:
        pass


class FakeFlash:
    """Stand-in for `flash.flash_hex`: records call args, replays `log`
    lines, and returns a controllable exit code. Never touches pyocd."""

    def __init__(self, rc: int = 0, log_lines: tuple[str, ...] = ()) -> None:
        self.rc = rc
        self.log_lines = list(log_lines)
        self.calls: list[tuple[str, str, str]] = []
        self.written_payload: bytes | None = None

    def __call__(self, uid, hex_path, target_mcu, log=None):
        self.calls.append((uid, hex_path, target_mcu))
        with open(hex_path, "rb") as f:
            self.written_payload = f.read()
        if log is not None:
            for line in self.log_lines:
                log(line)
        return self.rc


class FakeAdvertiser:
    """Stand-in for `mdns.Advertiser`: records register/unregister calls
    and hands back sequential integer handles. No real zeroconf, no real
    network traffic -- `Supervisor` only ever calls `register`/
    `unregister` on whatever object it's given, so a fake with the same
    two methods is a complete substitute for these tests."""

    def __init__(self) -> None:
        self.registered: dict[int, tuple[str, str, int, dict]] = {}
        self.register_calls: list[tuple[str, str, int, dict]] = []
        self.unregister_calls: list[int] = []
        self._next_handle = 1

    def register(self, name: str, service_type: str, port: int, txt: dict) -> int:
        handle = self._next_handle
        self._next_handle += 1
        self.registered[handle] = (name, service_type, port, txt)
        self.register_calls.append((name, service_type, port, txt))
        return handle

    def unregister(self, handle: int) -> None:
        self.registered.pop(handle, None)
        self.unregister_calls.append(handle)


def make_fake_probe_all(entries_by_uid: dict[str, dict], calls: list[dict]):
    """Build a `devices.probe_all`-shaped stand-in.

    Records every call's kwargs (so a test can assert exactly what
    `only_uids` a `Supervisor` arrival passed) and returns entries drawn
    from `entries_by_uid`, narrowed to `only_uids` the same way the real
    `probe_all` narrows to a scoped set -- never touching (or returning)
    an entry for any other UID.
    """

    def _fake_probe_all(config_path, target_mcu="nrf52833", only_uids=None, clear=False):
        calls.append(
            {
                "config_path": config_path,
                "target_mcu": target_mcu,
                "only_uids": only_uids,
                "clear": clear,
            }
        )
        uids = set(entries_by_uid) if only_uids is None else only_uids
        return [dict(entries_by_uid[u]) for u in uids if u in entries_by_uid]

    return _fake_probe_all


# ---------------------------------------------------------------------------
# Socket-level helpers
# ---------------------------------------------------------------------------

def _listener_socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    return s


def _connect(listener: socket.socket) -> socket.socket:
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.connect(listener.getsockname())
    return c


def _recv_line(sock: socket.socket) -> bytes:
    """Read one newline-terminated line from a *test-side* socket."""
    buf = bytearray()
    while True:
        b = sock.recv(1)
        if not b or b == b"\n":
            break
        buf.extend(b)
    return bytes(buf)


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _close_listener(loop: "server_mod.AcceptLoop", listener: socket.socket) -> None:
    loop.unregister(listener)
    listener.close()


@pytest.fixture
def accept_loop():
    loop = server_mod.AcceptLoop()
    thread = threading.Thread(target=loop.run, kwargs={"poll_timeout": 0.05}, daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        loop.close()
        thread.join(timeout=2.0)


@pytest.fixture
def make_supervisor(monkeypatch):
    """Factory fixture: `make_supervisor(entries_by_uid, **kwargs)` builds
    a `Supervisor` wired to a fake `devices.probe_all` and a fresh
    `FakeAdvertiser`, and returns `(supervisor, advertiser, calls)`.

    `_tick`/`_on_arrival`/`_on_departure` never start the accept loop's
    own thread (its `run()` is never called here) -- `register()`/
    `unregister()` work fine against a bare, unstarted `AcceptLoop`, and
    not spinning one up keeps every test in this fixture's family
    thread-free and sub-millisecond, per the ticket's own testing plan
    (no sleeping, no hardware). Every listener socket any test bound is
    closed on teardown so ephemeral loopback sockets never leak across
    the run.
    """
    built: list[tuple[server_mod.Supervisor, server_mod.AcceptLoop]] = []

    def _make(entries_by_uid, *, calls=None, base_port=0, advertiser=None, **kwargs):
        if calls is None:
            calls = []
        monkeypatch.setattr(
            server_mod.devices, "probe_all", make_fake_probe_all(entries_by_uid, calls)
        )
        adv = advertiser if advertiser is not None else FakeAdvertiser()
        loop = server_mod.AcceptLoop()
        sup = server_mod.Supervisor(
            accept_loop=loop,
            advertiser=adv,
            config_path=Path("/fake/config.json"),
            base_port=base_port,
            bind="127.0.0.1",
            **kwargs,
        )
        built.append((sup, loop))
        return sup, adv, calls

    yield _make

    for sup, loop in built:
        for board in sup.boards.values():
            for sock in (board.serial_listener, board.flash_listener):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        loop.selector.close()


# ---------------------------------------------------------------------------
# Board occupancy state machine -- no sockets, no threads beyond the test's own
# ---------------------------------------------------------------------------

class TestBoardOccupancy:
    def test_claim_serial_when_idle_succeeds(self):
        board = make_board()
        session = server_mod.SerialSession(DummyConn())
        assert board.claim_serial(session) is True
        assert board.occupant is session

    def test_claim_serial_when_occupied_by_serial_fails(self):
        board = make_board()
        first = server_mod.SerialSession(DummyConn())
        assert board.claim_serial(first) is True
        second = server_mod.SerialSession(DummyConn())
        assert board.claim_serial(second) is False
        assert board.occupant is first

    def test_claim_serial_when_occupied_by_flash_fails(self):
        board = make_board()
        flash_session = server_mod.FlashSession(DummyConn())
        board.occupant = flash_session
        serial_session = server_mod.SerialSession(DummyConn())
        assert board.claim_serial(serial_session) is False
        assert board.occupant is flash_session

    def test_claim_flash_when_idle_has_no_previous(self):
        board = make_board()
        flash_session = server_mod.FlashSession(DummyConn())
        claimed, previous = board.claim_flash(flash_session)
        assert claimed is True
        assert previous is None
        assert board.occupant is flash_session

    def test_claim_flash_when_occupied_by_serial_returns_it_as_previous(self):
        board = make_board()
        serial_session = server_mod.SerialSession(DummyConn())
        board.occupant = serial_session
        flash_session = server_mod.FlashSession(DummyConn())
        claimed, previous = board.claim_flash(flash_session)
        assert claimed is True
        assert previous is serial_session
        assert board.occupant is flash_session      # swapped in atomically

    def test_claim_flash_when_occupied_by_flash_fails(self):
        board = make_board()
        first_flash = server_mod.FlashSession(DummyConn())
        board.occupant = first_flash
        second_flash = server_mod.FlashSession(DummyConn())
        claimed, previous = board.claim_flash(second_flash)
        assert claimed is False
        assert previous is None
        assert board.occupant is first_flash        # untouched

    def test_release_clears_when_still_occupant(self):
        board = make_board()
        session = server_mod.SerialSession(DummyConn())
        board.occupant = session
        board.release(session)
        assert board.occupant is None

    def test_release_is_a_noop_for_a_stale_session(self):
        """A preempted session's own belated cleanup must not clobber a
        newer occupant that has since claimed the board."""
        board = make_board()
        old_session = server_mod.SerialSession(DummyConn())
        board.occupant = old_session
        new_session = server_mod.FlashSession(DummyConn())
        board.occupant = new_session   # simulates claim_flash having already run
        board.release(old_session)
        assert board.occupant is new_session


class TestSessionTerminateAndJoin:
    def test_terminate_sets_stop_and_shuts_down_and_closes_conn(self):
        conn = DummyConn()
        session = server_mod.SerialSession(conn)
        session.terminate()
        assert session.stop.is_set()
        assert conn.shutdown_calls == [socket.SHUT_RDWR]
        assert conn.closed is True

    def test_join_with_no_thread_returns_immediately(self):
        session = server_mod.SerialSession(DummyConn())
        session.join(timeout=0.01)     # thread is None -- must not block or raise

    def test_join_logs_a_warning_if_the_thread_does_not_exit_in_time(self, caplog):
        session = server_mod.SerialSession(DummyConn())
        block = threading.Event()
        t = threading.Thread(target=block.wait)
        t.start()
        session.thread = t
        try:
            with caplog.at_level(logging.WARNING, logger=server_mod.__name__):
                session.join(timeout=0.05)
            assert any("did not exit" in rec.message for rec in caplog.records)
        finally:
            block.set()
            t.join(timeout=2.0)

    def test_join_returns_quietly_once_the_thread_has_exited(self, caplog):
        session = server_mod.SerialSession(DummyConn())
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join(timeout=0.5)
        session.thread = t
        with caplog.at_level(logging.WARNING, logger=server_mod.__name__):
            session.join(timeout=0.5)
        assert not any("did not exit" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Accept loop
# ---------------------------------------------------------------------------

class TestAcceptLoop:
    def test_accept_spawns_exactly_one_thread_per_connection(self):
        loop = server_mod.AcceptLoop()
        release = threading.Event()

        def handler(conn):
            release.wait(timeout=2.0)
            conn.close()

        listener = _listener_socket()
        loop.register(listener, handler)
        thread = threading.Thread(target=loop.run, kwargs={"poll_timeout": 0.05}, daemon=True)
        thread.start()
        try:
            baseline = threading.active_count()
            client = _connect(listener)
            assert _wait_until(lambda: threading.active_count() == baseline + 1)
            release.set()
            assert _wait_until(lambda: threading.active_count() == baseline)
            client.close()
        finally:
            loop.close()
            thread.join(timeout=2.0)
            listener.close()

    def test_run_returns_promptly_once_stop_is_called(self):
        loop = server_mod.AcceptLoop()
        thread = threading.Thread(target=loop.run, kwargs={"poll_timeout": 0.05}, daemon=True)
        thread.start()
        time.sleep(0.05)
        start = time.time()
        loop.close()
        thread.join(timeout=2.0)
        assert time.time() - start < 1.0
        assert not thread.is_alive()


# ---------------------------------------------------------------------------
# serve_serial
# ---------------------------------------------------------------------------

class TestServeSerial:
    def test_raw_pipe_both_directions(self, accept_loop, monkeypatch):
        ser = FakeSerial([b"hello-from-board"])
        monkeypatch.setattr(server_mod.console, "open_port", lambda port, baud: ser)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_serial, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            assert client.recv(64) == b"hello-from-board"
            client.sendall(b"MOVE FORWARD\n")
            assert _wait_until(lambda: ser.written == b"MOVE FORWARD\n")
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_second_connection_gets_err_busy_first_session_unaffected(self, accept_loop, monkeypatch):
        ser = FakeSerial([])
        monkeypatch.setattr(server_mod.console, "open_port", lambda port, baud: ser)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_serial, board))
        first = _connect(listener)
        first.settimeout(2.0)
        try:
            assert _wait_until(lambda: isinstance(board.occupant, server_mod.SerialSession))

            second = _connect(listener)
            second.settimeout(2.0)
            try:
                assert _recv_line(second) == b"ERR busy"
                assert second.recv(16) == b""       # closed
            finally:
                second.close()

            first.sendall(b"PING\n")
            assert _wait_until(lambda: ser.written == b"PING\n")
        finally:
            first.close()
            _close_listener(accept_loop, listener)

    def test_open_port_failure_reports_err_and_releases_the_board(self, accept_loop, monkeypatch):
        def raising_open_port(port, baud):
            raise server_mod.console.ConsoleError("boom")

        monkeypatch.setattr(server_mod.console, "open_port", raising_open_port)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_serial, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            assert _recv_line(client) == b"ERR boom"
        finally:
            client.close()
        assert _wait_until(lambda: board.occupant is None)
        _close_listener(accept_loop, listener)

    def test_auth_success_then_raw_pipe(self, accept_loop, monkeypatch):
        ser = FakeSerial([b"data"])
        monkeypatch.setattr(server_mod.console, "open_port", lambda port, baud: ser)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(
            listener, functools.partial(server_mod.serve_serial, board, token="secret")
        )
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"AUTH secret\n")
            assert _recv_line(client) == b"OK"
            assert client.recv(64) == b"data"
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_auth_wrong_token_rejected_and_closed(self, accept_loop, monkeypatch):
        ser = FakeSerial([])
        monkeypatch.setattr(server_mod.console, "open_port", lambda port, baud: ser)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(
            listener, functools.partial(server_mod.serve_serial, board, token="secret")
        )
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"AUTH wrong\n")
            assert _recv_line(client) == b"ERR auth required"
            assert client.recv(16) == b""
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_no_auth_line_sent_rejected(self, accept_loop, monkeypatch):
        ser = FakeSerial([])
        monkeypatch.setattr(server_mod.console, "open_port", lambda port, baud: ser)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(
            listener, functools.partial(server_mod.serve_serial, board, token="secret")
        )
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"garbage\n")
            assert _recv_line(client) == b"ERR auth required"
        finally:
            client.close()
            _close_listener(accept_loop, listener)


# ---------------------------------------------------------------------------
# serve_flash
# ---------------------------------------------------------------------------

class TestServeFlash:
    def test_flash_basic_success(self, accept_loop, monkeypatch):
        fake_flash = FakeFlash(rc=0, log_lines=("erasing", "programming"))
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            payload = b":10000000FF" * 4
            client.sendall(f"FLASH {len(payload)}\n".encode())
            assert _recv_line(client) == b"OK send"
            client.sendall(payload)
            lines = []
            while True:
                line = _recv_line(client)
                lines.append(line)
                if line.startswith(b"OK") or line.startswith(b"ERR"):
                    break
            assert lines == [b"LOG erasing", b"LOG programming", b"OK flashed"]
            assert len(fake_flash.calls) == 1
            assert fake_flash.calls[0][0] == _UID
            assert fake_flash.written_payload == payload
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_flash_failure_reports_err_with_exit_code(self, accept_loop, monkeypatch):
        fake_flash = FakeFlash(rc=3)
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            payload = b"payload-bytes"
            client.sendall(f"FLASH {len(payload)}\n".encode())
            assert _recv_line(client) == b"OK send"
            client.sendall(payload)
            assert _recv_line(client) == b"ERR flash failed (exit 3)"
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_flash_with_matching_sha256_proceeds(self, accept_loop, monkeypatch):
        fake_flash = FakeFlash(rc=0)
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            payload = b"hexpayloadbytes"
            digest = hashlib.sha256(payload).hexdigest()
            client.sendall(f"FLASH {len(payload)} sha256={digest}\n".encode())
            assert _recv_line(client) == b"OK send"
            client.sendall(payload)
            assert _recv_line(client) == b"OK flashed"
            assert len(fake_flash.calls) == 1
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_flash_sha256_mismatch_rejected_flash_hex_never_called(self, accept_loop, monkeypatch):
        fake_flash = FakeFlash(rc=0)
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            payload = b"hexpayloadbytes"
            wrong_digest = "0" * 64
            client.sendall(f"FLASH {len(payload)} sha256={wrong_digest}\n".encode())
            assert _recv_line(client) == b"OK send"
            client.sendall(payload)
            assert _recv_line(client) == b"ERR sha256 mismatch"
            assert fake_flash.calls == []
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_malformed_header_missing_byte_count_rejected_before_payload(self, accept_loop, monkeypatch):
        fake_flash = FakeFlash(rc=0)
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"FLASH\n")
            assert _recv_line(client) == b"ERR bad header"
            assert fake_flash.calls == []
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_short_payload_client_closes_early(self, accept_loop, monkeypatch):
        fake_flash = FakeFlash(rc=0)
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"FLASH 100\n")
            assert _recv_line(client) == b"OK send"
            client.sendall(b"short")
            client.shutdown(socket.SHUT_WR)
            assert _recv_line(client) == b"ERR short payload"
            assert fake_flash.calls == []
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_short_payload_via_read_timeout_without_close(self, accept_loop, monkeypatch):
        """Client stalls mid-payload without closing -- server must give up
        on its own once `payload_timeout` elapses, not hang forever."""
        fake_flash = FakeFlash(rc=0)
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(
            listener, functools.partial(server_mod.serve_flash, board, payload_timeout=0.3)
        )
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"FLASH 100\n")
            assert _recv_line(client) == b"OK send"
            client.sendall(b"short")            # far fewer than 100 bytes; then goes silent
            assert _recv_line(client) == b"ERR short payload"
            assert fake_flash.calls == []
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_relay_guard_blocks_without_force_relay(self, accept_loop, monkeypatch):
        fake_flash = FakeFlash(rc=0)
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)
        board = make_board(entry={"uid": _UID, "port": "/dev/fake0", "role": "RADIOBRIDGE"})
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            payload = b"xyz"
            client.sendall(f"FLASH {len(payload)}\n".encode())
            assert _recv_line(client) == "ERR relay refused — send force-relay".encode("utf-8")
            assert fake_flash.calls == []
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_relay_guard_bypassed_with_force_relay(self, accept_loop, monkeypatch):
        fake_flash = FakeFlash(rc=0)
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)
        board = make_board(entry={"uid": _UID, "port": "/dev/fake0", "role": "RADIOBRIDGE"})
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            payload = b"xyz"
            client.sendall(f"FLASH {len(payload)} force-relay\n".encode())
            assert _recv_line(client) == b"OK send"
            client.sendall(payload)
            assert _recv_line(client) == b"OK flashed"
            assert len(fake_flash.calls) == 1
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_no_flash_disables_before_relay_guard_and_header_parsing(self, accept_loop, monkeypatch):
        """--no-flash must win even against a request that is *also*
        malformed and *also* relay-guarded -- proof it is checked first."""
        fake_flash = FakeFlash(rc=0)
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)
        board = make_board(entry={"uid": _UID, "port": "/dev/fake0", "role": "RADIOBRIDGE"})
        listener = _listener_socket()
        accept_loop.register(
            listener, functools.partial(server_mod.serve_flash, board, no_flash=True)
        )
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"FLASH\n")          # also malformed (no byte count)
            assert _recv_line(client) == b"ERR flash disabled"
            assert fake_flash.calls == []
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_second_concurrent_flash_gets_err_busy(self, accept_loop, monkeypatch):
        release_flash = threading.Event()
        calls: list[str] = []

        def slow_flash_hex(uid, hex_path, target_mcu, log=None):
            calls.append(uid)
            release_flash.wait(timeout=2.0)
            return 0

        monkeypatch.setattr(server_mod, "flash_hex", slow_flash_hex)
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))

        first = _connect(listener)
        first.settimeout(2.0)
        second = _connect(listener)
        second.settimeout(2.0)
        try:
            payload = b"abc"
            first.sendall(f"FLASH {len(payload)}\n".encode())
            assert _recv_line(first) == b"OK send"
            first.sendall(payload)
            assert _wait_until(lambda: len(calls) == 1)   # flash_hex now blocking

            second.sendall(f"FLASH {len(payload)}\n".encode())
            assert _recv_line(second) == b"ERR busy"

            release_flash.set()
            assert _recv_line(first) == b"OK flashed"
        finally:
            first.close()
            second.close()
            _close_listener(accept_loop, listener)

    def test_info_returns_documented_json(self, accept_loop, monkeypatch):
        board = make_board(entry={"uid": _UID, "port": "/dev/fakeX", "role": "robot"})
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"INFO\n")
            line = _recv_line(client)
            assert line.startswith(b"OK ")
            payload = json.loads(line[len(b"OK "):])
            assert payload == {
                "uid": _UID,
                "board_name": board.name,
                "role": "robot",
                "port": "/dev/fakeX",
                "connected": True,
            }
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_info_independent_of_in_progress_session(self, accept_loop, monkeypatch):
        board = make_board()
        board.occupant = server_mod.FlashSession(DummyConn())   # simulate in-flight flash
        listener = _listener_socket()
        accept_loop.register(listener, functools.partial(server_mod.serve_flash, board))
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"INFO\n")
            line = _recv_line(client)
            assert line.startswith(b"OK ")
            payload = json.loads(line[len(b"OK "):])
            assert payload["uid"] == _UID
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_auth_success_then_info(self, accept_loop, monkeypatch):
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(
            listener, functools.partial(server_mod.serve_flash, board, token="tok")
        )
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"AUTH tok\n")
            assert _recv_line(client) == b"OK"
            client.sendall(b"INFO\n")
            assert _recv_line(client).startswith(b"OK ")
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_auth_wrong_token_rejected(self, accept_loop, monkeypatch):
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(
            listener, functools.partial(server_mod.serve_flash, board, token="tok")
        )
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"AUTH nope\n")
            assert _recv_line(client) == b"ERR auth required"
            assert client.recv(16) == b""
        finally:
            client.close()
            _close_listener(accept_loop, listener)

    def test_no_auth_sent_rejected(self, accept_loop, monkeypatch):
        board = make_board()
        listener = _listener_socket()
        accept_loop.register(
            listener, functools.partial(server_mod.serve_flash, board, token="tok")
        )
        client = _connect(listener)
        client.settimeout(2.0)
        try:
            client.sendall(b"INFO\n")           # command sent directly, no AUTH
            assert _recv_line(client) == b"ERR auth required"
        finally:
            client.close()
            _close_listener(accept_loop, listener)


# ---------------------------------------------------------------------------
# The central case: a FLASH must preempt a live serial session without
# deadlocking -- Design Problem 1's fix, exercised end to end.
# ---------------------------------------------------------------------------

class TestFlashPreemptsLiveSession:
    def test_flash_kills_live_serial_session_and_proceeds(self, accept_loop, monkeypatch):
        ser = FakeSerial([])   # never produces data on its own; just holds the session open
        monkeypatch.setattr(server_mod.console, "open_port", lambda port, baud: ser)
        fake_flash = FakeFlash(rc=0)
        monkeypatch.setattr(server_mod, "flash_hex", fake_flash)

        board = make_board()
        serial_listener = _listener_socket()
        flash_listener = _listener_socket()
        accept_loop.register(serial_listener, functools.partial(server_mod.serve_serial, board))
        accept_loop.register(flash_listener, functools.partial(server_mod.serve_flash, board))

        serial_client = _connect(serial_listener)
        serial_client.settimeout(2.0)
        flash_client = _connect(flash_listener)
        flash_client.settimeout(2.0)
        try:
            assert _wait_until(lambda: isinstance(board.occupant, server_mod.SerialSession))

            start = time.time()
            payload = b"xyz"
            flash_client.sendall(f"FLASH {len(payload)}\n".encode())
            assert _recv_line(flash_client) == b"OK send"
            flash_client.sendall(payload)

            # The serial client observably loses its connection: the flash
            # preempted and tore the old session's socket down. A socket
            # timeout here (not a graceful assertion) is exactly the
            # failure mode this test exists to catch -- it means the
            # preemption path hung on Board.lock instead of releasing it
            # before tearing the old session down.
            assert serial_client.recv(16) == b""

            assert _recv_line(flash_client) == b"OK flashed"
            elapsed = time.time() - start
            assert elapsed < 2.0, "flash preemption took too long -- possible lock misuse"
            assert len(fake_flash.calls) == 1
        finally:
            serial_client.close()
            flash_client.close()
            _close_listener(accept_loop, serial_listener)
            _close_listener(accept_loop, flash_listener)

        assert board.occupant is None


# ---------------------------------------------------------------------------
# Supervisor -- the USB watcher (ticket 006)
# ---------------------------------------------------------------------------

_UID_A = "aaaa" + "1" * 36           # 40 hex chars
_UID_B = "bbbb" + "2" * 36           # 40 hex chars


class TestSupervisorTick:
    """`_tick(probes)` driven directly, per the ticket's own testing plan:
    hand-built probe-list sequences against a fake `Advertiser` and a
    fake `devices.probe_all` -- no sleeping, no real hardware, no real
    mDNS."""

    def test_tick_is_directly_callable_with_plain_probe_dicts(self, make_supervisor):
        """No sleep, no hardware: a bare list of `{"uid": ...}` dicts is
        enough to drive a full arrival."""
        sup, adv, calls = make_supervisor({_UID_A: {"uid": _UID_A, "board_name": "tovez"}})
        sup._tick([{"uid": _UID_A}])
        assert _UID_A in sup.boards

    def test_arrival_probes_registers_both_services_and_binds_two_listeners(
        self, make_supervisor
    ):
        sup, adv, calls = make_supervisor(
            {_UID_A: {"uid": _UID_A, "board_name": "tovez", "role": "", "enum": 1}}
        )
        sup._tick([{"uid": _UID_A}])

        assert len(calls) == 1
        assert calls[0]["only_uids"] == {_UID_A}

        assert len(adv.register_calls) == 2
        names = {c[0] for c in adv.register_calls}
        assert names == {"tovez"}
        service_types = {c[1] for c in adv.register_calls}
        assert service_types == {server_mod.SERIAL_SERVICE_TYPE, server_mod.FLASH_SERVICE_TYPE}

        board = sup.boards[_UID_A]
        assert board.serial_listener is not None
        assert board.flash_listener is not None
        assert board.serial_listener.getsockname()[1] > 0
        assert board.flash_listener.getsockname()[1] > 0
        assert board.serial_mdns_handle is not None
        assert board.flash_mdns_handle is not None

    def test_departure_unregisters_both_services_and_closes_both_listeners(
        self, make_supervisor
    ):
        sup, adv, calls = make_supervisor({_UID_A: {"uid": _UID_A, "board_name": "tovez"}})
        sup._tick([{"uid": _UID_A}])
        board = sup.boards[_UID_A]
        serial_sock, flash_sock = board.serial_listener, board.flash_listener
        serial_handle, flash_handle = board.serial_mdns_handle, board.flash_mdns_handle

        sup._tick([])   # UID no longer present

        assert _UID_A not in sup.boards
        assert set(adv.unregister_calls) == {serial_handle, flash_handle}
        assert serial_sock.fileno() == -1
        assert flash_sock.fileno() == -1

    def test_departure_does_not_skip_a_board_with_a_live_occupant(self, make_supervisor):
        """Anti-regression test for the old bug (sprint.md Design Problem
        1): a non-blocking lock acquire used to skip a board with a live
        session on every tick, leaking its advertisement after unplug.
        `_tick`'s departure detection must never contend for `Board.lock`
        at all, so a live occupant is torn down and the board is
        cleaned up regardless."""
        sup, adv, calls = make_supervisor({_UID_A: {"uid": _UID_A, "board_name": "tovez"}})
        sup._tick([{"uid": _UID_A}])
        board = sup.boards[_UID_A]

        thread_exited = threading.Event()
        session = server_mod.SerialSession(DummyConn())

        def _run():
            session.stop.wait(timeout=2.0)
            thread_exited.set()

        thread = threading.Thread(target=_run, daemon=True)
        session.thread = thread
        board.occupant = session      # simulates a live, long-running session
        thread.start()

        start = time.time()
        sup._tick([])                 # the board vanishes from the probe list
        elapsed = time.time() - start

        assert elapsed < 1.0, "departure blocked on the board lock"
        assert _UID_A not in sup.boards
        assert session.stop.is_set()
        assert session.conn.closed is True
        assert _wait_until(thread_exited.is_set)
        thread.join(timeout=2.0)

    def test_repeating_the_same_tick_twice_is_idempotent(self, make_supervisor):
        sup, adv, calls = make_supervisor({_UID_A: {"uid": _UID_A, "board_name": "tovez"}})
        sup._tick([{"uid": _UID_A}])
        assert len(calls) == 1
        assert len(adv.register_calls) == 2

        sup._tick([{"uid": _UID_A}])   # same UID set again -- no-op
        assert len(calls) == 1
        assert len(adv.register_calls) == 2

        sup._tick([])
        assert len(adv.unregister_calls) == 2

        sup._tick([])                  # already gone -- no-op, no error
        assert len(adv.unregister_calls) == 2

    def test_arrival_probe_never_touches_an_already_known_boards_entry(self, make_supervisor):
        """Two simultaneously-connected fake boards: B's arrival must not
        re-probe A, which is already known and still connected --
        Supervisor-level coverage of the same guarantee Ticket 002 tests
        at the `devices.probe_all` level."""
        sup, adv, calls = make_supervisor(
            {
                _UID_A: {"uid": _UID_A, "board_name": "aaaaa"},
                _UID_B: {"uid": _UID_B, "board_name": "bbbbb"},
            }
        )
        sup._tick([{"uid": _UID_A}])
        sup._tick([{"uid": _UID_A}, {"uid": _UID_B}])   # B arrives; A unchanged

        assert calls[0]["only_uids"] == {_UID_A}
        assert calls[1]["only_uids"] == {_UID_B}
        assert _UID_A in sup.boards
        assert _UID_B in sup.boards

    @pytest.mark.parametrize(
        "entry, expected",
        [
            ({"uid": _UID_A, "board_name": "tovez", "device_name": "other"}, "tovez"),
            ({"uid": _UID_A, "board_name": "vevov"}, "vevov"),
            ({"uid": _UID_A, "device_name": "getez"}, "getez"),
            ({"uid": _UID_A}, f"mb-{_UID_A[-8:]}"),
        ],
        ids=["both-present-board_name-wins", "board_name-only", "device_name-only", "neither-uid-fallback"],
    )
    def test_instance_naming_fallback_order(self, make_supervisor, entry, expected):
        sup, adv, calls = make_supervisor({_UID_A: entry})
        sup._tick([{"uid": _UID_A}])
        names = {c[0] for c in adv.register_calls}
        assert names == {expected}
        assert sup.boards[_UID_A].name == expected

    def test_service_name_overrides_the_fallback_chain(self, make_supervisor):
        """Ticket 007's `--service-name`: when set on the Supervisor, it
        wins over `board_name`/`device_name`/`mb-<uid8>` entirely, for
        every board -- a single-board-host override, not a per-board
        name (sprint.md Step 7 Open Question)."""
        sup, adv, calls = make_supervisor(
            {_UID_A: {"uid": _UID_A, "board_name": "tovez"}},
            service_name="nolanet-1",
        )
        sup._tick([{"uid": _UID_A}])
        names = {c[0] for c in adv.register_calls}
        assert names == {"nolanet-1"}
        assert sup.boards[_UID_A].name == "nolanet-1"

    def test_port_allocation_sequential_then_reclaimed_on_departure(self, make_supervisor):
        sup, adv, calls = make_supervisor(
            {
                _UID_A: {"uid": _UID_A, "board_name": "aaaaa"},
                _UID_B: {"uid": _UID_B, "board_name": "bbbbb"},
            },
            base_port=6000,
        )
        sup._tick([{"uid": _UID_A}])
        board_a = sup.boards[_UID_A]
        assert (board_a.serial_listener.getsockname()[1], board_a.flash_listener.getsockname()[1]) == (6000, 6001)

        sup._tick([{"uid": _UID_A}, {"uid": _UID_B}])
        board_b = sup.boards[_UID_B]
        assert (board_b.serial_listener.getsockname()[1], board_b.flash_listener.getsockname()[1]) == (6002, 6003)

        sup._tick([{"uid": _UID_B}])   # A departs -- its ports go to the free-list

        sup._tick([{"uid": _UID_B}, {"uid": _UID_A}])   # A re-arrives
        board_a2 = sup.boards[_UID_A]
        assert (
            board_a2.serial_listener.getsockname()[1],
            board_a2.flash_listener.getsockname()[1],
        ) == (6000, 6001), "departed board's ports should be reclaimed, not grown past 6003"

    def test_base_port_unset_binds_ephemeral_ports_without_error(self, make_supervisor):
        sup, adv, calls = make_supervisor(
            {_UID_A: {"uid": _UID_A, "board_name": "tovez"}}, base_port=0
        )
        sup._tick([{"uid": _UID_A}])
        board = sup.boards[_UID_A]
        assert board.serial_listener.getsockname()[1] > 0
        assert board.flash_listener.getsockname()[1] > 0
        assert board.serial_listener.getsockname()[1] != board.flash_listener.getsockname()[1]
