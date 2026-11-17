#!/usr/bin/env bash
# Decide whether this run publishes, and which 版本 it publishes.
#
#   ./scripts/upstream.sh <work dir>              # WANTED=<版本> pins one by hand
#
# The step that calls this used to decide in its own `run:` block: `bump` from
# the check's frozen line unless a human passed a version, and `target` from
# `WANTED` unless it was empty. Both halves were held only by greps over the
# YAML text, so a behaviour-identical reformat of one line turned the test job
# red, while changing `${bump:-0}` to `${bump:-1}` -- a check that never
# finished now meaning "publish" -- stayed green. A step cannot be run offline;
# this can, against a stub 上游, which is why the decision lives here.
#
# What it hands over on stdout, one reader per line (pkg.sh's line_field):
#
#   upstream=<the 版本 --check compared against>
#   target=<the 版本 this run would render>
#   bump=0|1
#   packages=<the 包清单 file it wrote>
#
# `packages=` is the 包清单's only name: the build and release steps read this
# line instead of spelling /tmp/packages.txt, so a path that moves moves here.
#
# The check's own output goes to stderr. It is a log -- `packaged <pkg>=`,
# `behind <pkg>=<reason>` -- and the run's reader is a human; the four lines
# above are what a program reads.
#
# BUMP is the one seam: tests point it at a stub so the decision runs offline,
# the way BUILD_CHECK lets build-repo.sh run without a container.
#
# One reason to publish is outside the check's reach, so it is asked here: when
# the check says bump=0, this asks release-state.sh whether v<target> has a
# Release. A rebuild whose Release step failed after the branch push leaves
# every direction the check can see quiet, and the 软件源 serving a build with
# no Release; absent is the reason to publish it again. GITHUB_REPOSITORY
# (Actions sets it) names the repo, and RELEASE_STATE is that script's seam.
set -euo pipefail

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

work="${1:?usage: upstream.sh <work dir>}"
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
# the drift guard (a committed PKGBUILD that is no longer its template's
# render), and skipping it because a human named a version would ship that
# drift with the next bump.
if ! python3 "$BUMP" --check >"$check" 2>&1; then
    cat "$check" >&2
    echo "the entry check failed" >&2
    exit 1
fi
# The log reaches the run: what was compared, and what it concluded.
cat "$check" >&2

# Absent here is a broken check, not "nothing to do" -- the caller cannot
# render a version nobody compared. line_field says which line is missing.
upstream="$(line_field upstream "$check")"

if ! python3 "$BUMP" --packages >"$packages"; then
    echo "the 包清单 could not be written" >&2
    exit 1
fi
[ -s "$packages" ] || { echo "the 包清单 names no packages" >&2; exit 1; }

wanted="${WANTED:-}"
if [ -n "$wanted" ]; then
    # A version a human asked for needs no comparison to decide: --check has
    # still run above, so the tree it is about to render into is known sound.
    echo "asked for $wanted, upstream is $upstream" >&2
    target="$wanted"
    bump=1
else
    target="$upstream"
    # An absent `bump=` means the check never finished; it does not mean
    # "publish". The defaulting is the caller's call -- here, where it is
    # visible -- and a duplicated line is still a protocol break.
    bump="$(line_field_or bump "$check" 0)"
fi

# The fifth direction, and the only one the check cannot see. A rebuild's 版本
# is committed by the person who re-rendered it, so once the branch push lands
# every direction above is quiet -- and a Release that failed after that push
# is never asked for again, while the 软件源 serves a build whose Release does
# not exist. When the others are quiet, ask the question the release step
# already owns: absent is a reason to publish, present really is nothing to do.
if [ "$bump" = 0 ]; then
    if [ -z "${GITHUB_REPOSITORY:-}" ]; then
        echo "GITHUB_REPOSITORY is not set: cannot ask whether v$target has a Release" >&2
    else
        state_log="$(mktemp)"
        state_code=0
        "$RELEASE_STATE" "$target" "$GITHUB_REPOSITORY" >"$state_log" 2>&1 || state_code=$?
        if [ "$state_code" -ne 0 ]; then
            # A lookup that failed is not "no Release" -- that is the bug
            # release-state.sh exists to keep out of the release step. This run
            # publishes nothing it cannot justify, and the next one asks again.
            cat "$state_log" >&2
            echo "cannot ask whether v$target has a Release; publishing only what the check found" >&2
        else
            release="$(line_field release "$state_log")"
            case "$release" in
                present) ;;
                absent)
                    echo "v$target has no Release: publishing it again" >&2
                    bump=1
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

# The gate downstream is `if: bump == '1'`, so anything else would silently
# skip a publish. Say so instead of letting a typo read as "nothing to do".
case "$bump" in
    0 | 1) ;;
    *) echo "bump=$bump is not 0 or 1" >&2; exit 1 ;;
esac

printf 'upstream=%s\n' "$upstream"
printf 'target=%s\n' "$target"
printf 'bump=%s\n' "$bump"
printf 'packages=%s\n' "$packages"
