# mbdeploy — Agent Manual

This manual is written for AI coding agents (and power users) driving
`mbdeploy` non-interactively. It documents the full command surface, the
device model, exit-code contract, and copy-paste recipes for the common
build-and-flash workflows on a micro:bit fleet.

If you only need a quick reminder, run `mbdeploy --help` or
`mbdeploy <subcommand> --help`. This document is the complete reference.

---

## 1. What mbdeploy does

`mbdeploy` builds micro:bit firmware and flashes it to one or more
connected micro:bit devices over USB using [pyOCD](https://pyocd.io/).
It maintains a small JSON **device registry** so that boards can be
addressed by a stable, human-friendly name or number instead of their
long hardware UID.

A typical fleet has two kinds of boards:

- **Robots / end devices** (e.g. role `Nezha2`) — the boards you normally
  flash.
- **Relays / bridges** (role contains `RELAY` or `BRIDGE`, e.g.
  `RADIOBRIDGE`) — radio gateways that you usually do **not** want to
  reflash by accident. `mbdeploy` refuses to deploy to a relay unless you
  pass `--force-relay`.

---

## 2. Command surface

```
mbdeploy [--version] [--agent] <subcommand> [options]
```

### Top-level flags

| Flag        | Effect |
|-------------|--------|
| `--version` | Print the installed mbdeploy version and exit. |
| `--agent`   | Print this agent manual to stdout and exit. |
| `-h`, `--help` | Print short usage and exit. |

`--version` and `--agent` are handled before any subcommand, so
`mbdeploy --version` and `mbdeploy --agent` work without naming a
subcommand.

### Subcommands

| Subcommand | Purpose |
|------------|---------|
| `build`    | Compile the micro:bit firmware. |
| `deploy`   | Flash firmware to a micro:bit device. |
| `list`     | List detected devices (fast; uses the saved registry for names). |
| `probe`    | Actively probe every connected device and update the registry. |

---

## 3. The device registry

The registry is a JSON file, by default `config/devices.json` (relative to
the current working directory). Override it with `--config PATH` on the
`deploy`, `list`, and `probe` subcommands.

Each entry is keyed by the board's UID and carries:

| Field         | Meaning |
|---------------|---------|
| `uid`         | Hardware unique id (40–52 hex chars). Stable forever. |
| `enum`        | Small integer assigned once; never reused or changed. |
| `port`        | `/dev/cu.*` serial port. Refreshed on every `probe`. |
| `role`        | Device type from its `DEVICE:` announcement (e.g. `Nezha2`, `RADIOBRIDGE`). |
| `common_name` | Friendly name (preferred for display and addressing). |
| `device_name` | Secondary name. |
| `serial`      | Serial reported in the announcement. |

Registry invariants worth knowing as an agent:

- **Entries are never deleted.** A board that was probed once stays in the
  file even when unplugged.
- **`port` is always refreshed** by `probe`; identity fields (`role`,
  `common_name`, …) are **preserved** if a probe can't read a fresh
  announcement (port busy, no firmware, timeout).
- **`enum` is assigned once** and is stable for a given UID.

Because of this, `list` is cheap and trustworthy for names, but `port`
values are only as fresh as the last `probe`.

---

## 4. Addressing a device (target resolution)

The `deploy` subcommand takes an optional positional `target`. It is
resolved in this precedence order:

1. **All digits** → matched against `enum`. Example: `2`
2. **Contains `/`** (e.g. starts with `/dev/`) → matched against `port`.
   Example: `/dev/cu.usbmodem1234`
3. **40–52 hex chars** → matched against `uid`.
4. **Anything else** → case-insensitive match on `common_name`, then
   `device_name`. Example: `gutov`

If `target` is omitted, `mbdeploy` **auto-picks** the unique non-relay
device in the registry. If there are zero or more than one non-relay
devices, it errors and asks you to be explicit.

---

## 5. Exit codes

`mbdeploy` follows the standard contract: **`0` = success, non-zero =
failure.** Always check the exit code rather than scraping stdout.

Common non-zero cases for `deploy`:

- Target token matched no registry entry.
- Resolved device is a relay and `--force-relay` was not given.
- Resolved device is not currently connected (not in the live probe list).
- The build step (`--build` / `--clean`) failed.
- `pyocd flash` failed *and* the automatic mass-erase recovery also failed
  (see §6.5), or `pyocd reset` returned non-zero.

Error messages are written to **stderr**; normal output goes to stdout.

---

## 6. Recipes

### 6.1 Discover the fleet

Always probe first when ports may have changed (e.g. after replugging):

```bash
mbdeploy probe
mbdeploy list
```

`probe` opens each serial port and updates names/ports; `list` is a fast
read of the saved registry merged with the current live probes.

### 6.2 Build, then deploy to the only robot

When exactly one non-relay device is connected and registered:

```bash
mbdeploy build
mbdeploy deploy --build
```

`deploy --build` compiles first, then flashes. Use `deploy` alone to flash
a pre-built `MICROBIT.hex`.

### 6.3 Deploy to a specific device

By enum:

```bash
mbdeploy deploy 2
```

By friendly name:

```bash
mbdeploy deploy gutov
```

By port or UID:

```bash
mbdeploy deploy /dev/cu.usbmodem1234
mbdeploy deploy F1A2...  # full 40–52 hex UID
```

### 6.4 Clean build before deploying

```bash
mbdeploy deploy gutov --clean
```

`--clean` implies a build, then flashes.

### 6.5 Locked / protected devices (automatic recovery)

If a board's nRF52833 is in a locked or protected state (APPROTECT set, or a
protected SoftDevice region at address `0x0`), the flash algorithm rejects
every erase and `deploy` would otherwise fail with something like
`flash erase sector failure (... result code 0x67)`.

