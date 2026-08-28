"""server — Board/Session model, accept loop, serve_serial, serve_flash.

This module implements the daemon-side half of sprint 002's fleet daemon:
per-board live network state (:class:`Board`), the exclusivity/preemption
state machine that resolves the sprint's Design Problem 1 (a live serial
session must never block a `FLASH` that needs to preempt it), a single
shared :class:`AcceptLoop`, and the two wire-protocol handlers
(:func:`serve_serial`, :func:`serve_flash`).

``Supervisor`` — the USB watcher that creates/destroys ``Board`` instances
and drives their listener/mDNS lifecycle (ticket 006) — lives alongside
this code in the same file, but nothing in the ``Board``/session code
above reaches into its responsibilities: ``serve_serial``/``serve_flash``
never open a listener socket themselves, never register mDNS, and never
decide which UIDs are currently connected -- that is entirely
``Supervisor``'s job.

The concurrency contract (sprint.md, Design Problem 1)
-------------------------------------------------------
``Board.lock`` guards only the instructions that read or write
``Board.occupant`` — never the duration of a session or a flash. That
is the whole fix: the issue as originally written held the lock for a
session's entire lifetime, which meant a `FLASH` that needed to preempt
a live session would block forever on a lock only the session it
intends to kill could release. Here, ``Board``'s own
``claim_serial``/``claim_flash``/``release`` methods are the *only*
code that ever touches ``self.lock`` or ``self.occupant`` — each holds
the lock for a handful of instructions and returns. Preemption itself
(``terminate()`` + ``join()`` on a displaced session) always happens
*after* the lock has already been released, holding only a plain object
reference to the session being torn down.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import logging
import os
import selectors
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from mbdeploy import console, devices
from mbdeploy.devices import is_relay
from mbdeploy.flash import flash_hex

logger = logging.getLogger(__name__)

#: How long a client has to send its "AUTH <token>\n" line before the
#: connection is dropped with "ERR auth required".
AUTH_TIMEOUT = 5.0

#: How long a client has to send its command line (`FLASH ...` / `INFO`)
#: once connected (after AUTH, if configured).
COMMAND_TIMEOUT = 5.0

#: How long `serve_flash` waits for the declared payload to finish
#: arriving before giving up and reporting "ERR short payload".
PAYLOAD_TIMEOUT = 30.0

#: How long `serve_flash`'s preemption path waits for a displaced
#: session's thread to exit before proceeding anyway (Design Problem 1:
#: the flash must never hang indefinitely on a session that won't die).
PREEMPT_JOIN_TIMEOUT = 2.0

#: Per-`recv()` poll granularity used while a line/payload read is
#: waiting out its overall timeout -- small enough that an external
#: `stop`/deadline is noticed promptly, without spinning.
_POLL_INTERVAL = 0.2


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class Session:
    """Something that can occupy a :class:`Board`: a live socket, a stop
    flag, and the thread running it.

    A session never blocks ``Board.lock`` for its own lifetime -- the
    board only ever holds a *reference* to it. ``terminate()``/``join()``
    are safe to call from a thread other than the one running the
    session; that is precisely how ``serve_flash`` preempts a live
    :class:`SerialSession`.
    """

    kind = "session"

    def __init__(self, conn: socket.socket) -> None:
        self.conn = conn
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def terminate(self) -> None:
        """Signal the session to stop and forcibly tear down its socket.

        Sets the stop event first (so a loop polling it notices even if
        the shutdown/close below is a no-op or races), then shuts down
        and closes the socket so a thread blocked in a receive call on
        it wakes up promptly instead of waiting out its own read
        timeout.
        """
        self.stop.set()
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass

    def join(self, timeout: float = PREEMPT_JOIN_TIMEOUT) -> None:
        """Join this session's thread with a bounded timeout.

        Never raises and never blocks past ``timeout``: if the thread is
        still alive afterward, a warning is logged and this returns
        anyway -- per Design Problem 1, nothing may hang a preempting
        `FLASH` indefinitely on a session that refuses to die.
        """
        thread = self.thread
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning(
                "%s session thread did not exit within %.1fs of terminate()",
                self.kind, timeout,
            )


class SerialSession(Session):
    """Occupies a board's raw `_mbserial._tcp` pipe."""

    kind = "serial"


