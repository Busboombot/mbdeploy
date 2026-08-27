"""console — serial sessions with a micro:bit (the ``connect`` subcommand).

Two modes share one open port:

- **one-shot** — write a line, collect what comes back, exit.  The reply is
  taken to be complete once the board has gone quiet for :data:`IDLE_GAP`, so
  a multi-line answer arrives whole without waiting out the full timeout, and
  the whole exchange is still bounded by the caller's timeout.
- **interactive** — relay stdin to the board and the board's output to stdout
  until EOF or Ctrl-C.

Device output goes to stdout and status text to stderr, so a one-shot call can
be piped without the banner contaminating the data.
"""

from __future__ import annotations

import socket
import sys
import threading
import time

from mbdeploy.devices import serial

#: Seconds to let a freshly opened port settle before writing to it, matching
#: what ``devices.probe_type`` does for the HELLO handshake.
OPEN_SETTLE = 0.3

#: How long the board may stay silent, *after* it has said something, before
#: its reply is treated as finished.
IDLE_GAP = 0.4

#: Per-read block time.  Bounds how long a session takes to notice its
#: deadline or a stop request; not a user-visible timeout.
READ_TIMEOUT = 0.1

#: Grace period after stdin ends, so a piped one-liner shows the board's reply
#: instead of racing EOF.  Skipped on Ctrl-C, which means "stop now".
EOF_DRAIN = 0.4


class ConsoleError(RuntimeError):
    """A serial session could not be established."""


def open_port(port: str, baud: int, settle: float = OPEN_SETTLE):
    """Open ``port`` at ``baud`` with DTR/RTS held low, and let it settle.

    DAPLink resets the target when DTR is asserted, so the modem lines are
    cleared *before* the port is opened — connecting to a robot must not
    reboot it.
    """
    if serial is None:
        raise ConsoleError(
            "pyserial is not installed, so no serial port can be opened."
        )
    ser = serial.Serial(
        baudrate=baud, timeout=READ_TIMEOUT, dsrdtr=False, rtscts=False
    )
    ser.port = port
    ser.dtr = False
    ser.rts = False
    try:
        ser.open()
    except Exception as exc:  # serial.SerialException and friends
        raise ConsoleError(f"cannot open {port}: {exc}") from exc
    if settle:
        time.sleep(settle)
    return ser


def send_command(
    ser, message: str, timeout: float, idle_gap: float = IDLE_GAP
) -> list[str]:
    """Send ``message`` as one line and return the reply lines.

    ``timeout`` is the whole budget for the exchange: the board gets that long
    to say anything at all, and the read stops early once it has answered and
    then stayed quiet for ``idle_gap``.  A board that streams continuously is
    therefore cut off at ``timeout`` rather than hanging the command.
    """
    ser.reset_input_buffer()
    ser.write(message.encode("utf-8") + b"\n")
    ser.flush()

    lines: list[str] = []
    deadline = time.time() + timeout
    quiet_after = deadline          # nothing heard yet: wait out the full budget
    while True:
        now = time.time()
        if now >= deadline or (lines and now >= quiet_after):
            return lines
        raw = ser.readline()
        if not raw:
            continue
        text = raw.decode("utf-8", "replace").rstrip("\r\n")
        if text:
            lines.append(text)
        quiet_after = time.time() + idle_gap


def interact(ser) -> int:
    """Relay stdin to ``ser`` and ``ser`` to stdout until EOF or Ctrl-C.

    Returns the exit code for the session (always 0 — ending the session is
    not a failure).
    """
    stop = threading.Event()

    def _pump() -> None:
        while not stop.is_set():
            try:
                data = ser.read(max(1, ser.in_waiting))
            except Exception:
                break               # port went away; the main loop will notice
            if data:
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()

    reader = threading.Thread(target=_pump, name="mbdeploy-serial-read", daemon=True)
    reader.start()
    try:
        while True:
            line = sys.stdin.readline()
            if not line:            # Ctrl-D, or the end of a pipe
                break
            ser.write(line.encode("utf-8"))
            ser.flush()
        time.sleep(EOF_DRAIN)
    except KeyboardInterrupt:
        pass                        # Ctrl-C means stop now — don't linger
    finally:
        stop.set()
        reader.join(timeout=1.0)
    return 0


def relay_socket(ser, conn, stop: threading.Event) -> None:
    """Relay bytes between ``ser`` and the connected socket ``conn``.

    The network-facing sibling of :func:`interact`: same two-thread shape
    (one daemon reader thread plus the caller's own loop), but a raw byte
    pipe in both directions instead of a decoded stdin/stdout terminal —
    no line buffering, no decoding, no :data:`EOF_DRAIN` grace period.

    Returns when either side closes (socket EOF, a serial read/write error)
    or when ``stop`` is set — including from another thread, which is how
    ``serve_flash`` preempts a live session.  Neither ``ser`` nor ``conn`` is
    closed here; that stays the caller's responsibility.
    """

    def _pump() -> None:
        while not stop.is_set():
            try:
                data = ser.read(max(1, ser.in_waiting))
            except Exception:
                stop.set()          # port went away; wake the main loop too
                break
            if data:
                try:
                    conn.sendall(data)
                except Exception:
                    stop.set()      # socket went away; wake the main loop too
                    break

    reader = threading.Thread(target=_pump, name="mbdeploy-relay-read", daemon=True)
    reader.start()
    try:
        conn.settimeout(READ_TIMEOUT)
        while not stop.is_set():
            try:
                data = conn.recv(4096)
            except socket.timeout:
                continue            # just a poll interval; re-check stop
            except Exception:
                break               # socket went away
            if not data:
                break               # clean EOF: the peer disconnected
            try:
                ser.write(data)
                ser.flush()
            except Exception:
                break               # port went away
    finally:
        stop.set()
        reader.join(timeout=1.0)
