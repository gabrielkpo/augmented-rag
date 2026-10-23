# Fork workflow: one public repository, one private overlay

This fork is maintained from two repositories that share one history.

```
garrytan/gbrain            upstream
  └── <public fork>        code, generic ops tooling, research write-up      (branch: public)
        └── <private repo> the same history + a private layer on top          (branch: master)
                           brain content export, question benches, results,
                           measured prompts, local settings (private/)
```

The machine that serves the brain runs `master` of the private repository. `master` is always the public
branch plus commits that touch private paths only.

## The one rule

A commit touches either public code or private data, never both. Private paths are listed in
[`ops/fork-sync/private-paths.txt`](../../ops/fork-sync/private-paths.txt). The pre-push guard refuses to send
anything that breaks the rule to the public remote.

## Day to day

| I want to | Do |
|---|---|
| Change code | Work on `public` (a worktree is convenient), commit, then `ops/fork-sync/sync.sh` |
| Add or change private data | Commit on `master`, paths from `private-paths.txt` only, push to the private remote |
| Take upstream changes | `ops/fork-sync/sync.sh --upstream` (merges upstream into `public`, then `public` into `master`) |
| Check nothing private went public | `ops/fork-sync/guard-pre-push.sh --check <range>` |

`sync.sh` pushes `public` to the public remote, merges it into `master`, and pushes `master` to the private
remote. It never pushes `master` to the public remote.

## Setup on a new clone of the private repository

```bash
git remote add public https://github.com/<you>/augmented-rag.git   # the public fork
git fetch public && git branch --track public public/master
ln -sf ../../ops/fork-sync/guard-pre-push.sh .git/hooks/pre-push
```

Local settings that keep the measured behaviour (French prompts, local hooks) live in `private/` and are loaded
by the nightly job through `~/.gbrain/nightly.env`.

## Public repository rules

The public fork was renamed from `gabrielkpo/gbrain` to `gabrielkpo/augmented-rag` on 23 October 2026. GitHub
redirects the old URL, so existing remotes and the private fork keep working; point them at the new name when
convenient (`git remote set-url public https://github.com/gabrielkpo/augmented-rag.git`).

The public fork follows the owner's GitHub conduct guide:

- `master` is protected by the `protect-main` ruleset: no direct push, no force-push, a pull request with green
  checks (`test`, `gitleaks`, `semantic-title`), squash merge only. `sync.sh` therefore cannot push `public` straight
  to the public `master`: push the `public` branch as a feature branch, open a pull request, and merge it once green.
- In a clone of the public fork, run `gh repo set-default gabrielkpo/augmented-rag` once: without it, `gh pr create`
  opens the pull request on upstream `garrytan/gbrain`.
- Upstream's workflows run on upstream's paid runners and are disabled in this repository's Actions settings.
  After an upstream merge that adds a workflow, disable it too (`gh workflow disable <name>`). The fork's own CI
  lives in `.github/workflows/ci.yml`, `gitleaks.yml`, `pr-title.yml` and `codeql.yml`.
- `README.md` is this fork's own. When an upstream merge conflicts on it, keep this fork's version; upstream's
  README stays readable on [garrytan/gbrain](https://github.com/garrytan/gbrain#readme).

## Schema migrations

The doc2query table is migration **v265** (`src/core/schema-migrations/v265-chunk-index-text.ts`), numbered in
upstream's sequence. An upstream release that ships its own v265 will collide: renumber this fork's migration to
the next free version during that merge, regenerate the registry (`bun run build:schema-migrations`), and check that
a brain already at the old number does not run it twice.
