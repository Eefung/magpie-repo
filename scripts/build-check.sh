#!/usr/bin/env bash
# Build one package directory and prove the payload runs.
#
#   ./scripts/build-check.sh packages/magpie-bin
#
# These are -bin packages: makepkg only downloads the release binaries and
# repacks them, so nothing is compiled. Works both as root inside the Arch
# container CI uses and as a normal user on a dev machine -- makepkg refuses to
# run as root, so the root case drops to an unprivileged builder account.
#
# As root it also installs the package's own `depends` before running the
# binary: that is what turns "--version prints the new version" into a real
# check that the dependency list is complete.
#
# It owns its directory from start to finish. Whatever the build leaves behind
# (src/, pkg/, .srcdest/, .SRCINFO) is removed on every exit path, and the one
# thing that survives is the package file -- because the caller is told where it
# is by the frozen line
#
#   artifact=<absolute path>
#
# instead of having to interpret the shape the directory was left in.
set -euo pipefail

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"

dir="${1:?usage: build-check.sh <package dir>}"
[ -d "$dir" ] || { echo "no such directory: $dir" >&2; exit 1; }

# Resolve before cd: callers pass the path relative to their own cwd.
pkgdir="$(cd "$dir" && pwd)"
owner="$(stat -c %u "$pkgdir")"
temp_bin=/tmp/magpie-build-check
artifact=""

# A package file left by an earlier run must not be published later, so the
# directory starts empty of them. The trap keeps that true for this run's own
# leftovers: on failure nothing survives, on success exactly one file does.
rm -f "$pkgdir"/*.pkg.tar.*

cleanup() {
    rm -rf "$pkgdir/src" "$pkgdir/pkg" "$pkgdir/.srcdest" "$pkgdir/.SRCINFO"
    rm -f "$temp_bin"
    local file
    for file in "$pkgdir"/*.pkg.tar.*; do
        [ -e "$file" ] || continue
        if [ "$file" = "$artifact" ]; then continue; fi
        rm -f "$file"
    done
    # As root, makepkg ran as `builder`; hand the directory back to whoever
    # owned it before this script touched it.
    if [ "$(id -u)" -eq 0 ]; then
        chown -R "$owner" "$pkgdir"
    fi
}
trap cleanup EXIT

# Keep the downloaded release binaries out of the package directory: makepkg
# would otherwise drop them next to PKGBUILD, where they would be committed.
# It lives inside the build directory on purpose: when this runs as root it is
# created before the `builder` account exists, and the chown has to reach it.
export SRCDEST="${SRCDEST:-$pkgdir/.srcdest}"
mkdir -p "$SRCDEST"

if [ "$(id -u)" -eq 0 ]; then
    id builder >/dev/null 2>&1 || useradd -m builder
    chown -R builder "$pkgdir"
fi

# Run makepkg as the account makepkg accepts. Arguments go in one by one and are
# quoted on the way through, so nothing here formats a command string.
run() {
    if [ "$(id -u)" -eq 0 ]; then
        su builder -s /bin/bash -c "$(printf '%q ' "$@")"
    else
        "$@"
    fi
}

cd "$pkgdir"

run makepkg --noconfirm -f --nodeps --nocheck
run makepkg --printsrcinfo > .SRCINFO

# makepkg is the one that knows what the file is called (pkgname-pkgver-pkgrel-
# arch); ask it, rather than guessing by timestamp or by globbing.
artifact="$(run makepkg --packagelist)"
case "$artifact" in
    /*) ;;
    *) artifact="$pkgdir/$artifact" ;;
esac
[ -f "$artifact" ] || { echo "makepkg did not write $artifact" >&2; exit 1; }

bin="$(bsdtar -tf "$artifact" | grep -E '^usr/bin/magpie(-cli)?$' | head -1)"
[ -n "$bin" ] || { echo "$(basename "$artifact") carries no magpie binary" >&2; exit 1; }
echo "ok: $(basename "$artifact") ships $bin"

# A bare container has no GTK/WebKit, so the desktop build cannot even load
# without its runtime dependencies; installing them from the PKGBUILD is the
# point of the check, not an inconvenience it works around.
if [ "$(id -u)" -eq 0 ]; then
    mapfile -t deps < <(sed -n 's/^[[:space:]]*depends = //p' .SRCINFO)
    if [ "${#deps[@]}" -gt 0 ]; then
        echo "installing the package's own depends: ${deps[*]}"
        pacman -Syu --noconfirm --needed "${deps[@]}" >/dev/null
    fi
fi

pkgver="$(pkg_field PKGBUILD pkgver)"

bsdtar -xOf "$artifact" "$bin" > "$temp_bin"
chmod +x "$temp_bin"
# Running it only proves it loads. The point of the check is that the payload
# still reports the version being packaged, so a binary that came out of the
# wrong asset or a stale SRCDEST cannot pass. The first dotted number it prints
# is compared exactly: a substring test would accept 0.1.412 for 0.1.41.
if ! output="$(timeout 90 "$temp_bin" --version 2>&1)"; then
    echo "the packaged binary failed to run --version:" >&2
    printf '%s\n' "$output" >&2
    exit 1
fi
reported="$(printf '%s\n' "$output" | grep -oE '[0-9]+(\.[0-9]+)+' | head -1)" || true
if [ "$reported" != "$pkgver" ]; then
    echo "the packaged binary reports '${reported:-nothing}', expected $pkgver:" >&2
    printf '%s\n' "$output" >&2
    exit 1
fi
echo "ok: $(basename "$artifact") reports $pkgver"

# The last line is the one fact the caller needs, and the one thing the trap
# above keeps for it.
echo "artifact=$artifact"