class FlashSession(Session):
    """Occupies a board while a `FLASH` is in flight on `_mbflash._tcp`."""

    kind = "flash"


def _terminate_displaced(session: Session, timeout: float = PREEMPT_JOIN_TIMEOUT) -> None:
    """Terminate and join a session that has just been displaced as a
    board's occupant.

    Shared by `serve_flash`'s preemption of a live `SerialSession` and
    `Supervisor._on_departure`'s teardown of *any* live occupant (ticket
    006) -- both call this only *after* the session has already been
    atomically removed as the board's occupant (via `claim_flash`'s
    returned ``previous``, or `Board.take_occupant()`), so this never
    runs while anything still holds `Board.lock`. Factored out once
    rather than duplicated, per Design Problem 1's own design note that
    the two call sites are "the same pattern".
    """
    session.terminate()
    session.join(timeout)


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------

class Board:
    """One board's live network state.

    Owns the board's identity (``uid``, ``name``, the registry ``entry``
    dict), its two listener sockets and two mDNS handles (bound/set by
    ``Supervisor``, ticket 006 -- this class just holds them), and the
    lock/occupant pair that is the whole of Design Problem 1's fix.

    ``lock`` and ``occupant`` are not meant to be touched from outside
    this class. Every legal state transition -- claim, preempt, release
    -- is one of the three methods below, each holding the lock for a
    handful of instructions and never longer.
    """

    def __init__(
        self,
        uid: str,
        name: str,
        entry: dict,
        *,
        serial_listener: socket.socket | None = None,
        flash_listener: socket.socket | None = None,
    ) -> None:
        self.uid = uid
        self.name = name
        self.entry = entry
        self.serial_listener = serial_listener
        self.flash_listener = flash_listener
        #: Set by Supervisor (ticket 006) once each service is registered.
        self.serial_mdns_handle: object | None = None
        self.flash_mdns_handle: object | None = None
        #: Flipped to False by Supervisor on USB departure; read by
        #: `serve_flash`'s INFO reply. Not part of the occupant state
        #: machine -- a plain status flag, not a lock/occupant write.
        self.connected = True

        self.lock = threading.Lock()
        self.occupant: SerialSession | FlashSession | None = None

    def claim_serial(self, session: SerialSession) -> bool:
        """Install ``session`` as occupant iff the board is currently idle.

        A serial session never preempts anything: if anything at all
        already occupies the board (a live session or an in-flight
        flash), this returns ``False`` and leaves the existing occupant
        untouched.
        """
        with self.lock:
            if self.occupant is not None:
                return False
            self.occupant = session
            return True

    def claim_flash(
        self, session: FlashSession
    ) -> tuple[bool, SerialSession | FlashSession | None]:
        """Attempt to install ``session`` as the new flash occupant.

        Returns ``(True, previous)``: ``previous`` is ``None`` if the
        board was idle, or the :class:`SerialSession` that the caller
        must now preempt -- *outside* this lock, via its own
        ``terminate()``/``join()``. Returns ``(False, None)`` if a
        :class:`FlashSession` already occupies the board: two flashes
        never race, and a concurrent second `FLASH` sees this new
        session as the occupant and also gets refused.
        """
        with self.lock:
            if isinstance(self.occupant, FlashSession):
                return False, None
            previous = self.occupant
            self.occupant = session
            return True, previous

    def release(self, session: Session) -> None:
        """Clear ``occupant`` iff it is still ``session``.

        The identity check guards against a stale release racing a
        newer occupant that has already claimed the board (e.g. a
        preempted session's own cleanup running after the flash that
        displaced it has already installed itself).
        """
        with self.lock:
            if self.occupant is session:
                self.occupant = None

    def take_occupant(self) -> SerialSession | FlashSession | None:
        """Atomically grab and clear whatever currently occupies the board.

        Used by `Supervisor._on_departure` (ticket 006): unlike
        ``claim_serial``/``claim_flash``/``release``, this doesn't care
        what the occupant *is* -- USB departure must clean up a board
        regardless of whether it was idle, mid-session, or mid-flash.
        Returns ``None`` if the board was already idle. The lock is held
        only for this read-and-clear; the caller terminates whatever is
        returned (via `_terminate_displaced`) afterward, outside the
        lock -- the same pattern `claim_flash`'s preemption uses.
        """
        with self.lock:
            occupant = self.occupant
            self.occupant = None
            return occupant


