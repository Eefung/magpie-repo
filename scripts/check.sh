#!/usr/bin/env bash
# Run every gate CI's test job runs, from any directory.
#
#   ./scripts/check.sh
#
# Five gates: the offline tests, shellcheck over every shell script under
# scripts/, ruff over the python that decides what gets published, actionlint
# over .github/workflows, and the probe -- the real makepkg + repo-add chain,
# run when this machine has those tools.
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
# What this cannot reach: build-check.sh's root branch (useradd / su / chown),
# which only exists inside the publish job's container. The rest of the package
# build -- the retention window and the database entry -- runs offline in
# tests/test_build_repo.py, against the real build-repo.sh.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/magpie-repo"

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

failures=0

heading() { printf '\n== %s\n' "$1"; }
failed() {
    failures=$((failures + 1))
    printf 'FAILED: %s\n' "$1" >&2
}

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

cd "$root" || exit 1

heading "offline tests"
python3 -m unittest discover -s "$root/tests" || failed "the offline tests"

heading "shellcheck"
shells=()
while IFS= read -r file; do
    shells+=("$file")
done < <(find "$here" -name '*.sh' -type f | sort)
if [ "${#shells[@]}" -eq 0 ]; then
    failed "shellcheck (no shell scripts found under $here)"
elif sc="$(pinned_tool shellcheck)"; then
    # -x follows `source`, -P resolves the `lib/pkg.sh` paths against scripts/.
    "$sc" -x -P "$here" "${shells[@]}" || failed "shellcheck"
else
    failed "shellcheck (could not obtain the pinned copy)"
fi

heading "ruff"
if rf="$(pinned_tool ruff)"; then
    # F is pyflakes (undefined names, unused imports), E9 is syntax, EXE001 is a
    # shebang without the executable bit. The rule set starts where the tree is
    # already clean and grows from there: bump.py decides what gets published,
    # and until this gate existed nothing linted it. --no-cache so a run leaves
    # no .ruff_cache behind.
    "$rf" check --no-cache --select F,E9,EXE001 "$here" "$root/tests" || failed "ruff"
else
    failed "ruff (could not obtain the pinned copy)"
fi

heading "actionlint"
if al="$(pinned_tool actionlint)"; then
    # Put the pinned shellcheck on PATH first: actionlint lints the workflows'
    # run: blocks with whatever shellcheck it finds, and that should be the
    # same one the gate above used. Resolved once, not a second time.
    [ -n "${sc:-}" ] && PATH="$(dirname "$sc"):$PATH"
    "$al" || failed "actionlint"
else
    failed "actionlint (could not obtain the pinned copy)"
fi

heading "probe (real makepkg + repo-add)"
# The probe was a paragraph of prose in the block below, and nothing ran it: a
# green gate said "the build chain is only proved by a publish run" while the
# chain's real tooling sat unexercised on the machine that has it. It runs when
# the tools exist and says so when they do not -- the CI runner has no makepkg.
missing=()
for tool in makepkg repo-add bsdtar; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if [ "${#missing[@]}" -eq 0 ]; then
    log="$(mktemp)"
    if "$here/probe-repo-build.sh" >"$log" 2>&1; then
        printf 'ok: %s\n' "$(grep -c '^  ok:' "$log") assertions, real makepkg and repo-add"
    else
        cat "$log" >&2
        failed "probe-repo-build.sh"
    fi
    rm -f "$log"
else
    printf 'skipped: no %s on PATH -- the real 打包 chain needs those\n' "${missing[*]}"
fi

heading "not covered here"
cat <<'EOF'
- build-check.sh's root branch (useradd / su / chown and the depends install
  behind it): it only exists inside the publish container, so a publish run
  proves it. Everything else runs without root: the offline tests drive the
  scripts against stubs and a srcinfo-shaped fixture (tests/test_build_check.py,
  tests/test_build_repo.py, tests/test_pkg_sh.py), and the probe gate above
  runs the real thing -- on a machine that has makepkg, which the runner has
  not.
- the publish steps themselves -- seeding repo/ from the previous gh-pages
  tree, the force push, the Release, the commit back to main: they only exist
  in the publish job, and a real publish is what proves them. The seed takes
  only $ARCH/*.pkg.tar.* across, so a file this repository still needs but
  does not write under that pattern would stop being served.
EOF

if [ "$failures" -ne 0 ]; then
    printf '\n%d gate(s) failed\n' "$failures" >&2
    exit 1
fi
printf '\nall gates passed\n'
