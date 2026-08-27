---
id: '005'
title: 'remote.py + cli.py: --remote on deploy (FLASH client protocol)'
status: open
use-cases: [SUC-012, SUC-013]
depends-on: ['002']
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# remote.py + cli.py: --remote on deploy (FLASH client protocol)

## Description

Add `remote.deploy_over_network(name, hex_path, target_mcu,
force_relay=False, timeout=2.0) -> int` to `remote.py`: resolve the
board via `remote.resolve_board(name, "_mbflash._tcp.local.", timeout)`,
open a TCP socket to the resolved host/port, and speak `server.py`'s
`_mbflash._tcp` protocol as a client:

1. Read `hex_path`'s bytes; compute `sha256`.
2. Send `FLASH <nbytes> sha256=<hex>[ force-relay]` (append `force-relay`
   only when `force_relay` is true).
3. Expect `OK send`; on anything else, treat the line as the final
   `ERR ...` and return non-zero.
4. Send the payload bytes.
5. Read lines until a terminal line: relay every `LOG <text>` line to
   stderr as it arrives (so a multi-second flash shows progress, not
   silence-then-result); `OK flashed` → return 0; any `ERR ...` → print
   it to stderr, return 1.

Add `--remote` (`store_true`) to `deploy`'s subparser. In `_cmd_deploy`:
apply the same `/dev/...`-plus-`--remote` rejection as ticket 004's
`connect` (before any I/O); additionally reject `--remote` with no
`target` (deploy's local no-target auto-pick has no remote equivalent —
there is no local registry of remote boards to auto-pick from). When
`args.remote`, run `--build`/`--clean` exactly as today (local,
unchanged), then call `remote.deploy_over_network(...)` instead of
`flash_mod.flash_hex(...)`, forwarding `args.force_relay` and
`args.target_mcu`. Exit code mirrors `deploy_over_network`'s return,
preserving the 0-is-success contract in agent manual §5.

## Acceptance Criteria

- [ ] `deploy --remote /dev/ttyACM0 ...` and `deploy --remote` (no
      target) are both rejected before any network I/O.
- [ ] `deploy --remote <name> --hex ...` against a fake `_mbflash._tcp`
      server: success path relays `LOG` lines to stderr and returns 0
      on `OK flashed`.
- [ ] Each of `ERR busy` / `ERR relay refused — send force-relay` /
      `ERR flash disabled` / `ERR sha256 mismatch` / `ERR short payload`
      / `ERR auth required` maps to a non-zero exit with the server's
      message visible on stderr.
- [ ] `--build`/`--clean` still run locally, unchanged, before the
      network exchange starts — verified by asserting the local
      `builder.run` call happens even when `--remote` is set.
- [ ] `--force-relay` is forwarded as the `force-relay` token on the
      wire only when the flag is given.
- [ ] Local `deploy <target>` (no `--remote`) is byte-for-byte
      unaffected.
- [ ] On real Nolanet hardware (validated in ticket 009):
      `deploy --remote togov --hex MICROBIT.hex` flashes and exits 0.

## Testing

- **Existing tests to run**: existing `deploy` tests (`tests/test_devices.py`
  and/or wherever `_cmd_deploy` is covered) must pass unchanged.
- **New tests to write**: `tests/test_remote.py` — a real loopback
  socket server standing in for `serve_flash`, scripted to return each
  named `ERR` and the success sequence; `deploy_over_network`'s sha256
  computation and `force-relay` forwarding; `_cmd_deploy`'s `--remote`
  rejections and its build/clean-still-local behavior (via monkeypatch
  on `builder.run`).
- **Verification command**: `uv run pytest tests/test_remote.py
  tests/test_devices.py`
