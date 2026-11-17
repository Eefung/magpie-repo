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
#   pin=0|1  -- this run holds the tree back from 上游, so it records PINNED
#   packages=<the 包清单 file it wrote>
#
# `packages=` is the 包清单's only name: the build and release steps read this
# line instead of spelling /tmp/packages.txt, so a path that moves moves here.
#
# The check's own output goes to stderr. It is a log -- `packaged <pkg>=`,
# `behind <pkg>=<reason>` -- and the run's reader is a human; the lines above
# are what a program reads, and GLOSSARY's 冻结行 entry lists them.
#
# BUMP is the one seam: tests point it at a stub so the decision runs offline,
# the way BUILD_CHECK lets build-repo.sh run without a container.
#
# One reason to publish is outside the check's reach, so it is asked here: when
# the check says bump=0, this asks release-state.sh whether v<target> has a
# Release. A rebuild whose Release step failed after the branch push leaves
# every direction the check can see quiet, and the 软件源 serving a build with
# no Release; absent is the reason to publish it again. The repository comes in
# as the second argument -- the workflow passes its own $GITHUB_REPOSITORY --
# so nothing here needs Actions to run, and RELEASE_STATE is that script's seam.
set -euo pipefail

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

work="${1:?usage: upstream.sh <work dir> [<owner/repo>]}"
# The repository this run publishes to, handed in by the caller. Reading
# GITHUB_REPOSITORY here would make the script need Actions to answer -- the
# case ADR-0004 rules out for a lifted step body -- and the workflow already
# passes its context to release-state.sh as argv.
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
    answered=1
else
    target="$upstream"
    # An absent `bump=` means the check never finished; it does not mean
    # "publish". The defaulting is the caller's call -- here, where it is
    # visible -- and a duplicated line is still a protocol break.
    #
    # `answered` is the distinction that default would otherwise erase: the
    # fifth direction below starts from "the four directions were compared and
    # are quiet", which is not something a check that printed no conclusion
    # said. Without it, a truncated check plus an absent Release published a
    # 版本 nobody compared.
    bump="$(line_field_or bump "$check" pending)"
    if [ "$bump" = pending ]; then
        bump=0
        answered=0
        echo "the check printed no bump= line: nothing was compared, so nothing is published" >&2
    else
        answered=1
    fi
fi

# The fifth direction, and the only one the check cannot see. A rebuild's 版本
# is committed by the person who re-rendered it, so once the branch push lands
# every direction above is quiet -- and a Release that failed after that push
# is never asked for again, while the 软件源 serves a build whose Release does
# not exist. When the others are quiet -- and only when the check actually
# answered -- ask the question the release step already owns: absent is a
# reason to publish, present really is nothing to do.
#
# The question is about the build the tree holds, not about the 版本 this run
# would render: they differ when a pin is holding the tree back, and asking
# about 上游's 版本 then answers "no Release yet" for a version this run must not
# publish -- absent being a reason to publish, that answer would undo the pin on
# the next upstream move, which is the day-long rollback the pin exists to end.
carried="$(line_field_or carried "$check" "$target")"
if [ "$bump" = 0 ] && [ "$answered" = 1 ]; then
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

# Whether this run records a pin. A human naming a version is not by itself a
# rollback: re-running the version the tree already carries is how a Release
# that failed after the branch push is retried (the fifth direction), and
# writing that down as a pin would stop every later scheduled run -- behind()
# suppresses the upstream direction while the pin holds, so the run prints
# bump=0 forever, and the step that removes the file only runs on bump=1.
#
# The sign is the comparison, not the presence of an input: a target that
# differs from 上游's is a tree told to stay where it is, and a target equal to
# it is the tree moving to where 上游 already points.
if [ -n "$wanted" ] && [ "$wanted" != "$upstream" ]; then
    pin=1
else
    pin=0
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
printf 'pin=%s\n' "$pin"
printf 'packages=%s\n' "$packages"
