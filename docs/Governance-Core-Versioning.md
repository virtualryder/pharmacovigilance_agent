# One versioned governance core (P0-9)

All four governed agents (pharmacovigilance, benefits, financial-aid, housing) share the **same
governance core** — the Cedar-authorized controls, the canonical evidence service, the human sign-off
gate, the provenance and identity verifiers, the deploy engine, and the runtime. That core is what makes
each vertical *governed*; it must not drift between verticals, because a fix or hardening applied to one
would otherwise silently miss the others.

## What "the core" is

The core is every governance-critical source file under `lib/`: `lib/controls/*`, `lib/engine/*`,
`lib/connector/*`, and `lib/runtime/*` (the tracked Dockerfile, requirements, agent, and helper scripts).
It is **byte-identical across all four repositories** — verified: the git-tracked `lib/` tree hashes to
the same SHA-256 in every repo.

## How it is versioned and pinned

- **`lib/CORE_VERSION`** — the single version number for the whole core (semantic version).
- **`lib/core.lock`** — a generated integrity manifest: the version, the number of core files, a combined
  `tree_sha256`, and a SHA-256 for every core file. This is what each vertical *pins*: the vertical
  carries the core at exactly the version and content recorded in its lock.
- **`lib/verify_core.py`** — recomputes the hashes and fails if `lib/` has drifted from `core.lock`
  (a modified, missing, or newly-added-but-unlocked core file). Stdlib only.
- **`lib/regen_core_lock.py`** — regenerates the lock and (optionally) bumps the version. Run this *only*
  when deliberately changing the core.

## CI enforcement

Each repo's `.github/workflows/ci.yml` runs `python lib/verify_core.py` on every push and pull request.
If a vertical's `lib/` no longer matches its pinned `core.lock`, **CI fails**. Drift is therefore
impossible to merge unnoticed — a change to the governance core is only valid if it is a deliberate,
re-locked, version-bumped change.

## The change workflow

1. Edit the core in the reference (the PV lighthouse repo).
2. `python lib/regen_core_lock.py --bump <patch|minor|major>` — re-hashes the core and bumps
   `CORE_VERSION` + rewrites `core.lock`.
3. Sync the identical core (all `lib/` files + `CORE_VERSION` + `core.lock`) to the other three verticals.
4. CI in every repo re-verifies against the new pinned version.

## Promotion path (hard single-source dependency)

This gives one *versioned* core with enforced no-drift across four repositories without a repository
restructure. The stronger form of "one core" — a genuinely single source the verticals depend on rather
than copy — is the next step and a deliberate infra decision for the adopter: publish the core as a
private package (pip/CodeArtifact) and have each vertical pin `governed-core==<version>`, or vendor it as
a **git submodule** pinned to a tag. Either way the versioning, lock, and `verify_core.py` gate defined
here remain the integrity contract; only the distribution mechanism changes.
