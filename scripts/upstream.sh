#!/usr/bin/env bash
# Relay the entry check, and write the 包清单 it names.
#
#   ./scripts/upstream.sh <work dir> [<owner/repo>]      # WANTED rides the env
#
# The check answers "does this run publish, and which 版本"; this script hands
# its four frozen lines to the workflow and adds one fact of its own: the file
# the 包清单 was written to. It used to decide the 版本 and ask the fifth
# direction too -- one run's log could then print the check's `bump=0` and the
# relay's `bump=1` a few lines apart. Both questions are the check's now.
#
# What it hands over on stdout, one reader per line (pkg.sh's line_field):
#
#   upstream= / target= / bump= / pin=   -- the check's answer, relayed
#   packages=<the 包清单 file it wrote>
#
# `packages=` is the 包清单's only name: the build and release steps read this
# line instead of spelling /tmp/packages.txt, so a path that moves moves here.
#
# The check's own log goes to stderr -- `packaged <pkg>=`, `served <pkg>=`,
# `behind <pkg>=<reason>`, `pinned=` -- and the run's reader is a human.
#
# BUMP is the seam: tests point it at a stub so this runs offline, the way
# BUILD_CHECK lets build-repo.sh run without a container.
set -euo pipefail

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

work="${1:?usage: upstream.sh <work dir> [<owner/repo>]}"
# The repository this run publishes to, handed in by the caller. Reading
# GITHUB_REPOSITORY here would make the script need Actions to answer -- the
# case ADR-0004 rules out. The check asks the fifth direction about it, so it
# goes in through the one name that question reads.
repo="${2:-}"
BUMP="${BUMP:-$here/bump.py}"
mkdir -p "$work"
work="$(cd "$work" && pwd)"
check="$work/check.txt"
packages="$work/packages.txt"

# The entry check runs in every mode, including a hand-picked 版本: it is also
# the drift guard, and it is what decides this run now.
if ! RELEASE_REPO="$repo" python3 "$BUMP" --check >"$check" 2>&1; then
    cat "$check" >&2
    echo "the entry check failed" >&2
    exit 1
fi
# The log reaches the run: what was compared, and what it concluded.
cat "$check" >&2

# Four lines, one reader each. A check that exited 0 without one of them is a
# broken check, not a quiet day -- which is why none of them has a default any
# more: `line_field` names the line that is missing.
upstream="$(line_field upstream "$check")"
target="$(line_field target "$check")"
bump="$(line_field bump "$check")"
pin="$(line_field pin "$check")"

if ! python3 "$BUMP" --packages >"$packages"; then
    echo "the 包清单 could not be written" >&2
    exit 1
fi
[ -s "$packages" ] || { echo "the 包清单 names no packages" >&2; exit 1; }

# The gates downstream are `if: bump == '1'` and `[ "$pin" = 1 ]`, so anything
# else would silently skip a publish or silently not pin. Say so instead of
# letting a typo read as "nothing to do".
case "$bump" in
    0 | 1) ;;
    *) echo "bump=$bump is not 0 or 1" >&2; exit 1 ;;
esac
case "$pin" in
    0 | 1) ;;
    *) echo "pin=$pin is not 0 or 1" >&2; exit 1 ;;
esac

printf 'upstream=%s\n' "$upstream"
printf 'target=%s\n' "$target"
printf 'bump=%s\n' "$bump"
printf 'pin=%s\n' "$pin"
printf 'packages=%s\n' "$packages"
