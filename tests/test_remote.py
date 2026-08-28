"""Tests for mbdeploy.remote: the SocketSerial adapter, resolve_board(),
and list_remote() -- plus `list --remote`'s wiring into `cli.py`.

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

``resolve_board()`` and ``list_remote()`` are both exercised against a
fake ``mdns.browse()`` -- no real zeroconf traffic. `cli.py`'s `_cmd_list`
`--remote` branch is exercised against a fake ``remote.list_remote()``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import socket
import threading
import time

import pytest

import mbdeploy.cli as cli_mod
import mbdeploy.console as console_mod
import mbdeploy.remote as remote_mod
from mbdeploy.remote import SocketSerial, resolve_board

SERVICE_TYPE = "_mbserial._tcp.local."
FLASH_SERVICE_TYPE = "_mbflash._tcp.local."


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

    def __init__(self, on_receive=None, immediate_send: bytes | None = None) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self.conn: socket.socket | None = None
        self.received = bytearray()
        self._on_receive = on_receive
        # Sent immediately on accept, then the connection is closed without
        # ever entering the recv loop -- stands in for `serve_serial`'s own
        # pre-relay `ERR ...` line (e.g. `ERR busy`), which is always
        # followed by the daemon closing the connection.
        self._immediate_send = immediate_send
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
        if self._immediate_send is not None:
            try:
                conn.sendall(self._immediate_send)
            finally:
                conn.close()
            return
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


def _browse_entry(
    short_name: str, host: str, port: int, txt=None, service_type: str = SERVICE_TYPE
) -> dict:
    return {
        "name": f"{short_name}.{service_type}",
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


# ---------------------------------------------------------------------------
# list_remote()
# ---------------------------------------------------------------------------


def _browse_by_type(**by_type):
    """Build a fake ``mdns.browse`` returning ``by_type[service_type]``."""

    def fake_browse(service_type, timeout):
        return by_type.get(service_type, [])

    return fake_browse


class TestListRemote:
    def test_joins_both_service_types_into_one_row_per_board(self, monkeypatch):
        """A board advertising on both service types must be one row, not two."""
        uid = "u-1"
        txt_common = {
            "uid": uid, "role": "", "common_name": "Bot A", "enum": "3",
        }
        serial = [_browse_entry(
            "togov", "192.168.1.10", 9001,
            txt={**txt_common, "port": "9001"},
        )]
        flash = [_browse_entry(
            "togov", "192.168.1.10", 9101,
            txt={**txt_common, "port": "9101"},
            service_type=FLASH_SERVICE_TYPE,
        )]
        monkeypatch.setattr(
            remote_mod.mdns, "browse",
            _browse_by_type(**{SERVICE_TYPE: serial, FLASH_SERVICE_TYPE: flash}),
        )

        rows = remote_mod.list_remote()

        assert rows == [{
            "enum": "3",
            "name": "togov",
            "common": "Bot A",
            "role": "",
            "uid": uid,
            "host": "192.168.1.10",
        }]

    def test_board_on_only_one_service_type_still_gets_exactly_one_row(self, monkeypatch):
        """serve's flash listener hasn't come up yet (or the daemon only
        exposes one service) -- still exactly one row, not zero."""
        uid = "solo-1"
        serial = [_browse_entry(
            "solob", "192.168.1.20", 9002,
            txt={"uid": uid, "role": "relay", "common_name": "", "enum": "1", "port": "9002"},
        )]
        monkeypatch.setattr(
            remote_mod.mdns, "browse", _browse_by_type(**{SERVICE_TYPE: serial}),
        )

        rows = remote_mod.list_remote()

        assert rows == [{
            "enum": "1",
            "name": "solob",
            "common": "",
            "role": "relay",
            "uid": uid,
            "host": "192.168.1.20",
        }]

    def test_four_boards_across_four_hosts_each_get_their_own_row(self, monkeypatch):
        """The real-world Nolanet shape: 4 nodes, 4 boards, 4 distinct hosts."""
        boards = [
            ("magni-board", "192.168.1.101", "u-magni"),
            ("hodr-board", "192.168.1.102", "u-hodr"),
            ("loki-board", "192.168.1.103", "u-loki"),
            ("meili-board", "192.168.1.104", "u-meili"),
        ]
        serial, flash = [], []
        for name, host, uid in boards:
            txt = {"uid": uid, "role": "", "common_name": "", "enum": "0"}
            serial.append(_browse_entry(name, host, 9001, txt={**txt, "port": "9001"}))
            flash.append(_browse_entry(
                name, host, 9101, txt={**txt, "port": "9101"},
                service_type=FLASH_SERVICE_TYPE,
            ))
        monkeypatch.setattr(
            remote_mod.mdns, "browse",
            _browse_by_type(**{SERVICE_TYPE: serial, FLASH_SERVICE_TYPE: flash}),
        )

        rows = remote_mod.list_remote()

        assert len(rows) == 4
        assert {row["host"] for row in rows} == {b[1] for b in boards}
        assert {row["name"] for row in rows} == {b[0] for b in boards}
        assert {row["uid"] for row in rows} == {b[2] for b in boards}

    def test_empty_browse_on_both_service_types_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(remote_mod.mdns, "browse", lambda *a, **k: [])

        assert remote_mod.list_remote() == []

    def test_missing_txt_fields_produce_blank_strings_not_a_crash(self, monkeypatch):
        entry = _browse_entry("bareb", "192.168.1.55", 9003, txt={})
        monkeypatch.setattr(
            remote_mod.mdns, "browse", _browse_by_type(**{SERVICE_TYPE: [entry]}),
        )

        rows = remote_mod.list_remote()

        assert rows == [{
            "enum": "", "name": "bareb", "common": "", "role": "", "uid": "",
            "host": "192.168.1.55",
        }]

    def test_uid_missing_falls_back_to_grouping_by_short_name(self, monkeypatch):
        """No TXT uid at all on either registration: still one row, not two,
        keyed by the recovered short name instead."""
        serial = [_browse_entry("nouid", "192.168.1.66", 9004, txt={})]
        flash = [_browse_entry(
            "nouid", "192.168.1.66", 9104, txt={}, service_type=FLASH_SERVICE_TYPE,
        )]
        monkeypatch.setattr(
            remote_mod.mdns, "browse",
            _browse_by_type(**{SERVICE_TYPE: serial, FLASH_SERVICE_TYPE: flash}),
        )

        rows = remote_mod.list_remote()

        assert len(rows) == 1
        assert rows[0]["name"] == "nouid"

    def test_browse_called_for_both_service_types_with_the_given_timeout(self, monkeypatch):
        calls = []

        def fake_browse(service_type, timeout):
            calls.append((service_type, timeout))
            return []

        monkeypatch.setattr(remote_mod.mdns, "browse", fake_browse)

        remote_mod.list_remote(timeout=5.0)

        assert calls == [(SERVICE_TYPE, 5.0), (FLASH_SERVICE_TYPE, 5.0)]


# ---------------------------------------------------------------------------
# `list --remote` wiring: cli._cmd_list's remote branch
# ---------------------------------------------------------------------------


def _list_args(**overrides) -> argparse.Namespace:
    defaults = dict(remote=True, config=None, fast=False, target_mcu="nrf52833")
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestCmdListRemote:
    def test_prints_table_with_host_column_matching_each_row(self, monkeypatch, capsys):
        rows = [
            {"enum": "0", "name": "togov", "common": "", "role": "",
             "uid": "u1", "host": "192.168.1.10"},
            {"enum": "1", "name": "solob", "common": "Relay1", "role": "relay",
             "uid": "u2", "host": "192.168.1.20"},
        ]
        monkeypatch.setattr(remote_mod, "list_remote", lambda timeout=2.0: rows)

        rc = cli_mod._cmd_list(_list_args())

        assert rc == 0
        out = capsys.readouterr().out
        assert "HOST" in out
        assert "CONN" not in out
        assert "PORT" not in out
        for row in rows:
            line = next(line for line in out.splitlines() if row["uid"] in line)
            assert row["host"] in line
            assert row["name"] in line

    def test_empty_remote_result_prints_an_empty_table_and_exits_zero(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(remote_mod, "list_remote", lambda timeout=2.0: [])

        rc = cli_mod._cmd_list(_list_args())

        assert rc == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "HOST" in lines[0]
        assert lines[1] == "-" * len(lines[0])
        assert len(lines) == 2                    # header + rule, zero data rows
        assert "no devices found" not in out

    def test_fast_and_target_mcu_are_ignored_not_rejected(self, monkeypatch, capsys):
        """--remote --fast must not error, and must not touch the debug probe
        (list_remote() never reads a board name over SWD in the first place)."""
        rows = [{"enum": "0", "name": "togov", "common": "", "role": "",
                 "uid": "u1", "host": "192.168.1.10"}]
        monkeypatch.setattr(remote_mod, "list_remote", lambda timeout=2.0: rows)

        rc = cli_mod._cmd_list(_list_args(fast=True, target_mcu="nrf52840"))

        assert rc == 0
        assert "togov" in capsys.readouterr().out

    def test_does_not_touch_local_devices_module(self, monkeypatch, capsys):
        """--remote must never fall through to the local USB/registry path."""
        import mbdeploy.devices as devices_mod

        def _boom(*a, **k):
            raise AssertionError("list --remote must not probe local USB devices")

        monkeypatch.setattr(devices_mod, "flashable_probes", _boom)
        monkeypatch.setattr(devices_mod, "load_devices", _boom)
        monkeypatch.setattr(remote_mod, "list_remote", lambda timeout=2.0: [])

        rc = cli_mod._cmd_list(_list_args())

        assert rc == 0


class TestListRemoteArgparse:
    def test_remote_flag_parses_and_defaults_to_false(self):
        parser = cli_mod._build_parser()

        assert parser.parse_args(["list"]).remote is False
        assert parser.parse_args(["list", "--remote"]).remote is True

    def test_remote_is_documented_in_list_help(self, capsys):
        parser = cli_mod._build_parser()

        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["list", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--remote" in out


class TestPrintDeviceTableLocalOutputUnchanged:
    """Guards the ticket's own acceptance criterion: local `list` output
    must be byte-for-byte unchanged now that `_print_device_table` takes
    an optional `remote` parameter."""

    def test_default_call_is_identical_to_an_explicit_remote_false(self, capsys):
        rows = [{
            "enum": "0", "conn": "yes", "name": "togov", "common": "",
            "role": "", "port": "/dev/cu.usbmodem1", "uid": "u1",
        }]

        cli_mod._print_device_table(rows)
        default_out = capsys.readouterr().out

        cli_mod._print_device_table(rows, remote=False)
        explicit_out = capsys.readouterr().out

        assert default_out == explicit_out
        assert "CONN" in default_out
        assert "PORT" in default_out
        assert "HOST" not in default_out


# ---------------------------------------------------------------------------
# `connect --remote` wiring: cli._cmd_connect's remote branch
#
# Ticket 004. Both `console.send_command` (one-shot) and `console.interact`
# (interactive, in_waiting-driven) run against `remote.SocketSerial` through
# the actual `_cmd_connect` handler here, against a real loopback socket
# standing in for `serve_serial` -- not just the adapter in isolation
# (that's TestConsoleUnchangedAgainstSocketSerial above).
# ---------------------------------------------------------------------------


def _connect_remote_args(
    target, message=(), remote=True, baud=115200, timeout=1.0, config=None
) -> argparse.Namespace:
    return argparse.Namespace(
        target=target, message=list(message), remote=remote,
        baud=baud, timeout=timeout, config=config,
    )


class TestConnectRemoteArgparse:
    def test_remote_flag_parses_and_defaults_to_false(self):
        parser = cli_mod._build_parser()

        assert parser.parse_args(["connect", "togov"]).remote is False
        assert parser.parse_args(["connect", "--remote", "togov"]).remote is True

    def test_remote_is_documented_in_connect_help(self, capsys):
        parser = cli_mod._build_parser()

        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["connect", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--remote" in out
        assert "ignored" in out.lower()  # --baud's note about --remote

    def test_remote_and_baud_combine_at_parse_time_without_error(self):
        """argparse itself never rejects --remote+--baud -- --baud is simply
        ignored by the handler, so the parser has no reason to refuse it."""
        parser = cli_mod._build_parser()

        args = parser.parse_args(["connect", "--remote", "--baud", "9600", "togov"])

        assert args.remote is True
        assert args.baud == 9600


class TestCmdConnectRemoteRejectsDevicePath:
    """Acceptance criterion: rejected before any mDNS lookup or socket I/O."""

    def test_dev_path_is_rejected_before_touching_mdns(self, monkeypatch, capsys):
        def _boom(*a, **k):
            raise AssertionError("must not touch mdns.browse for a /dev/ target")

        monkeypatch.setattr(remote_mod.mdns, "browse", _boom)

        rc = cli_mod._cmd_connect(_connect_remote_args("/dev/ttyACM0", ["HELLO"]))

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert (
            "--remote cannot be combined with a device path" in captured.err
        )
        assert "/dev/ttyACM0" in captured.err

    def test_any_slash_containing_target_is_rejected_too(self, monkeypatch, capsys):
        """Matches `_connect_port`'s own existing test: a bare '/' counts,
        not only a literal /dev/ prefix."""

        def _boom(*a, **k):
            raise AssertionError("must not touch mdns.browse for a path-like target")

        monkeypatch.setattr(remote_mod.mdns, "browse", _boom)

        rc = cli_mod._cmd_connect(_connect_remote_args("some/path", []))

        assert rc == 1
        assert "--remote cannot be combined" in capsys.readouterr().err

    def test_local_connect_with_a_dev_path_is_unaffected(self, monkeypatch):
        """Without --remote, a /dev/... target is still opened verbatim --
        this ticket must not touch that behavior."""
        assert cli_mod._connect_port("/dev/cu.usbmodem99", {}) == "/dev/cu.usbmodem99"


class TestCmdConnectRemote:
    """`_cmd_connect`'s --remote branch, against a real loopback TCP server
    standing in for `serve_serial`."""

    @pytest.fixture(autouse=True)
    def _fast_err_peek(self, monkeypatch):
        # The ERR-busy peek defaults to 0.3s; tests don't need to pay that
        # unless they are specifically exercising the busy path.
        monkeypatch.setattr(cli_mod, "_REMOTE_ERR_PEEK_TIMEOUT", 0.05)

    def _patch_resolve(self, monkeypatch, server: LoopbackServer, name="togov"):
        monkeypatch.setattr(
            remote_mod, "resolve_board",
            lambda target, service_type, timeout=2.0: {
                "name": name, "host": "127.0.0.1", "port": server.port, "txt": {},
            },
        )

    def test_one_shot_exchange_returns_reply_on_stdout_and_exits_zero(
        self, monkeypatch, capsys
    ):
        def on_receive(conn, chunk):
            if b"\n" in chunk:
                conn.sendall(b"PONG\n")

        server = LoopbackServer(on_receive=on_receive)
        server.start()
        try:
            self._patch_resolve(monkeypatch, server)
            rc = cli_mod._cmd_connect(
                _connect_remote_args("togov", ["HELLO"], timeout=2.0)
            )
        finally:
            server.close()

        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out == "PONG\n"          # reply only -- no banner on stdout
        assert server.wait_until_received(b"HELLO\n") == b"HELLO\n"

    def test_silent_board_exits_one_with_no_response_on_stderr(
        self, monkeypatch, capsys
    ):
        server = LoopbackServer()
        server.start()
        try:
            self._patch_resolve(monkeypatch, server)
            rc = cli_mod._cmd_connect(
                _connect_remote_args("togov", ["HELLO"], timeout=0.3)
            )
        finally:
            server.close()

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "no response" in captured.err

    def test_interactive_path_relays_both_directions_via_in_waiting(
        self, monkeypatch
    ):
        """The path a missing/broken in_waiting would break silently --
        driven here through the real _cmd_connect handler, not just the
        adapter directly."""
        server = LoopbackServer()
        server.start()
        fake_in = FakeStdin(["PING\n"])
        fake_out = FakeStdout()
        monkeypatch.setattr(console_mod.sys, "stdin", fake_in)
        monkeypatch.setattr(console_mod.sys, "stdout", fake_out)
        monkeypatch.setattr(console_mod, "EOF_DRAIN", 0.3)

        def push_reply():
            server.wait_for_connection()
            time.sleep(0.05)             # let the reader thread start polling
            server.conn.sendall(b"board says hi\n")

        pusher = threading.Thread(target=push_reply, daemon=True)
        pusher.start()
        try:
            self._patch_resolve(monkeypatch, server)
            rc = cli_mod._cmd_connect(_connect_remote_args("togov", [], timeout=1.0))
        finally:
            pusher.join(timeout=2.0)
            server.close()

        assert rc == 0
        assert "board says hi" in fake_out.getvalue()
        assert server.wait_until_received(b"PING\n") == b"PING\n"

    def test_resolve_board_failure_surfaces_as_error_and_exit_one(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            remote_mod, "resolve_board",
            lambda *a, **k: (_ for _ in ()).throw(
                ValueError(
                    "no board named 'nope' found advertising "
                    f"{SERVICE_TYPE}"
                )
            ),
        )

        rc = cli_mod._cmd_connect(_connect_remote_args("nope", ["HELLO"]))

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("Error: ")
        assert "no board named 'nope'" in captured.err

    def test_err_busy_surfaces_cleanly_as_error_not_garbage_reply(
        self, monkeypatch, capsys
    ):
        server = LoopbackServer(immediate_send=b"ERR busy\n")
        server.start()
        try:
            self._patch_resolve(monkeypatch, server)
            rc = cli_mod._cmd_connect(
                _connect_remote_args("togov", ["HELLO"], timeout=1.0)
            )
        finally:
            server.close()

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""                # never leaks into the reply
        assert "busy" in captured.err

    def test_err_busy_surfaces_cleanly_on_the_interactive_path_too(
        self, monkeypatch, capsys
    ):
        server = LoopbackServer(immediate_send=b"ERR busy\n")
        server.start()
        try:
            self._patch_resolve(monkeypatch, server)
            rc = cli_mod._cmd_connect(_connect_remote_args("togov", [], timeout=1.0))
        finally:
            server.close()

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "busy" in captured.err

    def test_connection_refused_surfaces_as_error_and_exit_one(
        self, monkeypatch, capsys
    ):
        """No listener at all on the resolved host:port -- resolve_board
        found a stale/incorrect mDNS entry, or the daemon just went down."""
        closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()                  # nothing listening on `port` now

        monkeypatch.setattr(
            remote_mod, "resolve_board",
            lambda *a, **k: {
                "name": "togov", "host": "127.0.0.1", "port": port, "txt": {},
            },
        )

        rc = cli_mod._cmd_connect(_connect_remote_args("togov", ["HELLO"]))

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Error: cannot connect to" in captured.err


# ---------------------------------------------------------------------------
# deploy_over_network() -- the FLASH client protocol
#
# Ticket 005. Two layers, per the ticket's own testing plan:
#
# - `ScriptedFlashServer`: a real loopback TCP listener scripted to play
#   `serve_flash`'s side of the wire protocol for exactly one connection --
#   used to drive `deploy_over_network` through the success path, each
#   named `ERR`, and the force-relay/sha256 header fields.
# - `TestDeployOverNetworkAgainstRealServeFlash`: the *actual*
#   `server.serve_flash` handler (only `flash_hex` stubbed) on the other
#   end of the same loopback socket -- proves both sides agree on the wire
#   format, which two independently written fakes could not.
# ---------------------------------------------------------------------------


def _srv_read_line(conn: socket.socket, timeout: float = 2.0) -> bytes:
    """Server-side line read for a `ScriptedFlashServer` handler: one
    `\\n`-terminated line, without the newline. Mirrors the byte-at-a-time
    style `server.py::_read_line` uses, for the same reason -- nothing
    read here may swallow bytes belonging to the payload that follows."""
    conn.settimeout(timeout)
    buf = bytearray()
    while True:
        b = conn.recv(1)
        if not b or b == b"\n":
            return bytes(buf)
        buf.extend(b)


def _srv_read_exact(conn: socket.socket, n: int, timeout: float = 2.0) -> bytes:
    """Server-side read of up to exactly `n` bytes, or fewer on EOF."""
    conn.settimeout(timeout)
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(min(4096, n - len(buf)))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


class ScriptedFlashServer:
    """A real ``127.0.0.1`` TCP listener that runs ``handler(conn)`` for
    exactly one accepted connection, in a background thread.

    ``handler`` owns the entire exchange -- reading the `FLASH` header
    line (and, if it chooses, the declared payload), then writing back
    whatever `serve_flash`-shaped response sequence the test wants to
    exercise `remote.deploy_over_network` against. Any exception raised
    inside ``handler`` is captured and re-raised from :meth:`close`, so a
    broken scripted handler fails the test loudly instead of just letting
    the client hang or time out with no explanation.
    """

    def __init__(self, handler) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._handler = handler
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        self._sock.settimeout(2.0)
        try:
            conn, _addr = self._sock.accept()
        except OSError:
            return
        try:
            self._handler(conn)
        except BaseException as exc:  # noqa: BLE001 -- surfaced by close()
            self.error = exc
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)
        if self.error is not None:
            raise self.error


def _patch_resolve_to(monkeypatch, server: ScriptedFlashServer, name: str = "togov") -> None:
    monkeypatch.setattr(
        remote_mod, "resolve_board",
        lambda target, service_type, timeout=2.0: {
            "name": name, "host": "127.0.0.1", "port": server.port, "txt": {},
        },
    )


class TestDeployOverNetworkWireProtocol:
    def test_success_path_sends_correct_header_relays_log_and_returns_zero(
        self, tmp_path, monkeypatch, capsys
    ):
        payload = b":10000000FF00112233\n:00000001FF\n"
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(payload)
        expected_digest = hashlib.sha256(payload).hexdigest()
        seen = {}

        def handler(conn):
            header = _srv_read_line(conn).decode()
            seen["header"] = header
            conn.sendall(b"OK send\n")
            m = re.match(r"FLASH (\d+)", header)
            seen["payload"] = _srv_read_exact(conn, int(m.group(1)))
            conn.sendall(b"LOG erasing\n")
            conn.sendall(b"LOG programming\n")
            conn.sendall(b"OK flashed\n")

        server = ScriptedFlashServer(handler)
        server.start()
        try:
            _patch_resolve_to(monkeypatch, server)
            rc = remote_mod.deploy_over_network("togov", str(hex_path), "nrf52833")
        finally:
            server.close()

        assert rc == 0
        assert seen["header"] == f"FLASH {len(payload)} sha256={expected_digest}"
        assert seen["payload"] == payload           # exact byte count, exact bytes
        err = capsys.readouterr().err
        assert "erasing" in err
        assert "programming" in err

    def test_force_relay_token_present_only_when_flag_is_true(
        self, tmp_path, monkeypatch
    ):
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(b"payload-bytes")
        headers = []

        def handler(conn):
            headers.append(_srv_read_line(conn).decode())
            conn.sendall(b"OK send\n")
            _srv_read_exact(conn, len(b"payload-bytes"))
            conn.sendall(b"OK flashed\n")

        server = ScriptedFlashServer(handler)
        server.start()
        try:
            _patch_resolve_to(monkeypatch, server)
            rc = remote_mod.deploy_over_network(
                "togov", str(hex_path), "nrf52833", force_relay=True,
            )
        finally:
            server.close()

        assert rc == 0
        assert headers[0].endswith(" force-relay")

    def test_force_relay_token_absent_by_default(self, tmp_path, monkeypatch):
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(b"payload-bytes")
        headers = []

        def handler(conn):
            headers.append(_srv_read_line(conn).decode())
            conn.sendall(b"OK send\n")
            _srv_read_exact(conn, len(b"payload-bytes"))
            conn.sendall(b"OK flashed\n")

        server = ScriptedFlashServer(handler)
        server.start()
        try:
            _patch_resolve_to(monkeypatch, server)
            rc = remote_mod.deploy_over_network("togov", str(hex_path), "nrf52833")
        finally:
            server.close()

        assert rc == 0
        assert "force-relay" not in headers[0]


class TestDeployOverNetworkErrCases:
    """Each of `serve_flash`'s named `ERR` lines, verbatim, must map to a
    non-zero exit with the message visible on stderr."""

    @pytest.mark.parametrize(
        "err_line",
        [
            "ERR busy",
            "ERR relay refused — send force-relay",
            "ERR flash disabled",
            "ERR auth required",
        ],
    )
    def test_immediate_err_instead_of_ok_send(
        self, tmp_path, monkeypatch, capsys, err_line
    ):
        """These are all responses `serve_flash` can send *instead of*
        `OK send`, before ever reading a payload."""
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(b"payload-bytes")

        def handler(conn):
            _srv_read_line(conn)
            conn.sendall((err_line + "\n").encode())

        server = ScriptedFlashServer(handler)
        server.start()
        try:
            _patch_resolve_to(monkeypatch, server)
            rc = remote_mod.deploy_over_network("togov", str(hex_path), "nrf52833")
        finally:
            server.close()

        assert rc != 0
        err = capsys.readouterr().err
        assert err_line[len("ERR "):] in err

    def test_sha256_mismatch_after_payload_sent(self, tmp_path, monkeypatch, capsys):
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(b"payload-bytes")

        def handler(conn):
            header = _srv_read_line(conn).decode()
            conn.sendall(b"OK send\n")
            m = re.match(r"FLASH (\d+)", header)
            _srv_read_exact(conn, int(m.group(1)))
            conn.sendall(b"ERR sha256 mismatch\n")

        server = ScriptedFlashServer(handler)
        server.start()
        try:
            _patch_resolve_to(monkeypatch, server)
            rc = remote_mod.deploy_over_network("togov", str(hex_path), "nrf52833")
        finally:
            server.close()

        assert rc != 0
        assert "sha256 mismatch" in capsys.readouterr().err

    def test_short_payload_after_payload_sent(self, tmp_path, monkeypatch, capsys):
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(b"payload-bytes")

        def handler(conn):
            _srv_read_line(conn)
            conn.sendall(b"OK send\n")
            _srv_read_exact(conn, 4)     # deliberately reads less than declared
            conn.sendall(b"ERR short payload\n")

        server = ScriptedFlashServer(handler)
        server.start()
        try:
            _patch_resolve_to(monkeypatch, server)
            rc = remote_mod.deploy_over_network("togov", str(hex_path), "nrf52833")
        finally:
            server.close()

        assert rc != 0
        assert "short payload" in capsys.readouterr().err

    def test_flash_failed_terminal_line_after_log_lines(
        self, tmp_path, monkeypatch, capsys
    ):
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(b"payload-bytes")

        def handler(conn):
            header = _srv_read_line(conn).decode()
            conn.sendall(b"OK send\n")
            m = re.match(r"FLASH (\d+)", header)
            _srv_read_exact(conn, int(m.group(1)))
            conn.sendall(b"LOG erasing\n")
            conn.sendall(b"ERR flash failed (exit 3)\n")

        server = ScriptedFlashServer(handler)
        server.start()
        try:
            _patch_resolve_to(monkeypatch, server)
            rc = remote_mod.deploy_over_network("togov", str(hex_path), "nrf52833")
        finally:
            server.close()

        assert rc != 0
        captured = capsys.readouterr()
        assert "erasing" in captured.err
        assert "flash failed (exit 3)" in captured.err

    def test_resolution_failure_errors_clearly_without_touching_a_socket(
        self, monkeypatch, capsys
    ):
        def _boom_resolve(*a, **k):
            raise ValueError("no board named 'nope' found advertising _mbflash._tcp.local.")

        def _boom_connect(*a, **k):
            raise AssertionError("must not open a socket after resolve_board fails")

        monkeypatch.setattr(remote_mod, "resolve_board", _boom_resolve)
        monkeypatch.setattr(remote_mod.socket, "create_connection", _boom_connect)

        rc = remote_mod.deploy_over_network("nope", "MICROBIT.hex", "nrf52833")

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("Error: ")
        assert "no board named 'nope'" in captured.err

    def test_missing_hex_file_errors_clearly_without_touching_a_socket(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            remote_mod, "resolve_board",
            lambda *a, **k: {"name": "togov", "host": "127.0.0.1", "port": 1, "txt": {}},
        )

        def _boom_connect(*a, **k):
            raise AssertionError("must not open a socket for an unreadable hex file")

        monkeypatch.setattr(remote_mod.socket, "create_connection", _boom_connect)

        missing = tmp_path / "does-not-exist.hex"
        rc = remote_mod.deploy_over_network("togov", str(missing), "nrf52833")

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.err.startswith("Error: ")
        assert "does-not-exist.hex" in captured.err

    def test_connection_refused_errors_clearly(self, tmp_path, monkeypatch, capsys):
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(b"payload-bytes")
        closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()                  # nothing listening on `port` now

        monkeypatch.setattr(
            remote_mod, "resolve_board",
            lambda *a, **k: {"name": "togov", "host": "127.0.0.1", "port": port, "txt": {}},
        )

        rc = remote_mod.deploy_over_network("togov", str(hex_path), "nrf52833")

        assert rc == 1
        assert "Error: cannot connect to" in capsys.readouterr().err

    def test_stalled_connection_times_out_rather_than_hanging(
        self, tmp_path, monkeypatch, capsys
    ):
        """A truncated exchange (server accepts, replies OK send, reads the
        payload, then goes silent forever) must not hang this test --
        bounded by `_FLASH_READ_TIMEOUT`, monkeypatched small here."""
        monkeypatch.setattr(remote_mod, "_FLASH_READ_TIMEOUT", 0.2)
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(b"payload-bytes")

        def handler(conn):
            header = _srv_read_line(conn).decode()
            conn.sendall(b"OK send\n")
            m = re.match(r"FLASH (\d+)", header)
            _srv_read_exact(conn, int(m.group(1)))
            time.sleep(2.0)              # far longer than the read timeout

        server = ScriptedFlashServer(handler)
        server.start()
        try:
            _patch_resolve_to(monkeypatch, server)
            start = time.time()
            rc = remote_mod.deploy_over_network("togov", str(hex_path), "nrf52833")
            elapsed = time.time() - start
        finally:
            server.close()

        assert rc != 0
        assert elapsed < 2.0
        assert "no response" in capsys.readouterr().err.lower()


class TestDeployOverNetworkAgainstRealServeFlash:
    """Drives `deploy_over_network` against `server.py`'s actual
    `serve_flash` handler -- only `flash_hex` is stubbed. Proves the
    client and server sides agree on the wire format, which two
    independently written fakes could not."""

    def test_success_round_trip_against_real_serve_flash(
        self, tmp_path, monkeypatch, capsys
    ):
        import mbdeploy.server as server_mod

        payload = b":10000000FF00112233\n:00000001FF\n"
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(payload)

        def fake_flash_hex(uid, hp, target_mcu, log=None):
            with open(hp, "rb") as f:
                written = f.read()
            assert written == payload
            if log is not None:
                log("erasing")
                log("programming")
            return 0

        monkeypatch.setattr(server_mod, "flash_hex", fake_flash_hex)

        board = server_mod.Board(
            "uid-real-1", "togov", {"uid": "uid-real-1", "port": "/dev/fake0", "role": ""}
        )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def _run():
            listener.settimeout(2.0)
            try:
                conn, _addr = listener.accept()
            except OSError:
                return
            try:
                server_mod.serve_flash(board, conn)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        monkeypatch.setattr(
            remote_mod, "resolve_board",
            lambda name, service_type, timeout=2.0: {
                "name": "togov", "host": "127.0.0.1", "port": port, "txt": {},
            },
        )

        try:
            rc = remote_mod.deploy_over_network("togov", str(hex_path), "nrf52833")
        finally:
            thread.join(timeout=2.0)
            listener.close()

        assert rc == 0
        err = capsys.readouterr().err
        assert "erasing" in err
        assert "programming" in err

    def test_relay_guard_refusal_against_real_serve_flash(
        self, tmp_path, monkeypatch, capsys
    ):
        import mbdeploy.server as server_mod

        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(b"payload-bytes")

        board = server_mod.Board(
            "uid-real-2", "solob",
            {"uid": "uid-real-2", "port": "/dev/fake1", "role": "RADIOBRIDGE"},
        )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def _run():
            listener.settimeout(2.0)
            try:
                conn, _addr = listener.accept()
            except OSError:
                return
            try:
                server_mod.serve_flash(board, conn)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        monkeypatch.setattr(
            remote_mod, "resolve_board",
            lambda name, service_type, timeout=2.0: {
                "name": "solob", "host": "127.0.0.1", "port": port, "txt": {},
            },
        )

        try:
            rc = remote_mod.deploy_over_network("solob", str(hex_path), "nrf52833")
        finally:
            thread.join(timeout=2.0)
            listener.close()

        assert rc != 0
        assert "relay refused" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# `deploy --remote` wiring: cli._cmd_deploy's remote branch
#
# Ticket 005.
# ---------------------------------------------------------------------------


def _deploy_remote_args(
    target=None, remote=True, build=False, clean=False, jobs=None,
    force_relay=False, hex_path=None, target_mcu="nrf52833", config=None,
    verbose=False,
) -> argparse.Namespace:
    return argparse.Namespace(
        target=target, remote=remote, build=build, clean=clean, jobs=jobs,
        force_relay=force_relay, hex=hex_path, target_mcu=target_mcu,
        config=config, verbose=verbose,
    )


class TestDeployRemoteArgparse:
    def test_remote_flag_parses_and_defaults_to_false(self):
        parser = cli_mod._build_parser()

        assert parser.parse_args(["deploy"]).remote is False
        assert parser.parse_args(["deploy", "togov", "--remote"]).remote is True

    def test_remote_is_documented_in_deploy_help(self, capsys):
        parser = cli_mod._build_parser()

        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["deploy", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--remote" in out


class TestCmdDeployRemoteRejections:
    """Acceptance criterion: both rejections fire before any network I/O."""

    def test_dev_path_target_is_rejected_before_touching_mdns_or_network(
        self, monkeypatch, capsys
    ):
        def _boom(*a, **k):
            raise AssertionError("must not touch the network for a /dev/ target")

        monkeypatch.setattr(remote_mod.mdns, "browse", _boom)
        monkeypatch.setattr(remote_mod, "resolve_board", _boom)
        monkeypatch.setattr(remote_mod, "deploy_over_network", _boom)

        rc = cli_mod._cmd_deploy(_deploy_remote_args(target="/dev/ttyACM0"))

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "--remote cannot be combined with a device path" in captured.err
        assert "/dev/ttyACM0" in captured.err

    def test_any_slash_containing_target_is_rejected_too(self, monkeypatch, capsys):
        monkeypatch.setattr(
            remote_mod, "deploy_over_network",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("must not reach deploy_over_network")
            ),
        )

        rc = cli_mod._cmd_deploy(_deploy_remote_args(target="some/path"))

        assert rc == 1
        assert "--remote cannot be combined" in capsys.readouterr().err

    def test_no_target_is_rejected(self, monkeypatch, capsys):
        monkeypatch.setattr(
            remote_mod, "deploy_over_network",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("must not reach deploy_over_network")
            ),
        )

        rc = cli_mod._cmd_deploy(_deploy_remote_args(target=None))

        assert rc == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "--remote requires a target" in captured.err

    def test_neither_rejection_touches_local_devices_module(self, monkeypatch):
        import mbdeploy.devices as devices_mod

        def _boom(*a, **k):
            raise AssertionError("a rejection must not probe local USB devices")

        monkeypatch.setattr(devices_mod, "flashable_probes", _boom)
        monkeypatch.setattr(devices_mod, "load_devices", _boom)

        assert cli_mod._cmd_deploy(_deploy_remote_args(target="/dev/ttyACM0")) == 1
        assert cli_mod._cmd_deploy(_deploy_remote_args(target=None)) == 1


class TestCmdDeployRemoteDispatch:
    def test_forwards_target_hex_path_and_target_mcu_and_force_relay(
        self, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            remote_mod, "deploy_over_network",
            lambda target, hex_path, target_mcu, force_relay=False, timeout=2.0: (
                calls.append((target, hex_path, target_mcu, force_relay)) or 0
            ),
        )

        rc = cli_mod._cmd_deploy(_deploy_remote_args(
            target="togov", target_mcu="nrf52840", force_relay=True,
            hex_path="custom.hex",
        ))

        assert rc == 0
        assert calls == [("togov", "custom.hex", "nrf52840", True)]

    def test_default_hex_path_used_when_not_given(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            remote_mod, "deploy_over_network",
            lambda target, hex_path, target_mcu, force_relay=False, timeout=2.0: (
                calls.append(hex_path) or 0
            ),
        )

        rc = cli_mod._cmd_deploy(_deploy_remote_args(target="togov"))

        assert rc == 0
        assert calls == [cli_mod._DEFAULT_HEX]

    def test_exit_code_mirrors_deploy_over_network_return_value(self, monkeypatch):
        monkeypatch.setattr(remote_mod, "deploy_over_network", lambda *a, **k: 1)

        assert cli_mod._cmd_deploy(_deploy_remote_args(target="togov")) == 1

    def test_does_not_touch_local_devices_module(self, monkeypatch):
        import mbdeploy.devices as devices_mod

        def _boom(*a, **k):
            raise AssertionError("deploy --remote must not touch local registry/USB")

        monkeypatch.setattr(devices_mod, "load_devices", _boom)
        monkeypatch.setattr(devices_mod, "flashable_probes", _boom)
        monkeypatch.setattr(remote_mod, "deploy_over_network", lambda *a, **k: 0)

        rc = cli_mod._cmd_deploy(_deploy_remote_args(target="togov"))

        assert rc == 0


class TestCmdDeployRemoteEndToEnd:
    """Acceptance criterion, verbatim: `deploy --remote <name> --hex ...`
    against a fake `_mbflash._tcp` server -- success path relays `LOG`
    lines to stderr and returns 0 on `OK flashed`, driven through the
    real `_cmd_deploy` handler rather than calling `deploy_over_network`
    directly."""

    def test_success_path_via_cmd_deploy_relays_log_and_exits_zero(
        self, tmp_path, monkeypatch, capsys
    ):
        payload = b":10000000FF00112233\n:00000001FF\n"
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(payload)

        def handler(conn):
            header = _srv_read_line(conn).decode()
            conn.sendall(b"OK send\n")
            m = re.match(r"FLASH (\d+)", header)
            _srv_read_exact(conn, int(m.group(1)))
            conn.sendall(b"LOG erasing\n")
            conn.sendall(b"LOG programming\n")
            conn.sendall(b"OK flashed\n")

        server = ScriptedFlashServer(handler)
        server.start()
        try:
            _patch_resolve_to(monkeypatch, server, name="togov")
            rc = cli_mod._cmd_deploy(
                _deploy_remote_args(target="togov", hex_path=str(hex_path))
            )
        finally:
            server.close()

        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "erasing" in captured.err
        assert "programming" in captured.err

    def test_err_busy_via_cmd_deploy_exits_nonzero_with_message_on_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        hex_path = tmp_path / "MICROBIT.hex"
        hex_path.write_bytes(b"payload-bytes")

        def handler(conn):
            _srv_read_line(conn)
            conn.sendall(b"ERR busy\n")

        server = ScriptedFlashServer(handler)
        server.start()
        try:
            _patch_resolve_to(monkeypatch, server, name="togov")
            rc = cli_mod._cmd_deploy(
                _deploy_remote_args(target="togov", hex_path=str(hex_path))
            )
        finally:
            server.close()

        assert rc != 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "busy" in captured.err


class TestCmdDeployRemoteBuildRunsLocally:
    """Acceptance criterion: --build/--clean still run locally, unchanged,
    before the network exchange starts."""

    def test_build_flag_calls_builder_run_before_deploy_over_network(
        self, monkeypatch
    ):
        import mbdeploy.builder as builder_mod

        build_calls = []
        monkeypatch.setattr(
            builder_mod, "run",
            lambda clean, verbose, jobs: build_calls.append(
                {"clean": clean, "verbose": verbose, "jobs": jobs}
            ) or 0,
        )
        deploy_calls = []
        monkeypatch.setattr(
            remote_mod, "deploy_over_network",
            lambda *a, **k: deploy_calls.append(1) or 0,
        )

        rc = cli_mod._cmd_deploy(_deploy_remote_args(target="togov", build=True))

        assert rc == 0
        assert len(build_calls) == 1
        assert build_calls[0]["clean"] is False
        assert len(deploy_calls) == 1

    def test_clean_flag_also_triggers_a_local_build(self, monkeypatch):
        import mbdeploy.builder as builder_mod

        build_calls = []
        monkeypatch.setattr(
            builder_mod, "run",
            lambda clean, verbose, jobs: build_calls.append(clean) or 0,
        )
        monkeypatch.setattr(remote_mod, "deploy_over_network", lambda *a, **k: 0)

        rc = cli_mod._cmd_deploy(_deploy_remote_args(target="togov", clean=True))

        assert rc == 0
        assert build_calls == [True]

    def test_no_build_or_clean_skips_builder_entirely(self, monkeypatch):
        import mbdeploy.builder as builder_mod

        def _boom(**k):
            raise AssertionError("must not build when neither flag is given")

        monkeypatch.setattr(builder_mod, "run", _boom)
        monkeypatch.setattr(remote_mod, "deploy_over_network", lambda *a, **k: 0)

        rc = cli_mod._cmd_deploy(_deploy_remote_args(target="togov"))

        assert rc == 0

    def test_build_failure_short_circuits_before_the_network_exchange(
        self, monkeypatch, capsys
    ):
        import mbdeploy.builder as builder_mod

        monkeypatch.setattr(builder_mod, "run", lambda clean, verbose, jobs: 7)

        def _boom(*a, **k):
            raise AssertionError("must not reach the network after a failed build")

        monkeypatch.setattr(remote_mod, "deploy_over_network", _boom)

        rc = cli_mod._cmd_deploy(_deploy_remote_args(target="togov", build=True))

        assert rc == 7
        assert "build failed" in capsys.readouterr().err
