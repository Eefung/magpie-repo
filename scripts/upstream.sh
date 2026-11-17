#!/usr/bin/env bash
# Relay the entry check, and ask the one question it cannot: does the 版本 this
# tree carries have a Release?
#
#   ./scripts/upstream.sh <work dir> [<owner/repo>]      # WANTED rides the env
#
# The step that calls this used to decide in its own `run:` block: `bump` from
# the check's frozen line unless a human passed a version, `target` from
# `WANTED` unless it was empty, and a `WANTED` run printed the check's `bump=0`
# and the relay's `bump=1` a few lines apart. That decision is the check's now
# (`scripts/bump.py --check`): this script relays what it decided, and adds
# only the fifth direction -- the one reason to publish the check cannot see.
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
# BUMP and RELEASE_STATE are the seams: tests point them at stubs so this runs
# offline, the way BUILD_CHECK lets build-repo.sh run without a container.
#
# The fifth direction is still here because it needs a repository name, and that
# is the caller's (the workflow passes its own $GITHUB_REPOSITORY): when the
# check found nothing to do, this asks release-state.sh whether v<carried> has a
# Release. A rebuild whose Release step failed after the branch push leaves
# every direction the check can see quiet, and the 软件源 serving a build with
# no Release; absent is the reason to publish it again. `carried=` is the 版本
# the tree holds -- not `target=`, which a pin can hold apart from it.
set -euo pipefail

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

work="${1:?usage: upstream.sh <work dir> [<owner/repo>]}"
# The repository this run publishes to, handed in by the caller. Reading
# GITHUB_REPOSITORY here would make the script need Actions to answer -- the
# case ADR-0004 rules out -- and the workflow already passes its context to
# release-state.sh as argv.
repo="${2:-}"
BUMP="${BUMP:-$here/bump.py}"
# The Release question is the fifth direction's, and it belongs to the script
# that already answers it (release-state.sh: present/absent as a value, with gh
# behind its own seam). Substituted here so this module can be driven offline.
RELEASE_STATE="${RELEASE_STATE:-$here/release-state.sh}"
mkdir -p "$work"
work="$(cd "$work" && pwd)"
check="$work/check.txt"
packages="$work/packages.txt"

# The entry check runs in every mode, including a hand-picked 版本: it is also
# the drift guard, and it is what decides this run now.
if ! python3 "$BUMP" --check >"$check" 2>&1; then
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

# The fifth direction, and the only one the check cannot see. A rebuild's 版本
# is committed by the person who re-rendered it, so once the branch push lands
# every direction the check sees is quiet -- and a Release that failed after
# that push is never asked for again, while the 软件源 serves a build whose
# Release does not exist. When the check says nothing to do, ask the question
# the release step already owns: absent is a reason to publish, present really
# is nothing to do.
#
# The question is about the build the tree holds, not about the 版本 this run
# would render: they differ when a pin is holding the tree back, and asking
# about 上游's 版本 then answers "no Release yet" for a version this run must not
# publish -- absent being a reason to publish, that answer would undo the pin on
# the next upstream move, which is the day-long rollback the pin exists to end.
if [ "$bump" = 0 ]; then
    carried="$(line_field_or carried "$check" "$target")"
    if [ -z "$repo" ]; then
        echo "no repository named: cannot ask whether v$carried has a Release (pass <owner/repo>)" >&2
    else
        state_log="$(mktemp)"
        state_code=0
        "$RELEASE_STATE" "$carried" "$repo" >"$state_log" 2>&1 || state_code=$?
        if [ "$state_code" -ne 0 ]; then
            # A lookup that failed is not "no Release" -- that is the bug
            # release-state.sh exists to keep out of the release step. This run
            # publishes nothing it cannot justify, and the next one asks again.
            cat "$state_log" >&2
            echo "cannot ask whether v$carried has a Release; publishing only what the check found" >&2
        else
            release="$(line_field release "$state_log")"
            case "$release" in
                present) ;;
                absent)
                    if [ "$carried" = "$target" ]; then
                        echo "v$carried has no Release: publishing it again" >&2
                        bump=1
                    else
                        # This run would render $target, not the 版本 it is
                        # missing a Release for: publishing is a dispatch that
                        # names $carried, and saying so is the whole repair.
                        echo "v$carried has no Release, and this run would publish v$target" >&2
                        echo "a pin is holding this tree back; dispatch $carried to publish it" >&2
                    fi
                    ;;
                *)
                    echo "the Release lookup answered '$release', not present or absent" >&2
                    rm -f "$state_log"
                    exit 1
                    ;;
            esac
        fi
        rm -f "$state_log"
    fi
fi

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
