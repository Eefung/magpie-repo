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
# Which of the two it is, is decided once, in `BUILD_AS`. The privileged
# knowledge -- who builds, who owns the directory, who installs the dependencies
# -- lives behind four functions, so the container-only half can be read in one
# place and reached offline with stubs (tests/test_build_check.py).
#
# It owns its directory from start to finish. Whatever the build leaves behind
# (src/, pkg/, .srcdest/) is removed on every exit path, and the one thing that
# survives is the package file. makepkg's srcinfo is kept in a variable, never
# written to disk. What the caller gets instead of the directory's shape is two
# frozen lines, the last one always last:
#
#   pkgname=<pkgname>
#   artifact=<absolute path>
#
# They are the whole handover: build-repo.sh takes the name from them instead of
# reading PKGBUILD a second time, and this is the module that ran makepkg, so it
# is the one that knows what it built. The pkgver is printed as a log line
# beside them -- no reader ever wanted it, and a 冻结行 nothing reads is a
# promise the next person has to keep guessing about.
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

# --- who builds, and who owns the directory --------------------------------
# One question, asked once. Everything privileged sits behind these four
# functions: `adopt` takes the directory for the builder account, `as_builder`
# runs a command as it, `install_depends` installs what the package declares,
# and `hand_over` gives the directory back.
#
# It used to be four separate `[ "$(id -u)" -eq 0 ]` tests strung between a
# prelude and a trap -- they could not be read together, so the half that only
# runs inside the publish container was read by nobody. BUILD_AS names the
# adapter instead, which is what lets a test take the root path with `su`,
# `useradd`, `chown` and `pacman` stubbed on PATH. The publish run still proves
# the real ones; the seam only moves how much of the script is unreadable.
case "${BUILD_AS:-auto}" in
    auto) if [ "$(id -u)" -eq 0 ]; then BUILD_AS=root; else BUILD_AS=user; fi ;;
    root | user) ;;
    *) echo "BUILD_AS must be root or user, not '$BUILD_AS'" >&2; exit 2 ;;
esac

hand_over() {
    [ "$BUILD_AS" = root ] || return 0
    chown -R "$1" "$pkgdir"
}

adopt() {
    [ "$BUILD_AS" = root ] || return 0
    id builder >/dev/null 2>&1 || useradd -m builder
    chown -R builder "$pkgdir"
}

# Arguments go in one by one and are quoted on the way through, so nothing here
# formats a command string.
as_builder() {
    if [ "$BUILD_AS" = root ]; then
        su builder -s /bin/bash -c "$(printf '%q ' "$@")"
    else
        "$@"
    fi
}

# A bare container has no GTK/WebKit, so the desktop build cannot even load
# without its runtime dependencies; installing them from the PKGBUILD is the
# point of the check, not an inconvenience it works around. On a developer's
# machine they are already there, which is the whole difference between the two
# adapters.
install_depends() {
    [ "$BUILD_AS" = root ] || return 0
    [ "$#" -gt 0 ] || return 0
    echo "installing the package's own depends: $*"
    pacman -Syu --noconfirm --needed "$@" >/dev/null
}

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
    hand_over "$owner"
}
trap cleanup EXIT

# Keep the downloaded release binaries out of the package directory: makepkg
# would otherwise drop them next to PKGBUILD, where they would be committed.
# It lives inside the build directory on purpose: when this runs as root it is
# created before the `builder` account exists, and the chown has to reach it.
export SRCDEST="${SRCDEST:-$pkgdir/.srcdest}"
mkdir -p "$SRCDEST"

adopt

cd "$pkgdir"

as_builder makepkg --noconfirm -f --nodeps --nocheck
# What the package needs at runtime, asked once and kept in a variable: writing
# it to .SRCINFO only to read it back would be a second copy of a value makepkg
# just printed. The read itself lives in pkg.sh, where the offline tests reach it.
srcinfo="$(as_builder makepkg --printsrcinfo)"

# makepkg is the one that knows what the file is called (pkgname-pkgver-pkgrel-
# arch); ask it, rather than guessing by timestamp or by globbing.
artifact="$(as_builder makepkg --packagelist)"
case "$artifact" in
    /*) ;;
    *) artifact="$pkgdir/$artifact" ;;
esac
[ -f "$artifact" ] || { echo "makepkg did not write $artifact" >&2; exit 1; }

# What the payload calls its program is the package's own fact, asked of the
# built file instead of restated here as a path -- the templates decide it.
member="$(payload_member "$artifact")" || exit 1
echo "ok: $(basename "$artifact") ships $member"

# The dependencies the package declares, handed to whichever adapter knows what
# to do with them: the container installs them, a developer's machine already
# has them.
mapfile -t deps < <(srcinfo_depends "$srcinfo")
install_depends "${deps[@]}"

pkgver="$(pkg_field PKGBUILD pkgver)"
pkgname="$(pkg_field PKGBUILD pkgname)"

# Running the program only proves it loads. The point of the check is that the
# payload reports the 版本 being packaged, so a binary that came out of the
# wrong asset or a stale SRCDEST cannot pass. pkg.sh holds that judgement now:
# it is the one that can publish a wrong 版本, so it sits where the offline
# tests can reach it. What stays here is the container-only part -- makepkg, the
# dependencies, the `BUILD_AS` adapter -- and the line the caller reads.
payload_reports "$artifact" "$pkgver" >/dev/null || exit 1
echo "ok: $(basename "$artifact") reports $pkgver"

# The lines the caller reads, and the one thing the trap above keeps for it:
# only a run that reaches here leaves a file behind. `pkgver=` used to be
# handed over too, with no reader anywhere but a test; it is the log line
# above (`ok: ... reports $pkgver`) that says which version was built.
keep="$artifact"
echo "pkgname=$pkgname"
echo "artifact=$artifact"
