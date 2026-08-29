"""remote — client-side half of the network protocols ``server.py`` speaks.

Per ``sprint.md``'s Architecture (Step 3), this module's boundary is the
client-side mirror of ``server.py``: it never imports ``server.py`` (it is
a wire peer, not a caller, of the module it talks to) and it never touches
``console.py``'s internals — only the public contract ``send_command``/
``interact`` already promise callers.

This ticket lands two of that boundary's pieces:

- :class:`SocketSerial` — an adapter around a connected ``socket.socket``
  that exposes exactly the six members ``console.py`` actually calls, so
  ``console.send_command()``/``console.interact()`` run completely
  unchanged against a network connection, the same duck-typed contract
  they already use against a real pyserial port opened by
  ``console.open_port``. Missing any one of the six breaks a specific
  caller: ``reset_input_buffer``/``readline`` are ``send_command``'s,
  ``in_waiting`` is ``interact``'s (via ``ser.read(max(1,
  ser.in_waiting))``) — a one-shot-only test suite would never catch a
  broken ``in_waiting``, since ``send_command`` never calls it.
- :func:`resolve_board` — turns an operator-typed short board name into
  exactly one ``{name, host, port, txt}`` via ``mdns.browse()``. Two
  things the source issue gets wrong, corrected here per sprint.md Step
  1 (read directly from ``server.py``, not the issue's paraphrase): the
  TXT ``port`` field is the network TCP port, not the board's local
  ``/dev/ttyACM0``; and the short board name is not a TXT field at all —
  it is recovered from the leading label of ``mdns.browse()``'s own
  ``name`` field, because ``mdns.Advertiser.register`` names every
  ``ServiceInfo`` ``f"{name}.{service_type}"``.
- :func:`list_remote` (ticket 003) — browses both service types and
  groups the results into one row per board for ``list --remote``.
- :func:`deploy_over_network` (ticket 005) — the client side of
  ``server.py::serve_flash``'s ``FLASH``/``LOG``/``OK``/``ERR`` wire
  protocol: resolves the board via :func:`resolve_board`, sends the hex
  payload with its sha256, relays ``LOG`` lines to stderr as they
  arrive, and turns the server's terminal line into a process-style exit
  code (``OK flashed`` -> 0, any ``ERR ...`` -> 1) for ``deploy
  --remote`` to return directly.
"""

from __future__ import annotations

import hashlib
import re
import socket
import sys
from pathlib import Path

from mbdeploy import console, mdns

#: Zeroconf's own collision-rename suffix (``allow_name_change=True`` in
#: ``mdns.Advertiser.register``), e.g. ``"togov (2)"``. Stripped in
#: addition to the ``.{service_type}`` suffix so that a second board which
#: happens to register the same short name -- a real possibility sprint.md
#: Step 6 calls out ("two boards on two hosts hashed to the same
#: five-letter name") -- still groups back to its true short name for
#: :func:`resolve_board`'s ambiguity check, instead of silently being
#: treated as an unrelated non-match and letting the collision through.
_DEDUPE_SUFFIX_RE = re.compile(r" \(\d+\)$")

#: The two mDNS service types this module's client functions browse.
#: Duplicated literally from ``server.py``'s own ``SERIAL_SERVICE_TYPE``/
#: ``FLASH_SERVICE_TYPE`` rather than imported from it -- this module's
#: boundary (sprint.md Step 3) is "never imports server.py; it is a wire
#: peer, not a caller, of the module it talks to." These two strings are
#: part of the wire protocol this module's client code implements, the
#: same reason ``tests/test_remote.py`` already redefines its own
#: ``SERVICE_TYPE`` constant rather than importing ``server``'s.
SERIAL_SERVICE_TYPE = "_mbserial._tcp.local."
FLASH_SERVICE_TYPE = "_mbflash._tcp.local."


