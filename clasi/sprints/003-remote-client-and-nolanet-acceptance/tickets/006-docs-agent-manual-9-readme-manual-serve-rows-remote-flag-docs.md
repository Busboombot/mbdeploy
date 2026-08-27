---
id: '006'
title: "Docs: agent manual \xA79, README/manual serve rows, --remote flag docs"
status: open
use-cases: [SUC-010, SUC-011, SUC-012]
depends-on: ['003', '004', '005']
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

- [ ] `agent_manual.md` has a new §9 covering `serve`, both mDNS service
      types, and `--remote` on all three subcommands with runnable
      examples.
- [ ] §9 documents the silent-board behavior explicitly (no reply on
      `connect --remote` to an unannounced board is correct, not a bug).
- [ ] §2's subcommand table and README's subcommand table both list
      `serve`.
- [ ] `mbdeploy --agent` (which prints the bundled manual) reflects the
      new §9 after a package rebuild — spot-checked, not just the
      source file.
- [ ] `mbdeploy connect --help` / `deploy --help` / `list --help` show
      `--remote`'s purpose and its `/dev/...` mutual-exclusion caveat.

## Testing

- **Existing tests to run**: any test asserting `agent_manual.md`'s
  packaged content is well-formed (if one exists) must still pass.
- **New tests to write**: none required — this is a documentation-only
  ticket; if the project has a doc-lint/table-of-contents check, run it.
- **Verification command**: `uv run pytest` (full suite, to confirm no
  doc-adjacent test regresses) plus a manual read-through of the new
  §9 against tickets 003-005's actual delivered `--help` text.
