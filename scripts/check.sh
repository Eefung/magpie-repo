#!/usr/bin/env bash
# Run every gate CI's test job runs, from any directory.
#
#   ./scripts/check.sh
#
# The gates are the table below, not a list here: GATE_ROWS is what runs and in
# what order, so a new gate is one row and nothing in this file has to be kept
# in step with a sentence. The loop runs one row per gate and counts the rows
# that reported: `all gates passed` is printed only when every declared gate
# reported and none failed. Before this, that sentence was
# a claim with nothing behind it -- `failures` counts failures, never gates --
# so deleting a gate's twenty lines left the run green and the summary saying
# everything had passed, with 333 offline tests green because none of them read
# which gates this script runs.
#
# The count leaves as `gates=<ran>/<declared>`, a frozen line read through
# pkg.sh's reader. scripts/check-range.sh reads it to answer "does this tree's
# gate carry a count at all" -- it used to answer that by grepping this file's
# source for `line_field tests`, so a behaviour-identical rewrite of the call
# (`line_field "$COUNT_KEY"`) reported `tests=none` while the log carried the
# number.
#
# The shell scripts are enumerated, not listed: a hand-written glob silently
# skips a new directory, which is how scripts/lib/ went un-linted when it was
# added.
#
# Both linters come from here, pinned, never from PATH. This machine and the
# runner then check the same versions, and "the tool is not installed" stops
# meaning "the gate did not run". Each digest is the sha256 of the release
# asset as fetched at pin time; bump the version and the digest together.
#
# What a green run still cannot prove is printed with the summary, under
# `not covered here`. That list has exactly one copy -- the one a run prints --
# and this header does not restate it: the copy that used to sit here named half
# the gaps, and the half it named was the one a stub had already replaced.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/magpie-repo"

# The gate reads the probe's handover the way every other caller reads a frozen
# line -- through the one reader, not through a grep of its own.
# shellcheck source=lib/pkg.sh
source "$here/lib/pkg.sh"

# Pinned tools, one row each: name|version|sha256|asset url|member|strip.
# Each digest is the sha256 of the release asset as fetched at pin time; bump
# the version and the digest together, in the same row. `strip` is how many
# leading path components the archive carries, so extracting always lands the
# member at <cache>/<name>-v<version>/<member>.
PINNED_TOOLS=(
    "shellcheck|0.11.0|8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198|https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz|shellcheck|1"
    "ruff|0.17.5|7d261fc981a784668e19e9fa6ad437bfd95b67f2dc263fc1eab87d54d72fcc90|https://github.com/astral-sh/ruff/releases/download/0.17.5/ruff-x86_64-unknown-linux-gnu.tar.gz|ruff|1"
    "actionlint|1.7.12|8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8|https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz|actionlint|0"
)

# One row per gate: slug|the heading a reader sees. The slug names the function
# below (`gate_tests`), so a row without a body -- or a body no row runs -- is
# a mistake this file can be read for, and tests/test_check_range.py holds the
# count the loop prints. `not covered here` is not a row: it is a note, not a
# gate, and nothing counts it.
GATE_ROWS=(
    "tests|offline tests"
    "shellcheck|shellcheck"
    "ruff|ruff"
    "actionlint|actionlint"
    "probe|probe (real makepkg + repo-add)"
)

failures=0
ran=0

heading() { printf '\n== %s\n' "$1"; }
# A gate says its own failure, in one line, and returns non-zero: the loop
# counts, it does not describe. Counting inside each gate is what let a gate
# disappear without the summary noticing.
failing() { printf 'FAILED: %s\n' "$1" >&2; }

# fetch <url> <sha256> <path> -- download once, then hold the file to its digest.
fetch() {
    local url="$1" want="$2" out="$3" got
    [ -f "$out" ] || curl -sSLo "$out" "$url" || { echo "cannot download $url" >&2; return 1; }
    got="$(sha256sum "$out" | cut -d' ' -f1)"
    if [ "$got" != "$want" ]; then
        echo "sha256 mismatch for $(basename "$out"): $got" >&2
        rm -f "$out"
        return 1
    fi
}

