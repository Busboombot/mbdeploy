---
id: '006'
title: "Docs: agent manual \xA79, README/manual serve rows, --remote flag docs"
status: done
use-cases:
- SUC-010
- SUC-011
- SUC-012
depends-on:
- '003'
- '004'
- '005'
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Docs: agent manual §9, README/manual serve rows, --remote flag docs

## Description

Sprint 002 deferred all `serve`/`--remote` documentation to this sprint
(its own Step 7 "Doc lag"). This ticket closes that gap, once the
client's actual behavior (tickets 003-005) is final enough to document
accurately:

- `src/mbdeploy/agent_manual.md`: new **§9 "Serving a fleet over the
  network"** — what `serve` does, the two mDNS service types, `--remote`
  on `list`/`connect`/`deploy` with examples, and a Nolanet-style setup
  walkthrough (udev rule, `dialout` group, systemd system-unit install,
  `--token`/`--token-file`, the silent-board caveat: `role`/
  `common_name`/`device_name` are empty on unannounced boards, so
  `connect --remote` to one gets no reply/exit 1 by design). §2's
  subcommand table gains a `serve` row (also deferred from sprint 002).
- `README.md`: subcommand table gains the same `serve` row; a short
  `--remote` mention alongside the existing subcommand list.
- `--help` text for `--remote` on all three subparsers, and for `serve`
  itself if any wording is still stale from sprint 002.

## Acceptance Criteria

- [x] `agent_manual.md` has a new §9 covering `serve`, both mDNS service
      types, and `--remote` on all three subcommands with runnable
      examples.
- [x] §9 documents the silent-board behavior explicitly (no reply on
      `connect --remote` to an unannounced board is correct, not a bug).
- [x] §2's subcommand table and README's subcommand table both list
      `serve`.
- [x] `mbdeploy --agent` (which prints the bundled manual) reflects the
      new §9 after a package rebuild — spot-checked, not just the
      source file.
- [x] `mbdeploy connect --help` / `deploy --help` / `list --help` show
      `--remote`'s purpose and its `/dev/...` mutual-exclusion caveat.

## Testing

- **Existing tests to run**: any test asserting `agent_manual.md`'s
  packaged content is well-formed (if one exists) must still pass.
- **New tests to write**: none required — this is a documentation-only
  ticket; if the project has a doc-lint/table-of-contents check, run it.
- **Verification command**: `uv run pytest` (full suite, to confirm no
  doc-adjacent test regresses) plus a manual read-through of the new
  §9 against tickets 003-005's actual delivered `--help` text.

## Completion Notes

- Ran `.venv/bin/python -m pytest -q`: 327 passed (matches the sprint's
  recorded baseline), including `test_cli_flags.py`'s manual-content
  assertions. Editable install, so `mbdeploy --agent` reads
  `agent_manual.md` straight from source — spot-checked directly
  (`python -m mbdeploy.cli --agent`) and confirmed §9 and its six
  subsections render, with none of the corrected-away claims present
  (`0666` only appears negated — "do NOT write a MODE=0666 rule" — no
  literal token ever appears in an `ExecStart` example, and the system
  unit, not `--user`, is documented as the default).
- Read `server.py`, `remote.py`, `mdns.py`, and `cli.py` directly rather
  than the source issue: every `ERR` string, JSON `INFO` shape, and
  timeout value in §9.2/§9.3 is copied verbatim from `server.py`
  (`AUTH_TIMEOUT=5s`, `PAYLOAD_TIMEOUT=30s`, `PREEMPT_JOIN_TIMEOUT=2s`).
  All `--help` text for `--remote` (on `list`/`connect`/`deploy`) and
  `serve` was already accurate from tickets 003-005/002-008 — no
  wording needed to change there.
- **Finding worth flagging**: `list`/`connect`/`deploy` have no
  client-side `--token` flag, and `remote.py`'s `resolve_board`/
  `deploy_over_network`/`_cmd_connect_remote` never send an `AUTH` line
  — confirmed by reading the code and by `test_remote.py`'s own
  `"ERR auth required"` case, which is handled as just another error
  string, not authenticated around. This means `connect --remote`/
  `deploy --remote` cannot currently reach a `serve --token`/
  `--token-file` daemon at all (they get `auth required` immediately);
  only `list --remote` is unaffected, since it never opens a socket.
  Sprint 003's own architecture text (Migration Concerns) describes
  client-side token forwarding as if it shipped — it did not. Documented
  accurately in agent manual §9.2 rather than repeating the sprint
  doc's assumption; flagging here since a future ticket may want to
  add `--token` to the three client subcommands.
