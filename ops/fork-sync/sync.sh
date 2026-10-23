#!/usr/bin/env bash
# Keep the public branch and the private branch in step.
#
#   ops/fork-sync/sync.sh              push `public` to the public remote, merge it into `master`,
#                                      push `master` to the private remote
#   ops/fork-sync/sync.sh --upstream   first merge upstream/master into `public`
#   ops/fork-sync/sync.sh --dry-run    print what would happen
#
# Both branches must be checked out somewhere with a clean working tree (`master` is usually the
# main checkout, `public` a worktree). Merges stop on conflict and leave them to you.
set -euo pipefail

public_remote="$(git config forksync.publicRemote || echo public)"
private_remote="$(git config forksync.privateRemote || echo origin)"
upstream_remote="$(git config forksync.upstreamRemote || echo upstream)"
private_branch="$(git config forksync.privateBranch || echo master)"
public_branch="$(git config forksync.publicBranch || echo public)"

dry=0; with_upstream=0
for a in "$@"; do
  case "$a" in
    --dry-run) dry=1 ;;
    --upstream) with_upstream=1 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done
run() { echo "+ $*"; [ $dry = 1 ] || "$@"; }

worktree_of() {
  git worktree list --porcelain | awk -v b="refs/heads/$1" '
    /^worktree /{p=substr($0,10)} /^branch /{if ($2==b) print p}'
}
pub_wt="$(worktree_of "$public_branch")"
priv_wt="$(worktree_of "$private_branch")"
[ -n "$pub_wt" ] || { echo "$public_branch is not checked out; add a worktree for it" >&2; exit 1; }
[ -n "$priv_wt" ] || { echo "$private_branch is not checked out" >&2; exit 1; }
for wt in "$pub_wt" "$priv_wt"; do
  [ -z "$(git -C "$wt" status --porcelain --untracked-files=no)" ] || { echo "uncommitted changes in $wt" >&2; exit 1; }
done

run git fetch "$public_remote"
if [ $with_upstream = 1 ]; then
  run git fetch "$upstream_remote"
  run git -C "$pub_wt" merge --no-edit "$upstream_remote/master"
fi
run git -C "$pub_wt" merge --ff-only "$public_remote/master" || true

# Guard: nothing private on the public branch, then publish it.
run "$(dirname "$0")/guard-pre-push.sh" --check "$upstream_remote/master..$public_branch"
run git -C "$pub_wt" push "$public_remote" "$public_branch:master"

# Bring the public work into the private branch.
run git -C "$priv_wt" merge --no-edit "$public_branch"
run git -C "$priv_wt" push "$private_remote" "$private_branch"
echo "fork-sync: $public_branch published, merged into $private_branch."