# ---------------------------------------------------------------------------
# Accept loop
# ---------------------------------------------------------------------------

def _run_handler(handler: Callable[[socket.socket], None], conn: socket.socket) -> None:
    """Run one connection's handler, guarding against it leaking the socket
    or an uncaught exception silently taking down its (otherwise
    invisible) thread."""
    try:
        handler(conn)
    except Exception:
        logger.exception("connection handler raised")
    finally:
        try:
            conn.close()
        except OSError:
            pass


class AcceptLoop:
    """One ``selectors.DefaultSelector`` shared across every board's
    listener sockets.

    A single selector rather than a thread per listening socket, so
    hotplug (registering/unregistering listeners as boards arrive and
    depart) never churns threads -- only accepted *connections* get
    their own thread, exactly one each.

    ``register``/``unregister`` are how ``Supervisor`` (ticket 006)
    wires a board's listeners in and out on arrival/departure; this
    class only implements the loop mechanism and per-connection
    dispatch, not the hotplug policy itself.
    """

    def __init__(self) -> None:
        self.selector = selectors.DefaultSelector()
        self._stop = threading.Event()

    def register(
        self, sock: socket.socket, handler: Callable[[socket.socket], None]
    ) -> None:
        """Register a listener socket; ``handler(conn)`` runs in a new
        thread for each connection it accepts."""
        sock.setblocking(False)
        self.selector.register(sock, selectors.EVENT_READ, handler)

    def unregister(self, sock: socket.socket) -> None:
        """Remove a listener socket. A no-op if it isn't registered."""
        try:
            self.selector.unregister(sock)
        except (KeyError, ValueError):
            pass

    def stop(self) -> None:
        """Ask :meth:`run` to return after its current poll interval."""
        self._stop.set()

    def close(self) -> None:
        """Stop the loop (if running) and release the selector's fd."""
        self.stop()
        self.selector.close()

    def run(self, poll_timeout: float = 0.2) -> None:
        """Accept connections until :meth:`stop` is called.

        For each listener that becomes ready, ``accept()``s exactly one
        connection and spawns exactly one new daemon thread to run its
        handler -- never more than one thread per accepted connection,
        and the accept-loop thread itself never blocks on a handler.
        """
        while not self._stop.is_set():
            try:
                events = self.selector.select(timeout=poll_timeout)
            except OSError:
                # Selector closed out from under us (e.g. shutdown racing
                # a poll) -- exit rather than raise out of the loop.
                return
            for key, _mask in events:
                listener = key.fileobj
                handler = key.data
                try:
                    conn, _addr = listener.accept()
                except OSError:
                    continue
                conn.setblocking(True)
                threading.Thread(
                    target=_run_handler,
                    args=(handler, conn),
                    name="mbdeploy-conn",
                    daemon=True,
                ).start()


# ---------------------------------------------------------------------------
# Wire-protocol helpers
# ---------------------------------------------------------------------------

def _send_line(conn: socket.socket, text: str) -> None:
    """Best-effort ``text + "\\n"`` write; a vanished peer is not an error
    worth raising out of a handler that's already ending."""
    try:
        conn.sendall((text + "\n").encode("utf-8"))
    except OSError:
        pass


def _close(conn: socket.socket) -> None:
    """Best-effort shutdown+close; safe to call more than once."""
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        conn.close()
    except OSError:
        pass


