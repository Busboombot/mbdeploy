"""Tests for `console.relay_socket` — the network-facing sibling of `interact`.

Uses a real loopback `socket.socketpair()` (no actual TCP listener needed) and
the scripted `FakeSerial` from `test_connect.py`.  `relay_socket` itself is
run in its own thread so the test can drive both the "conn" side (the
relay's actual argument) and the "peer" side (a stand-in for the remote
client) independently.
"""

from __future__ import annotations

import socket
import threading
import time

import mbdeploy.console as console_mod
from tests.test_connect import FakeSerial


class RaisingSerial(FakeSerial):
    """A `FakeSerial` whose `read` raises, simulating a physical unplug."""

    def read(self, size=1):
        raise OSError("device disconnected")


def _wait_until(predicate, timeout=2.0, interval=0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestRelaySocket:
    def test_serial_to_socket_bytes_flow(self):
        ser = FakeSerial([b"hello-from-board"])
        conn, peer = socket.socketpair()
        stop = threading.Event()
        try:
            t = threading.Thread(
                target=console_mod.relay_socket, args=(ser, conn, stop)
            )
            t.start()
            peer.settimeout(2.0)
            data = peer.recv(64)
            assert data == b"hello-from-board"
        finally:
            stop.set()
            t.join(timeout=2.0)
            conn.close()
            peer.close()
        assert not t.is_alive()

    def test_socket_to_serial_bytes_flow(self):
        ser = FakeSerial([])
        conn, peer = socket.socketpair()
        stop = threading.Event()
        try:
            t = threading.Thread(
                target=console_mod.relay_socket, args=(ser, conn, stop)
            )
            t.start()
            peer.sendall(b"MOVE FORWARD\n")
            assert _wait_until(lambda: ser.written == b"MOVE FORWARD\n")
        finally:
            stop.set()
            t.join(timeout=2.0)
            conn.close()
            peer.close()
        assert not t.is_alive()

    def test_returns_promptly_when_stop_is_set_externally(self):
        """No data need flow on either side for an external `stop` to work."""
        ser = FakeSerial([])
        conn, peer = socket.socketpair()
        stop = threading.Event()
        try:
            t = threading.Thread(
                target=console_mod.relay_socket, args=(ser, conn, stop)
            )
            t.start()
            time.sleep(0.05)         # let the threads settle into their loops
            start = time.time()
            stop.set()
            t.join(timeout=2.0)
            assert time.time() - start < 1.0
            assert not t.is_alive()
        finally:
            conn.close()
            peer.close()

    def test_returns_when_the_peer_closes_the_socket(self):
        """A client disconnect ends the session without an external `stop`."""
        ser = FakeSerial([])
        conn, peer = socket.socketpair()
        stop = threading.Event()
        try:
            t = threading.Thread(
                target=console_mod.relay_socket, args=(ser, conn, stop)
            )
            t.start()
            time.sleep(0.05)
            peer.close()
            t.join(timeout=2.0)
            assert not t.is_alive()
            assert stop.is_set()
        finally:
            conn.close()

    def test_serial_error_ends_the_session_promptly(self):
        """A serial read error (simulated unplug) must not hang the relay."""
        ser = RaisingSerial([])
        conn, peer = socket.socketpair()
        stop = threading.Event()
        try:
            start = time.time()
            t = threading.Thread(
                target=console_mod.relay_socket, args=(ser, conn, stop)
            )
            t.start()
            t.join(timeout=2.0)
            assert time.time() - start < 2.0
            assert not t.is_alive()
            assert stop.is_set()
        finally:
            conn.close()
            peer.close()

    def test_binary_payload_is_not_decoded_either_direction(self):
        """NUL bytes and high bytes must survive verbatim, both directions."""
        payload_up = bytes([0x00, 0xFF, 0x80, 0x01, 0x00, 0x9C])
        payload_down = bytes([0xDE, 0xAD, 0x00, 0xBE, 0xEF, 0xFF])
        ser = FakeSerial([payload_up])
        conn, peer = socket.socketpair()
        stop = threading.Event()
        try:
            t = threading.Thread(
                target=console_mod.relay_socket, args=(ser, conn, stop)
            )
            t.start()
            peer.settimeout(2.0)
            received = peer.recv(64)
            assert received == payload_up
            assert isinstance(received, bytes)

            peer.sendall(payload_down)
            assert _wait_until(lambda: ser.written == payload_down)
            assert isinstance(ser.written, bytes)
        finally:
            stop.set()
            t.join(timeout=2.0)
            conn.close()
            peer.close()

    def test_no_thread_is_leaked_after_the_session_ends(self):
        baseline = threading.active_count()
        ser = FakeSerial([])
        conn, peer = socket.socketpair()
        stop = threading.Event()
        try:
            t = threading.Thread(
                target=console_mod.relay_socket, args=(ser, conn, stop)
            )
            t.start()
            time.sleep(0.05)
            assert threading.active_count() > baseline
            stop.set()
            t.join(timeout=2.0)
            assert not t.is_alive()
        finally:
            conn.close()
            peer.close()
        assert _wait_until(lambda: threading.active_count() == baseline, timeout=1.0)
