# config/ — dotconfig managed environment configuration

This directory is managed by [dotconfig](https://pypi.org/project/dotconfig/),
an environment configuration cascade manager for `.env` files.

## Quick reference

```bash
# Initialise (creates this directory structure + age encryption setup)
dotconfig init

# Load config into .env (assembles layers into a single file)
dotconfig load dev yourname         # dev deployment + local overrides
dotconfig load prod                 # prod only, no local overrides
dotconfig load dev prod alice bob   # stacked deploys + locals
dotconfig load -d dev -l yourname   # legacy flag form (still supported)

# Save .env edits back to source files
dotconfig save                      # round-trip to whatever was loaded
dotconfig save dev                  # flatten the assembly into config/dev/

# Load/save a specific file
dotconfig load -d dev --file app.yaml --stdout
dotconfig save -d dev --file app.yaml
```

## Directory layout

```
config/
  sops.yaml                    # SOPS encryption rules
  dev/
    public.env                 # Public config for "dev"
    secrets.env                # SOPS-encrypted secrets for "dev"
  prod/
    public.env                 # Public config for "prod"
    secrets.env                # SOPS-encrypted secrets for "prod"
  local/
    <username>/
      public.env               # Per-developer public overrides
      secrets.env              # Per-developer encrypted secrets (optional)
```

## How it works

`dotconfig load` assembles a single `.env` from four layers in
last-write-wins order:

1. `config/{deploy}/public.env` — shared public config
2. `config/{deploy}/secrets.env` — shared SOPS-encrypted secrets
3. `config/local/{user}/public.env` — personal public overrides
4. `config/local/{user}/secrets.env` — personal encrypted secrets (optional)

The generated `.env` contains marked sections (`#@dotconfig: public (dev)`, etc.)
so `dotconfig save` can round-trip edits back to the correct source files.

## Important notes

- **Do not edit `.env` section markers** — they are used for round-tripping.
- **`.env` is generated** — add it to `.gitignore`. The source of truth is
  this `config/` directory.
- **Secrets files are SOPS-encrypted** — use `dotconfig save` (not manual
  sops commands) to re-encrypt after editing `.env`.
- Deployment names are open-ended: dev, prod, test, staging, ci, etc.
