#!/usr/bin/env bash
# Carry the previous publish's 包文件 into this run's tree.
#
#   ./scripts/seed-tree.sh <tree> <arch> <branch>              # remote: origin
#   ./scripts/seed-tree.sh <tree> <arch> <branch> <remote>      # a path or a URL
#
#   ./scripts/seed-tree.sh repo x86_64 gh-pages
#
# The tree is cumulative and the database is rebuilt from what is in it, so the
# only thing worth carrying across is the 包文件 set: the 保留窗口 keeps the
# previous 版本 for a client whose db is still old, and an upgrade in flight
# must not 404. Every other file is either rebuilt from those (the database and
# its files list) or would be carried forward forever frozen at the version
# that last wrote it (SHA256SUMS used to be exactly that). What counts as a
# 包文件 is pkg.sh's rule, not this script's glob: the set seeded and the set
# pruned have to be one set, or a retired file stays served.
#
# The same line used to be a step body, and its decision was an exit status:
# `if git ls-remote --exit-code --heads origin "$PAGES_BRANCH"`. git answers 2
# for "no such ref" and 128 for "cannot ask" -- an unreachable host, a
# credential that no longer works, a remote that is not a repository -- and the
# `else` branch read both as "no branch yet". A transient failure at that line
# therefore seeded an empty tree, and the publish that followed force-pushed a
# repository holding one 版本 over the branch: the 保留窗口 emptied, every
# earlier 包文件 404, and the run green. Three answers need three branches, and
# only the first two are a reason to continue.
set -euo pipefail

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"

tree="${1:?usage: seed-tree.sh <tree> <arch> <branch> [<remote>]}"
arch="${2:?usage: seed-tree.sh <tree> <arch> <branch> [<remote>]}"
branch="${3:?usage: seed-tree.sh <tree> <arch> <branch> [<remote>]}"
remote="${4:-origin}"

mkdir -p "$tree"

# Captured, not consumed inline: `set -e` would end the script on 2 as well, and
# the code is the answer this whole script exists to read.
code=0
git ls-remote --exit-code --heads "$remote" "$branch" >/dev/null 2>&1 || code=$?

case "$code" in
    0)
        git fetch --depth 1 "$remote" "$branch"
        git archive FETCH_HEAD | tar -x -C "$tree" --wildcards "$arch/$(pkgfile_pattern)"
        echo "previous repository:"
        ls "$tree/$arch" || true
        ;;
    2)
        echo "no $branch branch yet, starting a fresh repository"
        ;;
    *)
        echo "cannot ask $remote for $branch: git ls-remote exited $code" >&2
        echo "refusing to start a fresh repository over an unread one" >&2
        exit 1
        ;;
esac