class SocketSerial:
    """Adapter around a connected ``socket.socket``, usable anywhere
    ``console.py`` expects an open pyserial port.

    Exposes exactly the six members ``console.send_command`` and
    ``console.interact`` touch: :meth:`reset_input_buffer`,
    :meth:`write`, :meth:`flush`, :meth:`readline`, :meth:`read`, and the
    :attr:`in_waiting` property. ``close`` is provided too, for a
    caller's own socket-lifecycle management (not one of the six
    ``console.py`` calls, but the adapter that opens the socket needs
    some way to close it).

    ``sock`` must already be connected (e.g. via
    ``socket.create_connection``) -- this class does no connecting of
    its own, mirroring how ``console.open_port`` hands ``send_command``/
    ``interact`` an already-open port rather than opening one itself.

    A single internal ``bytes`` buffer holds whatever has been read off
    the socket but not yet consumed by a caller, since ``socket.recv()``
    is not line-oriented the way a serial port's byte stream is treated
    by :meth:`readline`: a line can arrive split across multiple
    ``recv()`` calls, or several lines can arrive in one.
    """

    def __init__(self, sock: socket.socket, timeout: float = console.READ_TIMEOUT) -> None:
        self._sock = sock
        self._timeout = timeout
        self._buf = b""
        self._eof = False
        self._sock.settimeout(timeout)

    def reset_input_buffer(self) -> None:
        """Discard anything buffered or currently available, unread.

        Mirrors pyserial's ``reset_input_buffer()``, which ``send_command``
        calls before writing a new command so a previous exchange's
        leftover bytes can never be mistaken for the new reply.
        """
        self._drain_nonblocking()
        self._buf = b""

    def write(self, data: bytes) -> int:
        """Send ``data`` in full; return the number of bytes written."""
        self._sock.sendall(data)
        return len(data)

    def flush(self) -> None:
        """No-op: ``write``'s ``sendall`` already blocks until the OS has
        accepted every byte, so there is nothing left to wait for here --
        unlike a real serial port, a TCP socket has no separate
        user-space write buffer of its own to flush.
        """

    def readline(self) -> bytes:
        """Return one line, ending in ``b"\\n"``, or whatever is available
        at EOF or at a read timeout.

        A socket is not line-oriented, so this keeps pulling more bytes
        into the internal buffer until a ``b"\\n"`` shows up, the peer
        closes (EOF), or a single ``recv()`` times out with nothing new
        to show for it -- at which point it returns whatever partial (or
        empty) buffer it has, rather than blocking indefinitely. That
        timeout-returns-partial-data behavior is what lets
        ``console.send_command``'s own deadline/idle-gap polling loop
        keep terminating, the same way it does against a real pyserial
        port with a short read timeout.
        """
        while True:
            idx = self._buf.find(b"\n")
            if idx != -1:
                line, self._buf = self._buf[: idx + 1], self._buf[idx + 1 :]
                return line
            if self._eof:
                line, self._buf = self._buf, b""
                return line
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                line, self._buf = self._buf, b""
                return line
            except OSError:
                self._eof = True
                line, self._buf = self._buf, b""
                return line
            if not chunk:
                self._eof = True
                continue
            self._buf += chunk

    def read(self, size: int = 1) -> bytes:
        """Return up to ``size`` bytes, blocking as needed (bounded by
        this adapter's read timeout) until that many are available, EOF
        is reached, or a read times out with nothing new.

        Used by ``console.interact``'s reader thread as
        ``ser.read(max(1, ser.in_waiting))`` -- since :attr:`in_waiting`
        already pulled everything currently available into the internal
        buffer, this call typically just slices it off without touching
        the socket again.
        """
        if size <= 0:
            return b""
        while len(self._buf) < size and not self._eof:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                break
            except OSError:
                self._eof = True
                break
            if not chunk:
                self._eof = True
                break
            self._buf += chunk
        data, self._buf = self._buf[:size], self._buf[size:]
        return data

    @property
    def in_waiting(self) -> int:
        """Bytes buffered plus currently available on the socket, without
        ever blocking.

        This is the member the source issue's four-member adapter
        omitted: ``console.interact()`` calls ``ser.read(max(1,
        ser.in_waiting))`` in its reader loop, so a missing or blocking
        ``in_waiting`` breaks only the interactive path -- a test suite
        that only exercises ``send_command``'s one-shot path would never
        catch it.
        """
        self._drain_nonblocking()
        return len(self._buf)

    def close(self) -> None:
        """Close the underlying socket.

        Not one of the six members ``console.py`` calls -- provided so
        whatever opened the socket (a future ticket's ``connect
        --remote``/``deploy --remote`` handler) has a symmetric way to
        close it, the way ``serve_serial`` closes its own end via
        ``ser.close()`` on the real pyserial port.
        """
        self._sock.close()

    def _drain_nonblocking(self) -> None:
        """Pull everything currently sitting in the socket's receive
        buffer into ``self._buf``, without blocking even briefly.

        Flips the socket to non-blocking mode for the duration of the
        drain and always restores this adapter's own read timeout
        afterwards, in a ``finally``, so a caller between two ``read``/
        ``readline`` calls never observes the socket in non-blocking
        mode.
        """
        if self._eof:
            return
        self._sock.setblocking(False)
        try:
            while True:
                try:
                    chunk = self._sock.recv(65536)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError:
                    self._eof = True
                    break
                if not chunk:
                    self._eof = True
                    break
                self._buf += chunk
        finally:
            self._sock.settimeout(self._timeout)


