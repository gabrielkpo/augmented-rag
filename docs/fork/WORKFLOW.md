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
git remote add public https://github.com/<you>/gbrain.git   # the public fork
git fetch public && git branch --track public public/master
ln -sf ../../ops/fork-sync/guard-pre-push.sh .git/hooks/pre-push
```

Local settings that keep the measured behaviour (French prompts, local hooks) live in `private/` and are loaded
by the nightly job through `~/.gbrain/nightly.env`.
