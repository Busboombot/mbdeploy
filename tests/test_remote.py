"""Tests for mbdeploy.remote: the SocketSerial adapter and resolve_board().

``SocketSerial`` is exercised two ways:

- Low-level unit tests against a real ``socket.socketpair()`` (same
  pattern ``tests/test_console_relay.py`` uses for `console.relay_socket`)
  -- readline reassembly across chunks, binary safety, ``in_waiting``
  never blocking, EOF handling, ``reset_input_buffer``.
- Integration tests against a real loopback TCP server on ``127.0.0.1``
  standing in for `server.py`'s `serve_serial` (a plain, unframed byte
  pipe once a client connects), driving `console.send_command()` (the
  one-shot path) and `console.interact()` (the interactive, ``in_waiting``
  -driven path) completely unchanged -- proving the adapter satisfies
  ``console.py``'s duck-typed contract for real, not just in isolation.

``resolve_board()`` is exercised against a fake ``mdns.browse()`` --
no real zeroconf traffic.
"""

from __future__ import annotations

import io
import re
import socket
import threading
import time

import pytest

import mbdeploy.console as console_mod
import mbdeploy.remote as remote_mod
from mbdeploy.remote import SocketSerial, resolve_board

SERVICE_TYPE = "_mbserial._tcp.local."


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# SocketSerial: low-level unit tests against a real socketpair()
# ---------------------------------------------------------------------------


class TestSocketSerialUnit:
    def test_readline_reassembles_a_line_split_across_two_recv_chunks(self):
        mine, theirs = socket.socketpair()
        try:
            ser = SocketSerial(mine, timeout=1.0)
            theirs.sendall(b"partial-")
            time.sleep(0.05)          # force two distinct recv()s, not one
            theirs.sendall(b"line\n")
            assert ser.readline() == b"partial-line\n"
        finally:
            mine.close()
            theirs.close()

    def test_readline_returns_multiple_lines_delivered_in_one_chunk(self):
        mine, theirs = socket.socketpair()
        try:
            ser = SocketSerial(mine, timeout=1.0)
            theirs.sendall(b"one\ntwo\n")
            assert ser.readline() == b"one\n"
            assert ser.readline() == b"two\n"
        finally:
            mine.close()
            theirs.close()

    def test_readline_returns_empty_bytes_on_timeout_with_no_data(self):
        mine, theirs = socket.socketpair()
        try:
            ser = SocketSerial(mine, timeout=0.05)
            start = time.time()
            assert ser.readline() == b""
            assert time.time() - start < 1.0
        finally:
            mine.close()
            theirs.close()

    def test_binary_safety_round_trip_via_read_and_in_waiting(self):
        """NUL bytes and high bytes must survive verbatim, both directions."""
        mine, theirs = socket.socketpair()
        try:
            ser = SocketSerial(mine, timeout=1.0)
            up = bytes([0x00, 0xFF, 0x80, 0x01, 0x00, 0x9C])
            down = bytes([0xDE, 0xAD, 0x00, 0xBE, 0xEF, 0xFF])

            theirs.sendall(up)
            assert _wait_until(lambda: ser.in_waiting == len(up))
            data = ser.read(len(up))
            assert data == up
            assert isinstance(data, bytes)

            ser.write(down)
            theirs.settimeout(2.0)
            assert theirs.recv(64) == down
        finally:
            mine.close()
            theirs.close()

    def test_in_waiting_never_blocks(self):
        mine, theirs = socket.socketpair()
        try:
            ser = SocketSerial(mine, timeout=5.0)
            start = time.time()
            assert ser.in_waiting == 0            # nothing sent: must not block
            assert time.time() - start < 0.5

            theirs.sendall(b"xyz")
            assert _wait_until(lambda: ser.in_waiting == 3)
            start = time.time()
            assert ser.in_waiting == 3             # already buffered: still no block
            assert time.time() - start < 0.5
            assert ser.read(3) == b"xyz"
        finally:
            mine.close()
            theirs.close()

    def test_eof_when_peer_closes_drains_then_reads_empty(self):
        mine, theirs = socket.socketpair()
        try:
            ser = SocketSerial(mine, timeout=0.2)
            theirs.sendall(b"tail")
            theirs.close()
            assert _wait_until(lambda: ser.in_waiting == 4)
            assert ser.read(10) == b"tail"        # fewer than requested: EOF, not a hang

            start = time.time()
            assert ser.read(1) == b""
            assert time.time() - start < 1.0
            assert ser.readline() == b""
        finally:
            mine.close()

    def test_reset_input_buffer_discards_stale_bytes(self):
        mine, theirs = socket.socketpair()
        try:
            ser = SocketSerial(mine, timeout=1.0)
            theirs.sendall(b"stale-data")
            assert _wait_until(lambda: ser.in_waiting > 0)
            ser.reset_input_buffer()
            assert ser.in_waiting == 0

            theirs.sendall(b"fresh\n")
            assert ser.readline() == b"fresh\n"
        finally:
            mine.close()
            theirs.close()

    def test_write_returns_byte_count_and_flush_is_a_harmless_noop(self):
        mine, theirs = socket.socketpair()
        try:
            ser = SocketSerial(mine, timeout=1.0)
            n = ser.write(b"hello")
            assert n == 5
            ser.flush()                            # must not raise
            theirs.settimeout(2.0)
            assert theirs.recv(64) == b"hello"
        finally:
            mine.close()
            theirs.close()