def _short_name(raw_name: str, service_type: str) -> str:
    """Recover a board's short name from ``mdns.browse()``'s own ``name``.

    ``mdns.Advertiser.register`` names every ``ServiceInfo``
    ``f"{name}.{service_type}"`` -- the TXT record never carries the
    short name at all (confirmed by reading ``server.py::_service_txt``,
    which sets only ``uid``/``role``/``common_name``/``enum``/``port``).
    Stripping the trailing zeroconf collision-rename suffix too (see
    :data:`_DEDUPE_SUFFIX_RE`) means a board renamed to ``"togov (2)"``
    is still recognized as sharing its base name with a plain
    ``"togov"``, for :func:`resolve_board`'s ambiguity check.
    """
    suffix = f".{service_type}"
    short = raw_name[: -len(suffix)] if raw_name.endswith(suffix) else raw_name
    return _DEDUPE_SUFFIX_RE.sub("", short)


def resolve_board(name: str, service_type: str, timeout: float = 2.0) -> dict:
    """Resolve ``name`` to exactly one ``{name, host, port, txt}`` via mDNS.

    Makes one ``mdns.browse(service_type, timeout)`` call and matches
    each result's recovered short name (see :func:`_short_name`) against
    ``name``. Never silently picks a result: raises :class:`ValueError`
    with a clear, actionable message on zero matches (nothing named
    ``name`` is advertising ``service_type`` -- possibly because the
    daemon hasn't finished registering yet, not necessarily that the
    board doesn't exist) or on 2+ matches, listing every ambiguous
    candidate's host and port rather than guessing.  This is the same
    "the same command must never be able to hit two different boards"
    guarantee sprint.md's Step 6 states for name collisions on the wire,
    whether that collision is a real two-boards-one-name clash (grouped
    together via the zeroconf rename-suffix strip) or a transient
    duplicate before ``mdns.browse``'s listener settles.

    A result's ``txt`` dict is passed through as-is (even if a field is
    missing, e.g. a board that hasn't finished announcing) -- this
    function only reads ``name``/``host``/``port`` for its own matching
    and error-reporting, never a TXT field.
    """
    results = mdns.browse(service_type, timeout)
    matches = [
        {
            "name": _short_name(entry.get("name", ""), service_type),
            "host": entry.get("host"),
            "port": entry.get("port"),
            "txt": entry.get("txt") or {},
        }
        for entry in results
        if _short_name(entry.get("name", ""), service_type) == name
    ]
    if not matches:
        raise ValueError(f"no board named '{name}' found advertising {service_type}")
    if len(matches) > 1:
        candidates = ", ".join(f"{m['host']}:{m['port']}" for m in matches)
        raise ValueError(
            f"multiple boards named '{name}' found advertising "
            f"{service_type}: {candidates}"
        )
    return matches[0]


