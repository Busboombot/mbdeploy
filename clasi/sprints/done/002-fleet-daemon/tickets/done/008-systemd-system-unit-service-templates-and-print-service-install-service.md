---
id: 008
title: Systemd system-unit service templates and --print-service/--install-service
status: done
use-cases:
- SUC-007
depends-on:
- '007'
github-issue: ''
issue: mbdeploy-serve-a-network-facing-micro-bit-fleet-daemon.md
completes_issue: true
---
<!-- CLASI: Before changing code or making plans, review the SE process in CLAUDE.md -->

# Systemd system-unit service templates and --print-service/--install-service

## Description

Add `--print-service` / `--install-service` to the `serve` subparser
(Ticket 007), plus `--system`/`--user` to select the unit scope, and the
systemd unit template itself under `src/mbdeploy/service/`.

**Default is the system unit** — this is the stakeholder's binding
deployment decision, restated in `sprint.md`'s Scope and Migration
Concerns: Docker Swarm services support neither `--device` nor
`--privileged` (ruling out a Swarm-managed alternative), and Nolanet's
`Linger=no` means a systemd **user** unit would not start at boot. So
`--install-service` with no `--system`/`--user` flag installs to
`/etc/systemd/system/mbdeploy.service` (requires root/sudo — document
this, don't silently fail); `--user` is an explicit opt-in that installs
to `~/.config/systemd/user/mbdeploy.service` instead, for a non-Nolanet
workstation deployment where that tradeoff is fine.

**The unit must bake in an explicit `WorkingDirectory`** and the exact
`ExecStart` invocation, including the resolved `--config` path — `serve`
is CWD-relative like every other subcommand, and a service manager gives
a process no useful CWD by default. `WorkingDirectory` is the directory
`mbdeploy serve` was invoked from (or an explicit `--config`'s parent, if
that's a clearer semantic — implementer's call, but document the chosen
rule in the template's generation code).

**When a token is configured, `ExecStart` uses `--token-file`, never a
literal `--token`** — even if the operator ran `--install-service`
with `--token` on the same command line, the generated unit must convert
that to writing the token out to a file and referencing it with
`--token-file` in `ExecStart` (or refuse and instruct the operator to
use `--token-file` in the first place — implementer's choice, but a
literal secret must never land in the generated unit's `ExecStart`,
matching the whole point of Ticket 007's `--token-file` feature).

**`--print-service`** renders the same unit content to stdout without
writing anything — useful for review before installing, or for a
different provisioning mechanism (e.g. Ansible) to consume.

A macOS launchd plist may be included if it's a small, low-risk
addition on top of the systemd template's logic — not required, and
skip it rather than let it expand this ticket's scope if it turns out to
need meaningfully different logic.

## Acceptance Criteria

- [x] `mbdeploy serve --print-service --system` emits valid systemd unit
      syntax (parseable structure: `[Unit]`, `[Service]`, `[Install]`
      sections present; `ExecStart=` is a single well-formed command
      line) to stdout, without touching the filesystem.
- [x] The emitted unit's `ExecStart` includes the resolved `--config`
      path and every other flag passed to `serve` that isn't a
      service-management flag itself (`--print-service`/
      `--install-service`/`--system`/`--user` are naturally excluded from
      `ExecStart`, since they don't apply to the running daemon).
- [x] `WorkingDirectory=` is set to an explicit absolute path, never a
      relative one.
- [x] `mbdeploy serve --install-service --system` writes to
      `/etc/systemd/system/mbdeploy.service` (path itself must be
      overridable/mockable in tests — do not hardcode `/etc/...` writes
      into an untestable corner; inject the target path so tests can
      redirect it to a temp directory).
- [x] `mbdeploy serve --install-service --user` writes to
      `~/.config/systemd/user/mbdeploy.service` instead (same
      path-injection requirement for testability).
- [x] With neither `--system` nor `--user` given, `--install-service`
      defaults to the **system** path — this is the one behavior this
      ticket must get right per the stakeholder's binding decision;
      write a test that asserts the default explicitly, not just the
      `--system` case.
- [x] When a token is configured via `--token` at install time, the
      generated unit's `ExecStart` contains `--token-file <path>`, never
      `--token <secret>` — grep the generated content in a test to prove
      the literal secret string never appears in it.
- [x] `pyproject.toml`'s `[tool.hatch.build.targets.wheel] artifacts`
      list includes the new `src/mbdeploy/service/` template file(s),
      alongside the existing `agent_manual.md` entry.

## Implementation Plan

**Approach**: Store the unit as a template file under
`src/mbdeploy/service/mbdeploy.service.template` (or similar), loaded
via `importlib.resources` the same way `_read_agent_manual()`
(`cli.py:33-37`) already loads `agent_manual.md` — reuse that pattern
rather than inventing a new resource-loading mechanism. Render it with
`str.format()` or a small manual substitution (no new templating
dependency needed for one file), filling in `WorkingDirectory`,
`ExecStart`, and `Description`. `--install-service`'s target path is a
function of `--system`/`--user` and, for testability, an injectable base
path (e.g. a module-level constant or a parameter defaulting to
`/etc/systemd/system` / `~/.config/systemd/user`, overridable in tests
via monkeypatch rather than actually writing to `/etc` during `pytest`).

**Files to create**: `src/mbdeploy/service/mbdeploy.service.template` (or
`.service.j2`-style name if a lightweight templating convention is
preferred — keep it simple, this is one file with a handful of
substitutions).

**Files to modify**: `src/mbdeploy/cli.py` (add `--print-service`,
`--install-service`, `--system`, `--user` to the `serve` subparser from
Ticket 007; rendering/installation logic), `pyproject.toml` (artifacts
list).

**Files to modify (tests)**: `tests/test_serve_cli.py` (from Ticket 007)
or a new `tests/test_service_install.py`.

**Testing plan**: Render tests against `--print-service` output
(string-level assertions: sections present, `ExecStart` contains
expected substrings, `WorkingDirectory` is absolute). Install-path tests
monkeypatch the injectable base path to a `tmp_path` fixture and assert
file content and location for `--system` (explicit), `--user`
(explicit), and neither (default-to-system). A specific test constructs
a `serve --install-service --token <secret>` invocation and asserts the
written unit file does not contain `<secret>` as a literal substring,
but does contain `--token-file` and a path to a file that itself
contains the secret.

**Documentation updates**: Deferred per sprint scope, same as Ticket
007 — `agent_manual.md`/README `serve` documentation lands in sprint
003. This ticket's own template file and CLI help text
(`--install-service`'s `help=` string) are the only user-facing text
this ticket is responsible for.