def _read_line(conn: socket.socket, timeout: float, max_len: int = 8192) -> bytes | None:
    """Read one ``\\n``-terminated line, one byte at a time.

    Returns the line without its trailing newline, or ``None`` if the
    peer closed or ``timeout`` elapsed before a newline arrived.
    Reading a single byte per ``recv()`` call is deliberate: it
    guarantees this never reads ahead into bytes that belong to what
    comes *after* the line -- the raw relay stream following a
    successful `AUTH`, or a `FLASH` payload's exact byte count -- which
    a buffered readline() could easily swallow.
    """
    deadline = time.time() + timeout
    buf = bytearray()
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        conn.settimeout(min(remaining, _POLL_INTERVAL))
        try:
            b = conn.recv(1)
        except socket.timeout:
            continue
        except OSError:
            return None
        if not b:
            return None                 # EOF before a newline arrived
        if b == b"\n":
            return bytes(buf)
        buf.extend(b)
        if len(buf) > max_len:
            return None


def _read_exact(conn: socket.socket, n: int, timeout: float) -> bytes:
    """Read up to exactly ``n`` bytes, or as many as arrive before the
    peer closes or ``timeout`` elapses -- never raises.

    The caller compares ``len(result)`` against ``n`` to detect a short
    payload; this never raises, so a stalled or disconnecting client
    always yields a definite (if incomplete) buffer rather than an
    exception the caller would have to guard against too.
    """
    deadline = time.time() + timeout
    buf = bytearray()
    while len(buf) < n:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        conn.settimeout(min(remaining, _POLL_INTERVAL))
        try:
            chunk = conn.recv(min(4096, n - len(buf)))
        except socket.timeout:
            continue
        except OSError:
            break
        if not chunk:
            break                       # EOF before the declared count
        buf.extend(chunk)
    return bytes(buf)


def _authenticate(conn: socket.socket, token: str, timeout: float) -> bool:
    """Consume the ``AUTH <token>\\n`` handshake; reply ``OK`` on success.

    Returns ``False`` (having sent nothing) on a missing line, a line
    that isn't ``AUTH ...``, or a token that doesn't match -- the
    caller is responsible for replying ``ERR auth required`` and
    closing. Comparison is constant-time (``hmac.compare_digest``) so a
    wrong-token guess can't be narrowed down by timing.
    """
    line = _read_line(conn, timeout)
    if line is None:
        return False
    text = line.decode("utf-8", "replace")
    if not text.startswith("AUTH "):
        return False
    supplied = text[len("AUTH "):]
    if not hmac.compare_digest(supplied, token):
        return False
    _send_line(conn, "OK")
    return True


# ---------------------------------------------------------------------------
# serve_serial
# ---------------------------------------------------------------------------

def serve_serial(
    board: Board,
    conn: socket.socket,
    *,
    token: str | None = None,
    baud: int = devices.BAUD_RATE,
    auth_timeout: float = AUTH_TIMEOUT,
) -> None:
    """Handle one accepted `_mbserial._tcp` connection against ``board``.

    Exclusive and non-preempting: a second connection while one is
    already live gets ``ERR busy`` and is closed, and a serial session
    never displaces anything else occupying the board -- only
    ``serve_flash`` preempts (Design Problem 1). Once claimed, this is a
    genuinely raw byte pipe (:func:`console.relay_socket`): no framing,
    no re-encoding, so ``_mbserial._tcp`` stays exactly what a plain
    serial terminal expects.
    """
    try:
        if token is not None and not _authenticate(conn, token, auth_timeout):
            _send_line(conn, "ERR auth required")
            return

        session = SerialSession(conn)
        session.thread = threading.current_thread()
        if not board.claim_serial(session):
            _send_line(conn, "ERR busy")
            return

        try:
            try:
                ser = console.open_port(board.entry.get("port"), baud)
            except console.ConsoleError as exc:
                _send_line(conn, f"ERR {exc}")
                return
            try:
                console.relay_socket(ser, conn, session.stop)
            finally:
                try:
                    ser.close()
                except Exception:
                    pass
        finally:
            board.release(session)
    finally:
        _close(conn)