def _txt_field(txt: dict, key: str) -> str:
    """Return ``txt[key]`` as a ``str``, or ``""`` if missing/empty/``None``.

    ``server.py::_service_txt`` always writes a ``str`` (even converting
    ``enum`` with ``str(...)``), but this helper does not trust that --
    a stubbed ``mdns.browse()`` in a test, or a future TXT producer, may
    hand back ``None`` or a non-``str`` value, and a bare ``value or ""``
    would (wrongly) treat the *string* ``"0"`` as present but an *int*
    ``0`` as missing.
    """
    value = txt.get(key)
    return "" if value in (None, "") else str(value)


def _looks_like_ip(value: str) -> bool:
    """True if ``value`` parses as an IPv4 or IPv6 literal."""
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, value)
            return True
        except OSError:
            continue
    return False


def _reverse_lookup(host: str) -> str:
    """Return a display hostname for ``host``, or ``host`` unchanged.

    ``list --remote``'s HOST column reads better as ``null`` than
    ``192.168.4.50``, so an IP address is reverse-resolved (PTR) to a
    name; a host with no PTR record falls back to the address unchanged.
    A value that is already a name (``mdns.browse``'s ``.local`` fallback
    when no address resolved) is tidied by trimming a trailing dot and a
    ``.local`` suffix rather than looked up.

    Display-only: ``resolve_board`` (the ``connect``/``deploy --remote``
    path) keeps returning the raw address it got from mDNS, so a socket
    is always opened against the literal the daemon advertised -- never
    against a name this best-effort lookup produced.
    """
    if not host:
        return host
    if _looks_like_ip(host):
        try:
            return socket.gethostbyaddr(host)[0]
        except OSError:
            return host
    name = host.rstrip(".")
    if name.endswith(".local"):
        name = name[: -len(".local")]
    return name or host


def list_remote(timeout: float = 2.0) -> list[dict]:
    """Browse both service types; return one row per board, HOST included.

    Calls ``mdns.browse()`` once for :data:`SERIAL_SERVICE_TYPE` and once
    for :data:`FLASH_SERVICE_TYPE`, and groups the combined results by TXT
    ``uid`` -- the one field both of a board's two registrations share
    (``server.py::_service_txt`` writes the same ``uid`` into both). A
    board that only answers on one of the two service types (its ``serve``
    process has only one listener up, or a poll caught it mid-registration)
    still produces exactly one row, built from whichever registration(s)
    were actually seen; a field missing from one is filled in from the
    other if present there.

    A result with no TXT ``uid`` at all (should not happen in practice --
    ``server.py`` always sets one, but ``browse()``'s own contract makes no
    such guarantee) falls back to grouping by its recovered short name
    instead, so two such entries are not silently merged into one row just
    because both happened to omit ``uid``.

    Each row is shaped like the existing local device table's own row dict
    (``enum``, ``name``, ``common``, ``role``, ``uid`` -- see ``cli.py``'s
    ``_device_rows``) plus a ``host`` field. Deliberately no ``port``: a
    joined row can carry two different network ports (one per service
    type), and neither is the one right value for a column that used to
    mean "the local serial device path" -- ``host`` is the new information
    worth showing instead.

    Rows are sorted by (short name, uid) for a stable, deterministic
    listing -- ``mdns.browse()``'s own result order is a dict's insertion
    order from a background listener thread, not something a caller
    should rely on.

    Never returns ``None``; nothing found on either service type is ``[]``.
    """
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for service_type in (SERIAL_SERVICE_TYPE, FLASH_SERVICE_TYPE):
        for entry in mdns.browse(service_type, timeout):
            txt = entry.get("txt") or {}
            name = _short_name(entry.get("name", ""), service_type)
            key = _txt_field(txt, "uid") or f"name:{name}"
            fields = {
                "enum": _txt_field(txt, "enum"),
                "name": name,
                "common": _txt_field(txt, "common_name"),
                "role": _txt_field(txt, "role"),
                "uid": _txt_field(txt, "uid"),
                "host": entry.get("host") or "",
            }
            if key not in grouped:
                grouped[key] = fields
                order.append(key)
            else:
                row = grouped[key]
                for field_name, value in fields.items():
                    row[field_name] = row[field_name] or value
    rows = [grouped[key] for key in order]
    rows.sort(key=lambda r: (r["name"], r["uid"]))
    # Reverse-resolve the HOST column to hostnames for display, caching so a
    # host shared by several boards is looked up once per call.
    host_cache: dict[str, str] = {}
    for row in rows:
        host = row["host"]
        if host:
            if host not in host_cache:
                host_cache[host] = _reverse_lookup(host)
            row["host"] = host_cache[host]
    return rows