# pinned_tool <name> -- the path to that tool, downloaded once into the cache
# and held to its digest. Both linters come through here: the two copies this
# replaced differed in five constants and one tar flag, which is how a third
# linter would have become a third copy.
pinned_tool() {
    local name="$1" row="" line version sha url member components dir archive
    local -a strip=()
    for line in "${PINNED_TOOLS[@]}"; do
        case "$line" in
            "$name|"*) row="$line"; break ;;
        esac
    done
    [ -n "$row" ] || { echo "no pinned tool named $name" >&2; return 1; }
    IFS='|' read -r _ version sha url member components <<<"$row"
    dir="$cache/$name-v$version"
    archive="$cache/$name-v$version.${url##*.}"
    if [ -x "$dir/$member" ]; then
        printf '%s\n' "$dir/$member"
        return 0
    fi
    mkdir -p "$dir" || return 1
    fetch "$url" "$sha" "$archive" || return 1
    [ "$components" = "1" ] && strip=(--strip-components=1)
    # -xf reads the compression off the file, so xz and gz need no flag here.
    tar -xf "$archive" -C "$dir" "${strip[@]}" || return 1
    printf '%s\n' "$dir/$member"
}

# --- the gates, one function each -------------------------------------------
# A gate owns its verdict: it prints its own detail, says FAILED itself when it
# failed, and returns non-zero. Everything shared -- the heading, the count, the
# summary -- belongs to the loop below.

gate_tests() {
    local tests_log tests_code tests
    # The count is this gate's own frozen line. It used to be unittest's "Ran N
    # tests" prose, sed-ed out of this log by scripts/check-range.sh: a reworded
    # line printed `tests=?` and the commit still read ok. `TextTestRunner.testsRun`
    # is the number the program itself knows, printed once here through the same
    # write gate as every other handover, and read back the same way.
    tests_log="$(mktemp)"
    python3 - "$root/tests" >"$tests_log" 2>&1 <<'PY'
import sys
import unittest

result = unittest.TextTestRunner(verbosity=2).run(
    unittest.TestLoader().discover(sys.argv[1])
)
print(f"tests={result.testsRun}")
sys.exit(0 if result.wasSuccessful() else 1)
PY
    tests_code=$?
    # One gate, one verdict. A python that crashed prints no count, so the exit
    # code and the missing line are the same failure -- reporting both printed
    # two FAILED lines for one gate and the log twice.
    if ! frozen_var tests "$tests_log" tests; then
        cat "$tests_log" >&2
        rm -f "$tests_log"
        failing "the offline tests (a broken tests= line)"
        return 1
    fi
    frozen_write tests "$tests"
    if [ "$tests_code" -ne 0 ]; then
        cat "$tests_log" >&2
        rm -f "$tests_log"
        failing "the offline tests"
        return 1
    fi
    rm -f "$tests_log"
}

gate_shellcheck() {
    local shells=() file sc
    while IFS= read -r file; do
        shells+=("$file")
    done < <(find "$here" -name '*.sh' -type f | sort)
    if [ "${#shells[@]}" -eq 0 ]; then
        failing "shellcheck (no shell scripts found under $here)"
        return 1
    fi
    if ! sc="$(pinned_tool shellcheck)"; then
        failing "shellcheck (could not obtain the pinned copy)"
        return 1
    fi
    # -x follows `source`, -P resolves the `lib/pkg.sh` paths against scripts/.
    if ! "$sc" -x -P "$here" "${shells[@]}"; then
        failing "shellcheck"
        return 1
    fi
}

gate_ruff() {
    local rf
    if ! rf="$(pinned_tool ruff)"; then
        failing "ruff (could not obtain the pinned copy)"
        return 1
    fi
    # F is pyflakes (undefined names, unused imports), E9 is syntax, EXE001 is a
    # shebang without the executable bit. The rule set starts where the tree is
    # already clean and grows from there: bump.py decides what gets published,
    # and until this gate existed nothing linted it. --no-cache so a run leaves
    # no .ruff_cache behind.
    if ! "$rf" check --no-cache --select F,E9,EXE001 "$here" "$root/tests"; then
        failing ruff
        return 1
    fi
}

gate_actionlint() {
    local al sc
    if ! al="$(pinned_tool actionlint)"; then
        failing "actionlint (could not obtain the pinned copy)"
        return 1
    fi
    # actionlint lints the workflows' run: blocks with whatever shellcheck it
    # finds on PATH, and that should be the same one the shellcheck gate used.
    # Asked for here rather than carried over from that gate: it is a cache hit,
    # and a gate that reads a variable another gate set fails when its neighbour
    # is reordered -- which is exactly the coupling the table exists to remove.
    if sc="$(pinned_tool shellcheck)"; then
        PATH="$(dirname "$sc"):$PATH"
    fi
    if ! "$al"; then
        failing actionlint
        return 1
    fi
}

