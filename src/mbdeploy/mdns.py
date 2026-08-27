"""mdns — mDNS advertise/browse for board network services.

The only module in the codebase that imports ``zeroconf``. Per
``sprint.md``'s Architecture (Step 3), its purpose is narrow and
self-contained: make board network services discoverable via mDNS.

Nothing outside this module ever sees a ``zeroconf.Zeroconf`` or
``zeroconf.ServiceInfo`` object. Callers deal only in plain ``str``/
``int``/``dict`` values and an opaque registration handle returned by
``Advertiser.register``, so an ``avahi-publish``-backed implementation
could be swapped in later without touching a caller such as
``server.py``.

TXT records are raw bytes on the wire, and zeroconf's own
``ServiceInfo.properties`` dict uses ``bytes`` keys and values. This
module UTF-8-encodes every TXT value going in (``register``) and
UTF-8-decodes every TXT value coming back (``browse``), so callers on
both sides only ever see ``str``.
"""

from __future__ import annotations

import socket
import time
from typing import Any

import zeroconf


def _local_ip() -> str:
    """Best-effort LAN IPv4 address (not 127.0.0.1) for this host.

    Uses the standard "connect a UDP socket, read back the local
    endpoint" trick -- no packets are actually sent. Falls back to
    resolving the host's own name if that fails (e.g. no route to the
    public internet).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def _encode_txt(txt: dict[str, str]) -> dict[bytes, bytes]:
    """UTF-8-encode a plain ``str`` TXT dict into zeroconf's byte form."""
    return {
        str(key).encode("utf-8"): str(value if value is not None else "").encode("utf-8")
        for key, value in txt.items()
    }


def _decode_txt(properties: dict[Any, Any] | None) -> dict[str, str]:
    """UTF-8-decode a zeroconf TXT ``properties`` dict back to ``str``.

    Handles a missing dict, and a value of ``None`` (zeroconf's
    representation of a TXT key with no ``=value``) -- both round-trip
    to an empty string rather than raising or losing the key.
    """
    if not properties:
        return {}
    decoded: dict[str, str] = {}
    for key, value in properties.items():
        k = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        if value is None:
            v = ""
        elif isinstance(value, bytes):
            v = value.decode("utf-8")
        else:
            v = str(value)
        decoded[k] = v
    return decoded


def _resolve_host(info: Any) -> str:
    """Best-effort address string for a discovered service.

    Prefers a resolved IP address (directly usable by a client opening
    a socket); falls back to the ``.local.`` server hostname if no
    address resolved.
    """
    parsed_addresses = getattr(info, "parsed_addresses", None)
    if callable(parsed_addresses):
        addresses = parsed_addresses()
        if addresses:
            return addresses[0]
    server = getattr(info, "server", None)
    if server:
        return str(server).rstrip(".")
    return ""


class _BrowseListener:
    """Collects ``add_service``/``remove_service`` callbacks for browse().

    Not a ``zeroconf.ServiceListener`` subclass -- zeroconf's
    ``ServiceBrowser`` works with any object exposing these three
    methods (duck typing), and this stays a plain internal class so
    tests can drive it without importing zeroconf's ABC.
    """

    def __init__(self, resolve_timeout_ms: int = 3000) -> None:
        self._resolve_timeout_ms = resolve_timeout_ms
        self.found: dict[str, Any] = {}

    def add_service(self, zc: Any, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name, timeout=self._resolve_timeout_ms)
        if info is not None:
            self.found[name] = info

    def update_service(self, zc: Any, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)

    def remove_service(self, zc: Any, type_: str, name: str) -> None:
        self.found.pop(name, None)


class Advertiser:
    """Registers/unregisters mDNS services, backed by ``python-zeroconf``.

    Public surface never takes or returns a ``zeroconf.Zeroconf`` or
    ``zeroconf.ServiceInfo`` instance: ``register``/``unregister``/
    ``close`` deal only in plain values and an opaque integer handle.
    """

    def __init__(self, bind_addr: str | None = None) -> None:
        """Create the underlying ``Zeroconf`` instance.

        ``bind_addr``, when given, restricts which interface/address
        zeroconf advertises on (threaded into zeroconf's own
        ``interfaces=`` constructor argument). When omitted, defaults
        to zeroconf's normal all-interfaces behavior.
        """
        if bind_addr:
            self._zc = zeroconf.Zeroconf(interfaces=[bind_addr])
        else:
            self._zc = zeroconf.Zeroconf()
        self._handles: dict[int, Any] = {}
        self._next_handle = 1
        self._closed = False

    def register(
        self, name: str, service_type: str, port: int, txt: dict[str, str]
    ) -> object:
        """Register a service; return an opaque handle for ``unregister``.

        ``service_type`` must be a fully-qualified zeroconf type ending
        in a domain, e.g. ``"_mbserial._tcp.local."``. The registered
        instance is named after the host's real ``.local`` hostname;
        zeroconf's own ``name (2)`` collision-renaming applies
        (``allow_name_change=True``) rather than this module
        reimplementing it.
        """
        info = zeroconf.ServiceInfo(
            service_type,
            f"{name}.{service_type}",
            addresses=[socket.inet_aton(_local_ip())],
            port=port,
            properties=_encode_txt(txt),
            server=f"{socket.gethostname()}.local.",
        )
        self._zc.register_service(info, allow_name_change=True)
        handle = self._next_handle
        self._next_handle += 1
        self._handles[handle] = info
        return handle

    def unregister(self, handle: object) -> None:
        """Unregister exactly the service ``handle`` refers to.

        A second/duplicate call on the same (already-removed) handle
        is a no-op, not an error.
        """
        info = self._handles.pop(handle, None)
        if info is not None:
            self._zc.unregister_service(info)

    def close(self) -> None:
        """Unregister every handle still registered, then shut down.

        Idempotent: a second call finds nothing left to unregister and
        does not re-close the underlying ``Zeroconf`` instance.
        """
        if self._closed:
            return
        for handle in list(self._handles):
            self.unregister(handle)
        self._zc.close()
        self._closed = True


def browse(service_type: str, timeout: float = 2.0) -> list[dict]:
    """Browse for ``service_type`` for up to ``timeout`` seconds.

    Returns a list of ``{"name", "host", "port", "txt"}`` dicts, one
    per discovered instance. Never returns ``None``; an empty result
    (nothing found) is ``[]``.
    """
    zc = zeroconf.Zeroconf()
    listener = _BrowseListener()
    zeroconf.ServiceBrowser(zc, service_type, listener)
    try:
        time.sleep(timeout)
        return [
            {
                "name": name,
                "host": _resolve_host(info),
                "port": getattr(info, "port", None),
                "txt": _decode_txt(getattr(info, "properties", None)),
            }
            for name, info in listener.found.items()
        ]
    finally:
        zc.close()
