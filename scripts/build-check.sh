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
set -euo pipefail

dir="${1:?usage: build-check.sh <package dir>}"
[ -d "$dir" ] || { echo "no such directory: $dir" >&2; exit 1; }
cd "$dir"

# Keep the downloaded release binaries out of the package directory: makepkg
# would otherwise drop them next to PKGBUILD, where they would be committed.
# It lives inside the build directory on purpose: when this runs as root it is
# created before the `builder` account exists, and chown -R has to reach it.
export SRCDEST="${SRCDEST:-$PWD/.srcdest}"
mkdir -p "$SRCDEST"

run() {
    if [ "$(id -u)" -eq 0 ]; then
        id builder >/dev/null 2>&1 || useradd -m builder
        chown -R builder .
        su builder -s /bin/bash -c "$*"
    else
        bash -c "$*"
    fi
}

run "makepkg --noconfirm -f --nodeps --nocheck"
run "makepkg --printsrcinfo > .SRCINFO"

# shellcheck disable=SC2012  # names here are pkgname-pkgver-pkgrel-arch, no oddities
pkgfile="$(ls -1t ./*.pkg.tar.* | head -1)"
bin="$(bsdtar -tf "$pkgfile" | grep -E '^usr/bin/magpie(-cli)?$' | head -1)"
[ -n "$bin" ] || { echo "$pkgfile carries no magpie binary" >&2; exit 1; }
echo "ok: $pkgfile ships $bin"

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

bsdtar -xOf "$pkgfile" "$bin" > /tmp/magpie-build-check
chmod +x /tmp/magpie-build-check
timeout 90 /tmp/magpie-build-check --version
rm -f /tmp/magpie-build-check
