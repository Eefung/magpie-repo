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
# (src/, pkg/, .srcdest/) is removed on every exit path, and the one thing that
# survives is the package file. makepkg's srcinfo is kept in a variable, never
# written to disk. What the caller gets instead of the directory's shape is
# three frozen lines, the last one always last:
#
#   pkgname=<pkgname>
#   pkgver=<pkgver>
#   artifact=<absolute path>
#
# They are the whole handover: build-repo.sh takes the name from them instead of
# reading PKGBUILD a second time, and this is the module that ran makepkg, so it
# is the one that knows what it built.
set -euo pipefail

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"

dir="${1:?usage: build-check.sh <package dir>}"
[ -d "$dir" ] || { echo "no such directory: $dir" >&2; exit 1; }

# Resolve before cd: callers pass the path relative to their own cwd.
pkgdir="$(cd "$dir" && pwd)"
owner="$(stat -c %u "$pkgdir")"
artifact=""
# What the trap is allowed to keep. Empty until the run has proved the payload,
# so a failure after the package file exists still leaves nothing behind.
keep=""

# A package file left by an earlier run must not be published later, so the
# directory starts empty of them. The trap keeps that true for this run's own
# leftovers: on failure nothing survives, on success exactly one file does.
rm -f "$pkgdir"/*.pkg.tar.*

cleanup() {
    rm -rf "$pkgdir/src" "$pkgdir/pkg" "$pkgdir/.srcdest"
    local file
    for file in "$pkgdir"/*.pkg.tar.*; do
        [ -e "$file" ] || continue
        if [ "$file" = "${keep:-}" ]; then continue; fi
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
# What the package needs at runtime, asked once and kept in a variable: writing
# it to .SRCINFO only to read it back would be a second copy of a value makepkg
# just printed. The read itself lives in pkg.sh, where the offline tests reach it.
srcinfo="$(run makepkg --printsrcinfo)"

# makepkg is the one that knows what the file is called (pkgname-pkgver-pkgrel-
# arch); ask it, rather than guessing by timestamp or by globbing.
artifact="$(run makepkg --packagelist)"
case "$artifact" in
    /*) ;;
    *) artifact="$pkgdir/$artifact" ;;
esac
[ -f "$artifact" ] || { echo "makepkg did not write $artifact" >&2; exit 1; }

# What the payload calls its program is the package's own fact, asked of the
# built file instead of restated here as a path -- the templates decide it.
member="$(payload_member "$artifact")" || exit 1
echo "ok: $(basename "$artifact") ships $member"

# A bare container has no GTK/WebKit, so the desktop build cannot even load
# without its runtime dependencies; installing them from the PKGBUILD is the
# point of the check, not an inconvenience it works around.
if [ "$(id -u)" -eq 0 ]; then
    mapfile -t deps < <(srcinfo_depends "$srcinfo")
    if [ "${#deps[@]}" -gt 0 ]; then
        echo "installing the package's own depends: ${deps[*]}"
        pacman -Syu --noconfirm --needed "${deps[@]}" >/dev/null
    fi
fi

pkgver="$(pkg_field PKGBUILD pkgver)"
pkgname="$(pkg_field PKGBUILD pkgname)"

# Running the program only proves it loads. The point of the check is that the
# payload reports the 版本 being packaged, so a binary that came out of the
# wrong asset or a stale SRCDEST cannot pass. pkg.sh holds that judgement now:
# it is the one that can publish a wrong 版本, so it sits where the offline
# tests can reach it. What stays here is the container-only part -- makepkg,
# the dependencies, the root switch -- and the line the caller reads.
payload_reports "$artifact" "$pkgver" >/dev/null || exit 1
echo "ok: $(basename "$artifact") reports $pkgver"

# The lines the caller reads, and the one thing the trap above keeps for it:
# only a run that reaches here leaves a file behind.
keep="$artifact"
echo "pkgname=$pkgname"
echo "pkgver=$pkgver"
echo "artifact=$artifact"
