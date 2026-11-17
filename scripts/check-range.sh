#!/usr/bin/env bash
# Gate every commit in a range, each against its own tree.
#
#   ./scripts/check-range.sh 0decc15..HEAD
#   ./scripts/check-range.sh HEAD~3            # the same range, spelled shorter
#
# AGENTS.md asks for a gate run on every intermediate state when a change lands
# in pieces. By hand that is a worktree per commit and the same four commands
# every time, which is where one of them gets skipped. This is that loop, once.
#
# A worktree, not a stash: the tree you are working in may be dirty, and a
# commit's gate has to read that commit's tree -- `git stash` changes what is
# under test and loses the answer if the script dies halfway.
#
# The gate is scripts/check.sh as committed in that tree, so a commit from
# before the gate existed fails here loudly instead of being skipped silently.
set -euo pipefail

spec="${1:?usage: check-range.sh <base>..<ref> (a bare revision means <base>..HEAD)}"
case "$spec" in
    *..*) ;;
    *) spec="$spec..HEAD" ;;
esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
cd "$root" || exit 1

# Captured first, not read through a process substitution: `< <(...)` exits 0
# whatever the reader said, so a typo in the range would read as "no commits".
listing="$(git rev-list --reverse "$spec")" || { echo "no such range: $spec" >&2; exit 2; }
if [ -z "$listing" ]; then
    echo "no commits in $spec" >&2
    exit 2
fi
mapfile -t commits <<<"$listing"

workroot="$(mktemp -d)"
declare -a trees=()
failures=0
cleanup() {
    local wt
    for wt in "${trees[@]}"; do
        git worktree remove --force "$wt" >/dev/null 2>&1 || true
    done
    if [ "$failures" -eq 0 ]; then
        rm -rf "$workroot"
    else
        echo "logs kept in $workroot" >&2
    fi
}
trap cleanup EXIT

for commit in "${commits[@]}"; do
    wt="$workroot/$commit"
    if ! git worktree add -q "$wt" "$commit"; then
        printf '%s: cannot add a worktree\n' "${commit:0:7}" >&2
        failures=$((failures + 1))
        continue
    fi
    trees+=("$wt")
    log="$workroot/$commit.log"
    code=0
    ( cd "$wt" && ./scripts/check.sh ) >"$log" 2>&1 || code=$?
    # The gate prints its own test count; this reads that line rather than
    # counting anything itself.
    tests="$(sed -n 's/^Ran \([0-9]*\) tests.*/\1/p' "$log" | tail -1)"
    if [ "$code" -eq 0 ]; then
        printf '%s ok   tests=%s\n' "${commit:0:7}" "${tests:-?}"
    else
        printf '%s FAIL tests=%s\n' "${commit:0:7}" "${tests:-?}" >&2
        sed -n '/^FAILED/p' "$log" >&2 || true
        tail -3 "$log" >&2
        failures=$((failures + 1))
    fi
done

if [ "$failures" -ne 0 ]; then
    printf '\n%d of %d commit(s) failed their own tree\n' "$failures" "${#commits[@]}" >&2
    exit 1
fi
printf '\nall %d commit(s) passed their own tree\n' "${#commits[@]}"