`deploy` handles this automatically: when the initial `pyocd flash` fails, it
runs a CTRL-AP **mass erase** (`pyocd erase --mass`, i.e. `ERASEALL`) — the
only operation that clears APPROTECT — and then retries the flash once. You
don't need to intervene. If the mass erase itself fails, `deploy` aborts with
a non-zero exit code rather than retrying blindly.

To recover a board manually (e.g. outside `deploy`):

```bash
pyocd erase -t nrf52833 -u <uid> --mass
pyocd flash -t nrf52833 -u <uid> MICROBIT.hex
pyocd reset -t nrf52833 -u <uid>
```

### 6.6 Flash a relay on purpose

Relays are guarded. Override deliberately:

```bash
mbdeploy deploy bridge1 --force-relay
```

### 6.7 Flash a custom hex / non-default MCU

```bash
mbdeploy deploy 2 --hex build/MICROBIT.hex --target-mcu nrf52833
```

### 6.8 Use a non-default registry location

```bash
mbdeploy probe  --config /path/to/devices.json
mbdeploy deploy 2 --config /path/to/devices.json
```

---

## 7. Build options (`build` and `deploy --build`)

| Option           | Effect |
|------------------|--------|
| `--clean`        | Clean before building (on `deploy`, implies `--build`). |
| `--verbose`      | Show full build output. |
| `-j N`           | Run the build with N parallel jobs. |
| `--build-cmd CMD`| Override the build command (`build` subcommand only). |

---

## 8. Agent operating tips

- **Probe before deploy** if you have any doubt about which ports are live;
  `port` in the registry is only as current as the last `probe`.
- **Prefer `enum` or `common_name`** over `port` when scripting — ports can
  shift across reconnects, enums and names do not.
- **Trust the exit code.** Don't infer success from stdout text.
- **Never reflash a relay implicitly.** If you intend to, say so explicitly
  with `--force-relay`; otherwise let the guard protect the gateway.
- **Disambiguate.** If auto-pick errors as "ambiguous", pass an explicit
  target rather than retrying the bare command.