# ---------------------------------------------------------------------------
# A real loopback TCP server standing in for serve_serial's raw byte pipe
# ---------------------------------------------------------------------------


class LoopbackServer:
    """A real ``127.0.0.1`` TCP listener, one connection, byte pipe.

    Mirrors `server.py`'s `serve_serial`: once a client connects, it is a
    raw, unframed byte pipe -- so this stands in for it without importing
    `server.py` (this ticket's adapter has no dependency on the daemon).
    Received bytes are recorded; ``on_receive`` (if given) is invoked
    with ``(conn, chunk)`` after each recv, so a test can script a reply
    once it has seen enough of what the client sent.
    """

    def __init__(self, on_receive=None) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self.conn: socket.socket | None = None
        self.received = bytearray()
        self._on_receive = on_receive
        self._lock = threading.Lock()
        self._accepted = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        self._sock.settimeout(2.0)
        try:
            conn, _addr = self._sock.accept()
        except OSError:
            return
        self.conn = conn
        self._accepted.set()
        conn.settimeout(0.1)
        while not self._stop.is_set():
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            with self._lock:
                self.received.extend(chunk)
            if self._on_receive:
                self._on_receive(conn, chunk)

    def wait_for_connection(self, timeout: float = 2.0) -> None:
        assert self._accepted.wait(timeout), "client never connected"

    def wait_until_received(self, substring: bytes, timeout: float = 2.0) -> bytes:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if substring in self.received:
                    return bytes(self.received)
            time.sleep(0.01)
        with self._lock:
            return bytes(self.received)

    def close(self) -> None:
        self._stop.set()
        try:
            if self.conn is not None:
                self.conn.close()
        finally:
            self._sock.close()
        self._thread.join(timeout=2.0)


@pytest.fixture()
def loopback_server():
    server = LoopbackServer()
    yield server
    server.close()


def _connect(server: LoopbackServer, timeout: float = 0.1) -> SocketSerial:
    server.start()
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
    server.wait_for_connection()
    return SocketSerial(sock, timeout=timeout)


