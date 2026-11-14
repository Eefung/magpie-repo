#!/usr/bin/env bash
# Run every gate CI's test job runs, from any directory.
#
#   ./scripts/check.sh
#
# Three gates: the offline tests, shellcheck over every shell script under
# scripts/, and actionlint over .github/workflows.
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
# What this cannot reach: the package build. build-check.sh's root branch
# (useradd / su / chown) only exists inside the publish job's container, so a
# change there is proven by a publish run rather than by this script.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/magpie-repo"

SHELLCHECK_VERSION=0.11.0
SHELLCHECK_SHA256=8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198
ACTIONLINT_VERSION=1.7.12
ACTIONLINT_SHA256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8

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

shellcheck_bin() {
    local dir="$cache/shellcheck-v$SHELLCHECK_VERSION"
    local tar="$cache/shellcheck-v$SHELLCHECK_VERSION.tar.xz"
    if [ -x "$dir/shellcheck" ]; then
        printf '%s\n' "$dir/shellcheck"
        return 0
    fi
    mkdir -p "$cache" || return 1
    fetch "https://github.com/koalaman/shellcheck/releases/download/v$SHELLCHECK_VERSION/shellcheck-v$SHELLCHECK_VERSION.linux.x86_64.tar.xz" \
        "$SHELLCHECK_SHA256" "$tar" || return 1
    tar -xJf "$tar" -C "$cache" || return 1
    printf '%s\n' "$dir/shellcheck"
}

actionlint_bin() {
    local dir="$cache/actionlint-v$ACTIONLINT_VERSION"
    local tar="$cache/actionlint-v$ACTIONLINT_VERSION.tar.gz"
    if [ -x "$dir/actionlint" ]; then
        printf '%s\n' "$dir/actionlint"
        return 0
    fi
    mkdir -p "$cache" "$dir" || return 1
    fetch "https://github.com/rhysd/actionlint/releases/download/v$ACTIONLINT_VERSION/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz" \
        "$ACTIONLINT_SHA256" "$tar" || return 1
    tar -xzf "$tar" -C "$dir" actionlint || return 1
    printf '%s\n' "$dir/actionlint"
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
elif sc="$(shellcheck_bin)"; then
    # -x follows `source`, -P resolves the `lib/pkg.sh` paths against scripts/.
    "$sc" -x -P "$here" "${shells[@]}" || failed "shellcheck"
else
    failed "shellcheck (could not obtain the pinned copy)"
fi

heading "actionlint"
if al="$(actionlint_bin)"; then
    # Put the pinned shellcheck on PATH first: actionlint lints the workflows'
    # run: blocks with whatever shellcheck it finds, and that should be the
    # same one the gate above used.
    PATH="$(dirname "$(shellcheck_bin)"):$PATH" "$al" || failed "actionlint"
else
    failed "actionlint (could not obtain the pinned copy)"
fi

heading "not covered here"
cat <<'EOF'
- the package build: build-repo.sh in the Arch container, including
  build-check.sh's root branch (useradd / su / chown). A publish run proves it.
EOF

if [ "$failures" -ne 0 ]; then
    printf '\n%d gate(s) failed\n' "$failures" >&2
    exit 1
fi
printf '\nall gates passed\n'