gate_probe() {
    local missing=() tool log assertions
    # The probe runs when the tools exist and says so when they do not -- the CI
    # runner has no makepkg. A skip still reports, so it counts as a gate that
    # ran: what `gates=` counts is gates that reached a verdict, not gates that
    # passed.
    for tool in makepkg repo-add bsdtar; do
        command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if [ "${#missing[@]}" -ne 0 ]; then
        printf 'skipped: no %s on PATH -- the real 打包 chain needs those\n' "${missing[*]}"
        return 0
    fi
    log="$(mktemp)"
    if ! "$here/probe-repo-build.sh" >"$log" 2>&1; then
        cat "$log" >&2
        rm -f "$log"
        failing probe-repo-build.sh
        return 1
    fi
    # The count is the probe's own frozen line. It used to be
    # `grep -c '^  ok:'` over this log, which made the alignment of an echo
    # the report's whole evidence: re-indenting two of them printed "ok: 3
    # assertions" and the gate stayed green. Absent, empty, duplicated, or not
    # an integer is a failed gate: the table says `assertions` is one.
    if ! frozen_var assertions "$log" assertions; then
        cat "$log" >&2
        rm -f "$log"
        failing "probe-repo-build.sh (a broken assertions= line)"
        return 1
    fi
    printf 'ok: %s assertions, real makepkg and repo-add\n' "$assertions"
    rm -f "$log"
}

# --- the loop ---------------------------------------------------------------
# One walk over the table. `ran` is what the summary and the frozen line below
# count: it is incremented for every row that was reached, so a table that lost
# a row, or a run killed between two gates, is a number and not a silence.
gate() {
    local row="$1" slug title
    IFS='|' read -r slug title <<<"$row"
    heading "$title"
    ran=$((ran + 1))
    if ! "gate_$slug"; then
        failures=$((failures + 1))
    fi
}

cd "$root" || exit 1

for row in "${GATE_ROWS[@]}"; do
    gate "$row"
done

declared="${#GATE_ROWS[@]}"
frozen_write gates "$ran/$declared"

heading "not covered here"
cat <<'EOF'
- what `BUILD_AS=root` calls: useradd, chown, su and pacman themselves. The
  adapter and the order it runs in are driven offline against stubs
  (`tests/test_build_check.py`, `RootAdapterTest`, plus a srcinfo-shaped
  fixture in `tests/test_pkg_sh.py`), so what a publish run still proves is only
  that the real tools do what their stubs pretend. `tests/test_build_repo.py`
  drives the real `build-repo.sh` the same way, and the probe gate above runs
  the real makepkg and repo-add -- on a machine that has them, which the runner
  has not.
- the entry check's own read of the 软件源: `bump.py`'s `served_evr` against the
  database pacman reads. `tests/test_bump.py` drives it against a synthesized
  database in repo-add's format, but the bytes a real Pages response carries --
  and what Pages serves *during* a deploy -- are what a publish run proves. A
  read that fails says so and falls back to comparing against 上游 alone, which
  is a documented fallback, not a green light.
- the publish job's own plumbing -- `git ls-remote` and `git fetch` of the
  branch, `gh release create`, and the commit and push back to main: they only
  exist in the publish job, and a real publish is what proves them. The branch
  push is no longer in this list: `scripts/publish-pages.sh` owns the tree it
  publishes (`.nojekyll`, one squashed commit per release, the force push) and
  `tests/test_publish_pages.py` drives it against a local bare repository, so
  what a publish run still proves there is the token URL and Pages itself. The
  seed is not in this list either: `scripts/seed-tree.sh` owns the decision, and
  `tests/test_inventory.py` drives all three of its answers -- branch there,
  branch absent, remote unreadable -- against a local bare repository, so what
  stays unproven about it is the real previous tree it fetches. The
  Release's rules are `scripts/release.sh`'s, driven offline by
  `tests/test_release.py`. Which of create/update to run is
  `scripts/release-state.sh`'s answer, driven offline against a stub `gh` by
  `tests/test_release_state.py` -- what a publish run still proves there is that
  the real gh answers the status line this script reads.
EOF

# The verdict is a count, not an adjective: every declared gate reported, and
# none of them failed. Without the first half a gate that never ran was
# indistinguishable from one that passed.
if [ "$ran" -ne "$declared" ] || [ "$failures" -ne 0 ]; then
    printf '\n%s of %s gate(s) reported, %s failed\n' "$ran" "$declared" "$failures" >&2
    exit 1
fi
printf '\nall gates passed\n'