class FakeStdin:
    """Scripted ``sys.stdin`` stand-in: a list of lines, then EOF (``""``)."""

    def __init__(self, lines) -> None:
        self._lines = list(lines)

    def readline(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        return ""


class FakeStdout:
    """Records everything written, like ``sys.stdout`` would."""

    def __init__(self) -> None:
        self._buf = io.StringIO()

    def write(self, text: str) -> None:
        self._buf.write(text)

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        return self._buf.getvalue()


class TestConsoleUnchangedAgainstSocketSerial:
    """console.py has zero lines changed -- these prove it doesn't need any."""

    def test_send_command_one_shot_exchange_returns_reply_lines(self, loopback_server):
        def on_receive(conn, chunk):
            if b"\n" in chunk:
                conn.sendall(b"OK line1\nOK line2\n")

        loopback_server._on_receive = on_receive
        ser = _connect(loopback_server)
        try:
            lines = console_mod.send_command(ser, "PING", timeout=2.0)
        finally:
            ser.close()

        assert lines == ["OK line1", "OK line2"]
        assert loopback_server.wait_until_received(b"PING\n") == b"PING\n"

    def test_send_command_calls_reset_input_buffer_first(self, loopback_server):
        """A stale reply sitting unread must not leak into the new command's lines."""

        def on_receive(conn, chunk):
            if b"\n" in chunk:
                conn.sendall(b"OK real-reply\n")

        loopback_server._on_receive = on_receive
        ser = _connect(loopback_server)
        try:
            # Stale bytes from "before" this call, sitting on the wire unread.
            loopback_server.wait_for_connection()
            loopback_server.conn.sendall(b"OK stale-reply\n")
            time.sleep(0.05)          # let it land before send_command runs

            lines = console_mod.send_command(ser, "PING", timeout=2.0)
        finally:
            ser.close()

        assert lines == ["OK real-reply"]

    def test_interact_uses_the_in_waiting_driven_read_path(
        self, loopback_server, monkeypatch
    ):
        """This is the path a missing/broken in_waiting would break silently."""
        ser = _connect(loopback_server)
        fake_in = FakeStdin(["PING\n"])
        fake_out = FakeStdout()
        monkeypatch.setattr(console_mod.sys, "stdin", fake_in)
        monkeypatch.setattr(console_mod.sys, "stdout", fake_out)
        # Generous relative to the send below: stdin hits EOF almost
        # immediately (one scripted line, then ""), so EOF_DRAIN is what
        # gives the reader thread room to poll in_waiting/read across
        # more than one iteration and drain the whole reply -- a single
        # short byte (e.g. just "b") landing on the first poll and the
        # rest arriving a moment later is a real, correct possibility,
        # not a bug; the loop must still be alive to pick up the rest.
        monkeypatch.setattr(console_mod, "EOF_DRAIN", 0.3)

        def push_reply():
            loopback_server.wait_for_connection()
            time.sleep(0.05)          # let interact()'s reader thread start polling
            loopback_server.conn.sendall(b"board says hi\n")

        pusher = threading.Thread(target=push_reply, daemon=True)
        pusher.start()
        try:
            rc = console_mod.interact(ser)
        finally:
            pusher.join(timeout=2.0)
            ser.close()

        assert rc == 0
        assert "board says hi" in fake_out.getvalue()
        assert loopback_server.wait_until_received(b"PING\n") == b"PING\n"

    def test_interact_relays_binary_board_output_without_readline(
        self, loopback_server, monkeypatch
    ):
        """A readline-only fake would never exercise this -- no trailing newline."""
        ser = _connect(loopback_server)
        fake_in = FakeStdin([])       # immediate EOF: no stdin to relay
        fake_out = FakeStdout()
        monkeypatch.setattr(console_mod.sys, "stdin", fake_in)
        monkeypatch.setattr(console_mod.sys, "stdout", fake_out)
        monkeypatch.setattr(console_mod, "EOF_DRAIN", 0.3)

        def push_reply():
            loopback_server.wait_for_connection()
            time.sleep(0.05)
            loopback_server.conn.sendall(b"no newline here")

        pusher = threading.Thread(target=push_reply, daemon=True)
        pusher.start()
        try:
            rc = console_mod.interact(ser)
        finally:
            pusher.join(timeout=2.0)
            ser.close()

        assert rc == 0
        assert "no newline here" in fake_out.getvalue()


# ---------------------------------------------------------------------------
# resolve_board()
# ---------------------------------------------------------------------------


def _browse_entry(short_name: str, host: str, port: int, txt=None) -> dict:
    return {
        "name": f"{short_name}.{SERVICE_TYPE}",
        "host": host,
        "port": port,
        "txt": txt
        if txt is not None
        else {"uid": "abc123", "role": "", "common_name": "", "enum": "0", "port": str(port)},
    }


class TestResolveBoard:
    def test_hit_returns_the_single_matching_dict(self, monkeypatch):
        entry = _browse_entry("togov", "192.168.1.149", 9001)
        monkeypatch.setattr(remote_mod.mdns, "browse", lambda *a, **k: [entry])

        result = resolve_board("togov", SERVICE_TYPE)

        assert result == {
            "name": "togov",
            "host": "192.168.1.149",
            "port": 9001,
            "txt": entry["txt"],
        }

    def test_miss_raises_value_error_naming_what_was_searched_for(self, monkeypatch):
        monkeypatch.setattr(remote_mod.mdns, "browse", lambda *a, **k: [])

        with pytest.raises(
            ValueError,
            match=re.escape(f"no board named 'togov' found advertising {SERVICE_TYPE}"),
        ):
            resolve_board("togov", SERVICE_TYPE)

    def test_multi_match_on_different_hosts_raises_and_lists_candidates(self, monkeypatch):
        entries = [
            _browse_entry("togov", "192.168.1.149", 9001),
            _browse_entry("togov", "192.168.1.150", 9002),
        ]
        monkeypatch.setattr(remote_mod.mdns, "browse", lambda *a, **k: entries)

        with pytest.raises(ValueError) as excinfo:
            resolve_board("togov", SERVICE_TYPE)

        message = str(excinfo.value)
        assert "multiple boards named 'togov'" in message
        assert "192.168.1.149:9001" in message
        assert "192.168.1.150:9002" in message

    def test_zeroconf_rename_suffix_still_counts_as_the_same_board_name(self, monkeypatch):
        """A genuine two-boards-one-name collision: zeroconf renames the
        second registration to "togov (2)" -- resolve_board must not
        silently match only the first and miss the collision entirely.
        """
        entries = [
            _browse_entry("togov", "192.168.1.149", 9001),
            _browse_entry("togov (2)", "192.168.1.150", 9002),
        ]
        monkeypatch.setattr(remote_mod.mdns, "browse", lambda *a, **k: entries)

        with pytest.raises(ValueError, match="multiple boards named 'togov'"):
            resolve_board("togov", SERVICE_TYPE)

    def test_short_name_extraction_does_not_over_match_unrelated_names(self, monkeypatch):
        entries = [
            _browse_entry("togov", "192.168.1.149", 9001),
            _browse_entry("other", "192.168.1.150", 9002),
        ]
        monkeypatch.setattr(remote_mod.mdns, "browse", lambda *a, **k: entries)

        result = resolve_board("other", SERVICE_TYPE)

        assert result["name"] == "other"
        assert result["host"] == "192.168.1.150"
        assert result["port"] == 9002

    def test_missing_txt_fields_do_not_crash_and_pass_through(self, monkeypatch):
        entry = _browse_entry("togov", "192.168.1.149", 9001, txt={})
        monkeypatch.setattr(remote_mod.mdns, "browse", lambda *a, **k: [entry])

        result = resolve_board("togov", SERVICE_TYPE)

        assert result["txt"] == {}

    def test_browse_is_called_with_the_given_service_type_and_timeout(self, monkeypatch):
        calls = []

        def fake_browse(service_type, timeout):
            calls.append((service_type, timeout))
            return [_browse_entry("togov", "192.168.1.149", 9001)]

        monkeypatch.setattr(remote_mod.mdns, "browse", fake_browse)

        resolve_board("togov", SERVICE_TYPE, timeout=5.0)

        assert calls == [(SERVICE_TYPE, 5.0)]