# ---------------------------------------------------------------------------
# deploy_over_network -- the FLASH client protocol
# ---------------------------------------------------------------------------

#: Socket read timeout applied once connected, for every line this module
#: reads back from `serve_flash` (the header response, each `LOG` line,
#: and the terminal `OK flashed`/`ERR ...` line). Deliberately generous
#: and per-*line*, not an overall deadline for the whole exchange: a real
#: flash can legitimately pause between `LOG` lines during erase/verify,
#: so this only has to catch a connection that has gone genuinely silent
#: (or a test's scripted stall), not bound how long flashing itself may
#: take.
#:
#: The real fix for a long flash going quiet under this timeout is
#: ticket 010's streaming of pyocd's own output through `flash_hex`'s
#: `log` callback (see `flash.py::_run_streamed`) -- that keeps
#: `serve_flash` emitting `LOG` lines throughout the whole flash, which
#: is what actually resets this timeout on a normal cadence. This value
#: is bumped from the original 30s (which, per
#: docs/acceptance/003-009-multi-node-acceptance.md Finding 2, a real
#: ~450 KB flash's mass-erase-recovery path could still exceed even with
#: streaming, on slow SWD hardware) to a more generous floor purely as
#: defence in depth against an unusually slow or scripted-silent gap --
#: not a substitute for the streaming fix above.
_FLASH_READ_TIMEOUT = 90.0


def _read_line(sock: socket.socket, max_len: int = 65536) -> str | None:
    """Read one ``\\n``-terminated line from ``sock``, decoded as UTF-8.

    Returns the line without its trailing newline, or ``None`` on EOF (the
    peer closed the connection) or a read timeout (whatever timeout
    ``sock`` currently has set) before a full line arrived -- either way
    an unambiguous "no terminal line" signal, so a truncated exchange
    becomes a definite error rather than a hang. Reads one byte at a time,
    mirroring `server.py::_read_line`'s own reasoning: this module's
    protocol reads are always short header/log/result lines, so the extra
    syscalls cost nothing worth avoiding, and the pattern stays identical
    to the wire peer it's reading from.
    """
    buf = bytearray()
    while True:
        try:
            b = sock.recv(1)
        except (socket.timeout, OSError):
            return None
        if not b:
            return None
        if b == b"\n":
            return buf.decode("utf-8", "replace")
        buf.extend(b)
        if len(buf) > max_len:
            return None


def _strip_err_prefix(line: str) -> str:
    """Drop a leading ``"ERR "`` so the message prints once, not as a
    doubled ``"Error: ERR ..."``. A line that isn't `ERR`-prefixed at all
    should not happen per the protocol, but is passed through unchanged
    rather than assumed away.
    """
    return line[len("ERR "):] if line.startswith("ERR ") else line


