#!/usr/bin/env bash
# Pre-push guard: nothing private reaches the public remote.
#
# Installed as .git/hooks/pre-push in the private repository. When the push goes to
# the public remote (git config forksync.publicRemote, default "public"), it refuses:
#   - pushing the private branch (forksync.privateBranch, default "master");
#   - any commit in the pushed range that touches a path listed in private-paths.txt;
#   - any added line matching a term of private/denylist.txt, when that file exists.
#
# Manual use: ops/fork-sync/guard-pre-push.sh --check <rev-range>
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
paths_file="$here/private-paths.txt"
denylist="$root/private/denylist.txt"
public_remote="$(git config forksync.publicRemote || echo public)"
private_branch="$(git config forksync.privateBranch || echo master)"

patterns="$(grep -v -E '^\s*(#|$)' "$paths_file")"

check_range() {
  local range="$1" bad=0
  local files
  files="$(git log --format= --name-only $range | sort -u | grep -v '^$' || true)"
  if [ -n "$files" ]; then
    local hits
    hits="$(printf '%s\n' "$files" | grep -E -f <(printf '%s\n' "$patterns") || true)"
    if [ -n "$hits" ]; then
      echo "fork-sync guard: private paths in $range:" >&2
      printf '  %s\n' $hits >&2
      bad=1
    fi
  fi
  if [ -f "$denylist" ]; then
    local terms
    terms="$(git log -p --format= $range | grep -E '^\+' | grep -v '^+++' \
      | grep -i -o -F -f <(grep -v -E '^\s*(#|$)' "$denylist") | sort -u || true)"
    if [ -n "$terms" ]; then
      echo "fork-sync guard: denylisted terms added in $range:" >&2
      printf '  %s\n' $terms >&2
      bad=1
    fi
  fi
  return $bad
}

if [ "${1:-}" = "--check" ]; then
  check_range "${2:?usage: --check <rev-range>}" && echo "fork-sync guard: $2 is clean"
  exit $?
fi

remote_name="${1:-}"
[ "$remote_name" = "$public_remote" ] || exit 0

zero=0000000000000000000000000000000000000000
status=0
while read -r local_ref local_sha remote_ref remote_sha; do
  [ "$local_sha" = "$zero" ] && continue   # deleting a branch
  if [ "$local_ref" = "refs/heads/$private_branch" ]; then
    echo "fork-sync guard: refusing to push $private_branch (the private branch) to $public_remote" >&2
    status=1; continue
  fi
  if [ "$remote_sha" = "$zero" ]; then
    range="$local_sha --not --remotes=$public_remote"
  else
    range="$remote_sha..$local_sha"
  fi
  check_range "$range" || status=1
done
[ $status -eq 0 ] || echo "fork-sync guard: push refused. Move private changes to $private_branch only." >&2
exit $status
