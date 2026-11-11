# M3c implementation record, 11 Nov 2026

Branch `gpt/m3-lx-deploy`, base `ffa2c65`. Ready for Claude review after the follow-up compatibility
fix recorded below. No real deployment, server/config edit, GitHub report, secret setup, image push
or scheduled task was run. All deployment actions in tests use FakeServer and temporary paths.

`server/lx-deploy` is now a thin launcher for `server/lxdeploy/`: layout/constants, release,
state, git, config, checks, deploy, backup, secrets and commands, with lower-level system,
static-file and reporting modules. Functions retain their code and order of operations; shared
layout and patched check/report dependencies remain owning-module attributes, not captured copies.
No cross-repository lookup, compatibility re-export facade or old implementation remains.
All modules are below 400 lines; the largest is secrets (205). The package has no import cycle.

Installation must copy the package, not just its launcher: `config_files()` adds its explicit
module list alongside `L.program`, with mode 0644. The original apply-config flow displays the
diffs, asks, backs up every old file and restores on validation failure; it now covers the package
too. The install script and real server files remain unchanged. No code runs from the fetched
checkout in normal deployment; only the owner's explicit apply-config installs code.

## Fresh checks

- All **32 original fake-server tests** pass, with only owner imports/patch targets migrated.
- One new integration test installs actual Python sources into a temporary fake checkout, runs
  the installed launcher's `--help` with no checkout PYTHONPATH, then verifies a failed module
  update restores its old bytes: **33 pass** before the separate follow-up fix.
- CLI help before/after is byte-identical. Moved executable declarations compare by AST after
  normalizing owning-module names; `config_files()` is the documented installation exception.
  Full evidence: `docs/modular/m3c/`.
- Original Compose, Caddy and ShellCheck checks pass. Caddy 2.6.2 and ShellCheck 0.10.0 were
  downloaded from Debian's official mirror and extracted only under `/tmp`; no system install.
  The Caddy harness uses --resolve to fix all its Host names to 127.0.0.1:19200. That command's
  proxy exclusions are scoped to those local fixture requests; real domains were not contacted.
- No existing assertion is skipped or weakened. Python lint checks the split modules for
  undefined names/imports; no broad style rewrites were mixed into this refactor.

## Pre-existing bug found (separate fix commit)

`resident_names()` still reads `/app/web/src/content/pack/pack.json` from Nobody's image, but
M1 moved the real pack to `/app/core/src/content/pack/pack.json` and removed web from that image.
As required by MODULAR R6, the refactor commit preserves the old path, then a separately named
fix changes only that path and adds a regression. The final result and commit are recorded below
when that follow-up is complete. This does not run secret initialization on the server.