# ---------------------------------------------------------------------------
# serve_flash
# ---------------------------------------------------------------------------

def _parse_flash_header(text: str) -> dict | None:
    """Parse ``FLASH <bytes> [sha256=<hex>] [force-relay]``.

    Returns ``{"nbytes", "sha256", "force_relay"}``, or ``None`` if the
    header is malformed -- today, only a missing or non-numeric byte
    count triggers that; the optional tokens may appear in either order
    or be omitted entirely.
    """
    parts = text.split()
    if len(parts) < 2 or parts[0] != "FLASH" or not parts[1].isdigit():
        return None
    nbytes = int(parts[1])
    sha256 = None
    force_relay = False
    for tok in parts[2:]:
        if tok.startswith("sha256="):
            sha256 = tok[len("sha256="):]
        elif tok == "force-relay":
            force_relay = True
    return {"nbytes": nbytes, "sha256": sha256, "force_relay": force_relay}


def _write_temp_hex(payload: bytes) -> str:
    """Write ``payload`` to a fresh temp file; return its path."""
    fd, path = tempfile.mkstemp(suffix=".hex", prefix="mbdeploy-flash-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
    except Exception:
        os.unlink(path)
        raise
    return path


def _handle_info(board: Board, conn: socket.socket) -> None:
    """Reply to ``INFO`` with the board's current identity/state.

    Deliberately does not touch ``board.lock``/``board.occupant`` at
    all: `INFO` must answer identically regardless of whether a session
    or a flash currently occupies the board.
    """
    payload = {
        "uid": board.uid,
        "board_name": board.name,
        "role": board.entry.get("role"),
        "port": board.entry.get("port"),
        "connected": board.connected,
    }
    _send_line(conn, "OK " + json.dumps(payload))


def serve_flash(
    board: Board,
    conn: socket.socket,
    *,
    token: str | None = None,
    no_flash: bool = False,
    target_mcu: str = devices.DEFAULT_MCU,
    auth_timeout: float = AUTH_TIMEOUT,
    command_timeout: float = COMMAND_TIMEOUT,
    payload_timeout: float = PAYLOAD_TIMEOUT,
) -> None:
    """Handle one accepted `_mbflash._tcp` connection against ``board``.

    Implements the `FLASH`/`INFO` header-line protocol, the
    `--no-flash`/relay-guard/`--token` access controls, and -- the
    ticket's central case -- Design Problem 1's preemption: a `FLASH`
    against a board with a live :class:`SerialSession` swaps in its own
    :class:`FlashSession` under the board's lock, then tears the old
    session down *outside* that lock before proceeding, so nothing here
    can ever deadlock against the session it's about to kill.
    """
    try:
        if token is not None and not _authenticate(conn, token, auth_timeout):
            _send_line(conn, "ERR auth required")
            return

        line = _read_line(conn, command_timeout)
        if line is None:
            return
        text = line.decode("utf-8", "replace").strip()
        if not text:
            return
        command = text.split(None, 1)[0]

        if command == "INFO":
            _handle_info(board, conn)
            return

        if command != "FLASH":
            _send_line(conn, "ERR unknown command")
            return

        # --no-flash is checked before anything else about this FLASH --
        # before the header is even parsed for validity -- so flash_hex
        # can never be reached while flashing is disabled, regardless of
        # what else is wrong (or right) with the request.
        if no_flash:
            _send_line(conn, "ERR flash disabled")
            return

        header = _parse_flash_header(text)
        if header is None:
            _send_line(conn, "ERR bad header")
            return

        if is_relay(board.entry.get("role")) and not header["force_relay"]:
            _send_line(conn, "ERR relay refused — send force-relay")
            return

        session = FlashSession(conn)
        session.thread = threading.current_thread()
        claimed, previous = board.claim_flash(session)
        if not claimed:
            _send_line(conn, "ERR busy")
            return

        try:
            if isinstance(previous, SerialSession):
                # Outside the lock (already released by claim_flash):
                # tear down the displaced session and wait -- bounded --
                # for it to actually exit before this flash proceeds.
                # Shared with `Supervisor._on_departure` (ticket 006) via
                # `_terminate_displaced` rather than duplicating this
                # terminate()+join() sequence in two places.
                _terminate_displaced(previous)

            _send_line(conn, "OK send")
            payload = _read_exact(conn, header["nbytes"], payload_timeout)
            if len(payload) < header["nbytes"]:
                _send_line(conn, "ERR short payload")
                return

            declared_sha256 = header["sha256"]
            if declared_sha256 is not None:
                digest = hashlib.sha256(payload).hexdigest()
                if not hmac.compare_digest(digest, declared_sha256.lower()):
                    _send_line(conn, "ERR sha256 mismatch")
                    return

            tmp_path = _write_temp_hex(payload)
            try:
                rc = flash_hex(
                    board.uid,
                    tmp_path,
                    target_mcu,
                    log=lambda msg: _send_line(conn, f"LOG {msg}"),
                    board_name=board.name,
                )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            if rc == 0:
                _send_line(conn, "OK flashed")
            else:
                _send_line(conn, f"ERR flash failed (exit {rc})")
        finally:
            board.release(session)
    finally:
        _close(conn)


# ---------------------------------------------------------------------------
# Supervisor -- the USB watcher (sprint.md Step 3, Design Problem 2)
# ---------------------------------------------------------------------------

#: Default `--poll-interval`, threaded through by ticket 007's CLI.
DEFAULT_POLL_INTERVAL = 2.0

#: Fully-qualified mDNS service types `Supervisor` registers.
#: `Advertiser.register`'s own contract requires a type ending in a
#: domain (e.g. ``"_mbserial._tcp.local."``).
SERIAL_SERVICE_TYPE = "_mbserial._tcp.local."
FLASH_SERVICE_TYPE = "_mbflash._tcp.local."


def _instance_name(entry: dict, uid: str) -> str:
    """mDNS instance name for a board: ``board_name`` -> ``device_name``
    -> ``mb-<last 8 of uid>``.

    ``board_name`` (read over SWD by `devices.probe_all`, part of every
    arrival probe) comes first because it works on unflashed and silent
    boards alike -- all four Nolanet boards currently announce nothing
    (``role``/``device_name`` are both empty strings), so this is the
    path that actually runs in production, not a rarely-hit fallback.
    ``device_name`` (from a ``DEVICE:``/``device `` announcement) covers
    a board whose SWD read failed. ``mb-<uid8>`` is the last resort, so
    a board is always nameable even with neither identity source
    available.
    """
    return entry.get("board_name") or entry.get("device_name") or f"mb-{uid[-8:]}"


def _service_txt(entry: dict, uid: str, port: int) -> dict[str, str]:
    """TXT record fields for one of a board's two mDNS registrations."""
    return {
        "uid": entry.get("uid") or uid,
        "role": entry.get("role") or "",
        "common_name": entry.get("common_name") or "",
        "enum": str(entry.get("enum", "")),
        "port": str(port),
    }


class Supervisor:
    """USB watcher: keeps the live `Board` set in sync with which boards
    are physically connected (sprint.md Step 3, Design Problem 2).

    `_tick(probes)` is directly callable with a plain list of probe
    dicts (``[{"uid": ...}, ...]``) -- no sleeping, no real hardware --
    which is what makes the watcher testable. It diffs the incoming UID
    set against the previous tick's, purely off that set: it never
    touches `Board.lock` or `Board.occupant` to detect arrival or
    departure, so a board mid-flash or mid-session is still detected as
    departed the instant it disappears from `devices.flashable_probes()`
    (sprint.md Design Problem 1's Supervisor-side half -- see
    `_on_departure`).

    Only arrival triggers a probe. This design has no periodic
    re-probing of an already-known, still-connected board -- Nolanet is
    a fixed single board per node, and nothing in this sprint's use
    cases needs a board's identity to change without a replug -- so the
    non-blocking lock-acquire the source issue describes for an optional
    identity-refresh step is deliberately not implemented here: there is
    no such step for it to guard.
    """

    def __init__(
        self,
        *,
        accept_loop: AcceptLoop,
        advertiser: Any,
        config_path: Path,
        base_port: int = 0,
        bind: str = "",
        target_mcu: str = devices.DEFAULT_MCU,
        token: str | None = None,
        no_flash: bool = False,
        service_name: str | None = None,
    ) -> None:
        self.accept_loop = accept_loop
        self.advertiser = advertiser
        self.config_path = config_path
        self.base_port = base_port
        self.bind = bind
        self.target_mcu = target_mcu
        self.token = token
        self.no_flash = no_flash
        #: ticket 007's `--service-name`: when set, overrides the
        #: board_name/device_name/mb-<uid8> fallback chain entirely for
        #: *every* board this Supervisor manages. Only meaningful on a
        #: single-board host (Nolanet's case, sprint.md Step 7 Open
        #: Question) -- on a multi-board host every board would get the
        #: same name and rely on zeroconf's own collision-renaming.
        self.service_name = service_name

        #: uid -> live Board, for every currently-connected board.
        self.boards: dict[str, Board] = {}
        #: The previous tick's UID set; `_tick` diffs against this.
        self._known: set[str] = set()
        #: Sequential port allocation from `base_port`, and a free-list of
        #: departed boards' port pairs available for reuse (sprint.md
        #: Step 7's Open Question, resolved there: reclaim rather than
        #: retire). Unused when `base_port` is 0/unset -- listeners bind
        #: OS-assigned ephemeral ports instead, and there is nothing to
        #: reclaim.
        self._next_port: int | None = base_port if base_port else None
        self._port_freelist: list[tuple[int, int]] = []
        #: uid -> the (serial, flash) ports actually handed out for it,
        #: so `_on_departure` reclaims exactly what `_on_arrival` gave
        #: out. Only populated when `base_port` is set.
        self._board_ports: dict[str, tuple[int, int]] = {}

    # -- the tick -------------------------------------------------------

    def _tick(self, probes: list[dict]) -> None:
        """Diff `probes`' UID set against the previous tick and fire
        `_on_arrival`/`_on_departure` for each change.

        Never touches `Board.lock`/`Board.occupant` -- the whole diff is
        computed from `probes` alone, which is exactly why a board
        mid-session is still detected as departed (sprint.md Design
        Problem 1). Repeating the same probe list twice is idempotent:
        the second call computes empty `arrived`/`departed` sets and
        does nothing.
        """
        current = {p["uid"] for p in probes}
        departed = self._known - current
        arrived = current - self._known
        for uid in departed:
            self._on_departure(uid)
        for uid in arrived:
            self._on_arrival(uid)
        self._known = current

    # -- arrival ----------------------------------------------------------

    def _on_arrival(self, uid: str) -> None:
        """Refresh this board's identity, bind its listeners, and
        register both mDNS services.

        Calls ``devices.probe_all(only_uids={uid})`` -- ticket 002's
        scoping parameter -- so this never sends a stray `HELLO` to any
        other already-connected board (sprint.md Design Problem 2). This
        is the *only* caller in `Supervisor` that ever invokes
        `probe_all`.
        """
        entries = devices.probe_all(
            self.config_path, target_mcu=self.target_mcu, only_uids={uid}
        )
        entry = next((e for e in entries if e.get("uid") == uid), {"uid": uid})
        name = self.service_name or _instance_name(entry, uid)

        serial_port, flash_port = self._alloc_ports()
        serial_listener = self._bind_listener(serial_port)
        flash_listener = self._bind_listener(flash_port)
        serial_port = serial_listener.getsockname()[1]
        flash_port = flash_listener.getsockname()[1]

        board = Board(
            uid, name, entry,
            serial_listener=serial_listener,
            flash_listener=flash_listener,
        )
        board.serial_mdns_handle = self.advertiser.register(
            name, SERIAL_SERVICE_TYPE, serial_port, _service_txt(entry, uid, serial_port)
        )
        board.flash_mdns_handle = self.advertiser.register(
            name, FLASH_SERVICE_TYPE, flash_port, _service_txt(entry, uid, flash_port)
        )

        self.accept_loop.register(
            serial_listener,
            functools.partial(serve_serial, board, token=self.token),
        )
        self.accept_loop.register(
            flash_listener,
            functools.partial(
                serve_flash,
                board,
                token=self.token,
                no_flash=self.no_flash,
                target_mcu=self.target_mcu,
            ),
        )

        self.boards[uid] = board
        if self.base_port:
            self._board_ports[uid] = (serial_port, flash_port)

    # -- departure ----------------------------------------------------------

    def _on_departure(self, uid: str) -> None:
        """Tear a departed board down: kill any live session, unregister
        both mDNS services, close both listeners, reclaim its ports.

        Detected purely from `uid` being absent from the latest probe
        list -- `_tick` never acquired `Board.lock` to get here. The
        lock *is* taken here, but only for the instant
        `Board.take_occupant()` needs to grab and clear whatever
        currently occupies the board; the `terminate()`/`join()` that
        actually tears the session down (`_terminate_displaced`) runs
        afterward, outside that lock -- the same pattern `serve_flash`'s
        preemption uses. This is precisely what stops a board with a
        long-lived serial session from being skipped and leaking its
        mDNS advertisement after unplug (sprint.md Design Problem 1).
        """
        board = self.boards.pop(uid, None)
        if board is None:
            return
        board.connected = False

        occupant = board.take_occupant()
        if occupant is not None:
            _terminate_displaced(occupant)

        if board.serial_mdns_handle is not None:
            self.advertiser.unregister(board.serial_mdns_handle)
            board.serial_mdns_handle = None
        if board.flash_mdns_handle is not None:
            self.advertiser.unregister(board.flash_mdns_handle)
            board.flash_mdns_handle = None

        if board.serial_listener is not None:
            self.accept_loop.unregister(board.serial_listener)
            board.serial_listener.close()
            board.serial_listener = None
        if board.flash_listener is not None:
            self.accept_loop.unregister(board.flash_listener)
            board.flash_listener.close()
            board.flash_listener = None

        ports = self._board_ports.pop(uid, None)
        if ports is not None:
            self._port_freelist.append(ports)

    # -- port allocation ------------------------------------------------

    def _alloc_ports(self) -> tuple[int | None, int | None]:
        """Two ports for an arriving board.

        `base_port` unset/0: returns ``(None, None)`` -- `_bind_listener`
        binds an OS-assigned ephemeral port for each. `base_port` set:
        reuses a departed board's port pair if one is free, else the
        next two ports sequentially from `base_port` -- a free-list
        rather than an ever-growing counter (sprint.md Step 7's Open
        Question).
        """
        if not self.base_port:
            return None, None
        if self._port_freelist:
            return self._port_freelist.pop()
        assert self._next_port is not None
        ports = (self._next_port, self._next_port + 1)
        self._next_port += 2
        return ports

    def _bind_listener(self, port: int | None) -> socket.socket:
        """Bind and listen on `port` (or an OS-assigned ephemeral port
        when `port` is `None`), on `self.bind`."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.bind, port or 0))
        sock.listen(5)
        return sock

    # -- polling loop -----------------------------------------------------

    def run(
        self,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        stop: threading.Event | None = None,
    ) -> None:
        """Call ``_tick(devices.flashable_probes())`` every
        `poll_interval` seconds until `stop` is set.

        `stop` defaults to a private `Event` nothing else can signal --
        callers that need to end the loop (ticket 007's `serve`
        subcommand, on SIGINT/SIGTERM) pass their own.
        """
        if stop is None:
            stop = threading.Event()
        while not stop.is_set():
            self._tick(devices.flashable_probes())
            stop.wait(poll_interval)