def deploy_over_network(
    name: str,
    hex_path: str,
    target_mcu: str,
    force_relay: bool = False,
    timeout: float = 2.0,
) -> int:
    """Flash ``hex_path`` to the board named ``name`` over `_mbflash._tcp`.

    Speaks `server.py::serve_flash`'s wire protocol as a client:
    :func:`resolve_board` the name, connect, send
    ``FLASH <nbytes> sha256=<hex>[ force-relay]``, send the payload once
    the server replies ``OK send``, then read lines until a terminal one
    -- relaying every ``LOG <text>`` line to stderr as it arrives (so a
    multi-second flash shows progress, not silence-then-result) -- and
    return ``0`` on ``OK flashed`` or ``1`` on anything else, including
    every named ``ERR ...`` `serve_flash` can send (``busy``, ``relay
    refused``, ``flash disabled``, ``sha256 mismatch``, ``short
    payload``, ``auth required``) and a response to the initial header
    that isn't literally ``OK send`` (the same case an auth-gated server
    without a matching `AUTH` handshake produces: `serve_flash` sends
    ``ERR auth required`` as the very first line back, in place of
    ``OK send``).

    ``target_mcu`` is accepted for call-site symmetry with the local
    ``flash_mod.flash_hex`` this replaces in `deploy --remote` -- the
    daemon already knows its own board's MCU (`serve`'s own
    ``--target-mcu``), so nothing here is sent on the wire; this
    function never needs it beyond the parameter itself.

    Every failure -- resolution, connection, a truncated/EOF exchange, or
    any non-success terminal line -- prints one ``Error: ...`` line to
    stderr and returns ``1``, the same "print and return non-zero"
    contract every other pre-flight rejection in this codebase already
    uses (``_deploy_entry``, ``_connect_port``), so ``deploy --remote``
    can forward this return value directly as its own exit code (agent
    manual §5's 0-is-success contract) with nothing left for the caller
    to catch.

    ``sha256`` is always sent, computed over exactly the bytes read from
    ``hex_path`` -- `serve_flash` already verifies it whenever present,
    so the marginal cost of one hash call catches in-transit corruption
    on every remote flash, not only when a caller remembers to ask for it.
    """
    try:
        board = resolve_board(name, FLASH_SERVICE_TYPE, timeout)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        payload = Path(hex_path).read_bytes()
    except OSError as exc:
        print(f"Error: cannot read '{hex_path}': {exc}", file=sys.stderr)
        return 1

    digest = hashlib.sha256(payload).hexdigest()
    host, port = board["host"], board["port"]
    label = f"{board['name']} ({host}:{port})"

    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        print(f"Error: cannot connect to {label}: {exc}", file=sys.stderr)
        return 1

    try:
        sock.settimeout(_FLASH_READ_TIMEOUT)

        header = f"FLASH {len(payload)} sha256={digest}"
        if force_relay:
            header += " force-relay"
        try:
            sock.sendall((header + "\n").encode("utf-8"))
        except OSError as exc:
            print(f"Error: cannot reach {label}: {exc}", file=sys.stderr)
            return 1

        line = _read_line(sock)
        if line is None:
            print(
                f"Error: no response from {label} (connection closed or timed "
                "out) while waiting for a reply to FLASH.",
                file=sys.stderr,
            )
            return 1
        if line != "OK send":
            print(f"Error: {_strip_err_prefix(line)}", file=sys.stderr)
            return 1

        try:
            sock.sendall(payload)
        except OSError as exc:
            print(f"Error: cannot send payload to {label}: {exc}", file=sys.stderr)
            return 1

        while True:
            line = _read_line(sock)
            if line is None:
                print(
                    f"Error: no response from {label} (connection closed or "
                    "timed out) before the flash finished.",
                    file=sys.stderr,
                )
                return 1
            if line.startswith("LOG "):
                print(line[len("LOG "):], file=sys.stderr)
                continue
            if line == "OK flashed":
                return 0
            print(f"Error: {_strip_err_prefix(line)}", file=sys.stderr)
            return 1
    finally:
        sock.close()
