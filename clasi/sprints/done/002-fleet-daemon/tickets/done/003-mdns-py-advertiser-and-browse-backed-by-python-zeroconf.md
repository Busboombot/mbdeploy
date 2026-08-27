---
id: '003'
title: 'mdns.py: Advertiser and browse backed by python-zeroconf'
status: done
use-cases:
- SUC-003
depends-on: []
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# mdns.py: Advertiser and browse backed by python-zeroconf

## Description

New `src/mbdeploy/mdns.py`, the only module in the codebase that imports
`zeroconf`. Per `sprint.md`'s Architecture (Step 3), its purpose is
narrow and self-contained: make board network services discoverable via
mDNS. Nothing outside this module should ever see a `zeroconf.Zeroconf`
or `zeroconf.ServiceInfo` object — callers deal in plain dicts and an
opaque registration handle, so an `avahi-publish`-backed implementation
could be swapped in later without touching `server.py`.

```python
class Advertiser:
    def __init__(self, bind_addr: str | None = None): ...
    def register(self, name: str, service_type: str, port: int,
                 txt: dict[str, str]) -> object: ...  # opaque handle
    def unregister(self, handle) -> None: ...
    def close(self) -> None: ...  # unregisters everything still registered

def browse(service_type: str, timeout: float = 2.0) -> list[dict]: ...
    # [{"name": ..., "host": ..., "port": ..., "txt": {...}}]
```

TXT records carry `uid`, `role`, `common_name`, `enum`, `port` (the
board's local `/dev/ttyACM*`). zeroconf stores TXT values as raw bytes on
the wire; `register` must UTF-8-encode every value going in, and
`browse` must UTF-8-decode every value coming back, so callers on both
sides only ever see `str`. Give `ServiceInfo` the host's real `.local`
hostname (`socket.gethostname()` + `.local.`) and rely on zeroconf's own
`name (2)` collision-renaming rather than reimplementing it (two
machines whose boards hash to the same five-letter name is unlikely —
5^5 = 3125 combinations — but not impossible, and zeroconf already
handles it correctly).

Add the `zeroconf` dependency to `pyproject.toml`.

This ticket is sequenced after Ticket 001 (the avahi-coexistence spike)
by design intent, not by a hard code dependency — `mdns.py`'s own tests
run entirely against a fake `Zeroconf` and don't need real hardware to
pass. If Ticket 001 threw an exception (spike failed), do not start this
ticket until that's resolved.

## Acceptance Criteria

- [x] `Advertiser.register(name, service_type, port, txt)` returns an
      opaque handle; `unregister(handle)` removes exactly that
      registration; calling `unregister` twice on the same handle does
      not raise.
- [x] `Advertiser.close()` unregisters every handle this instance ever
      registered, even if some were already individually unregistered.
- [x] TXT record round-trip: registering with
      `txt={"uid": "abc123", "role": "NEZHA2", "common_name": "gutov",
      "enum": "2", "port": "/dev/ttyACM0"}` and then browsing for the
      same service type returns a dict with those exact `str` values —
      not `bytes`, not missing keys.
- [x] `browse(service_type, timeout=2.0)` returns a list of
      `{"name", "host", "port", "txt"}` dicts; an empty result (nothing
      registered) returns `[]`, not an exception or `None`.
- [x] No caller-facing function or return value in `mdns.py`'s public
      interface (`Advertiser.__init__/register/unregister/close`,
      `browse`) takes or returns a `zeroconf.ServiceInfo` or
      `zeroconf.Zeroconf` instance.
- [x] `--bind ADDR` (Ticket 007's CLI flag) has a corresponding
      `Advertiser(bind_addr=...)` constructor parameter that, when given,
      restricts which interface/address zeroconf advertises on; when
      omitted, defaults to zeroconf's normal all-interfaces behavior.
- [x] `zeroconf` is added to `pyproject.toml`'s `dependencies`.

## Implementation Plan

**Approach**: `Advertiser` wraps a single `zeroconf.Zeroconf` instance
created in `__init__` (optionally scoped to `bind_addr` via zeroconf's
`interfaces=` constructor argument). `register` builds a
`zeroconf.ServiceInfo` from the given name/service_type/port/txt (TXT
dict UTF-8-encoded via zeroconf's own `properties=` bytes-dict
convention), calls `self._zc.register_service(info)`, and returns the
`ServiceInfo` object cast through a thin internal wrapper (or just
returns it typed as `object` at the public signature — the "free of
zeroconf types" requirement is about what callers are expected to import
and inspect, not about defeating Python's duck typing). `unregister`
calls `self._zc.unregister_service(handle)`, guarded so a
second/duplicate call doesn't raise. `close()` iterates every handle this
instance has issued and unregisters each, then calls
`self._zc.close()`. `browse` creates a short-lived `Zeroconf()` +
`ServiceBrowser` with a listener collecting `add_service` callbacks,
sleeps up to `timeout`, resolves each found service's address/port/TXT,
decodes TXT bytes to `str`, and tears down cleanly.

**Files to create**: `src/mbdeploy/mdns.py`, `tests/test_mdns.py`.

**Files to modify**: `pyproject.toml` (add `zeroconf` to
`dependencies`).

**Testing plan** (`tests/test_mdns.py`, no hardware, no real network):
- A fake/stub `zeroconf.Zeroconf` and `zeroconf.ServiceBrowser` (monkeypatch
  `mdns_mod.zeroconf.Zeroconf`/`ServiceBrowser` with test doubles) so
  `register_service`/`unregister_service`/`close` calls are recorded
  without opening a real socket.
- TXT bytes/str round-tripping: register with a `str`-valued dict, assert
  the fake `ServiceInfo.properties` (or equivalent) was built with UTF-8
  bytes; simulate a browse callback delivering bytes TXT values and
  assert `browse()`'s returned dicts have decoded `str` values.
- `register`/`unregister`/`close` handle bookkeeping: multiple
  registrations, partial unregistration, then `close()` cleans up the
  rest; double-`unregister` on the same handle doesn't raise.
- `browse()` with a fake listener that "finds" zero, one, and two
  services, asserting the returned list shape in each case.
- `bind_addr` is threaded into the constructed `Zeroconf`'s
  `interfaces=` argument when given (assert against the fake's captured
  constructor args).

**Documentation updates**: None required by this ticket — `mdns.py` has
no user-facing CLI surface of its own; the `serve` subcommand
(Ticket 007) documents the resulting behavior.
