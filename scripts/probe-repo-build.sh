#!/usr/bin/env bash
# Drive the build scripts against a synthesized package: real makepkg, real
# repo-add, no network, no container, no root.
#
#   ./scripts/probe-repo-build.sh            # this repository
#   ./scripts/probe-repo-build.sh <repo root>
#
# What it proves: makepkg names and builds the file, build-check hands over
# pkgname= / pkgver= / artifact=, build-repo prunes to KEEP *版本* and hands the
# database one entry per package, and a payload reporting the wrong version --
# or a longer one sharing its prefix -- is refused before anything is
# published. What it cannot reach: build-check's root branch (useradd / su /
# chown and the depends install), which only exists inside the publish
# container.
#
# Four cases; any of them failing prints "!! FAIL" and exits 1.
set -euo pipefail

REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PKG="magpie-bin"                 # pkgname the fixture pretends to be
BIN="usr/bin/magpie"             # path inside the package the build check asserts on
VER="0.1.2"                      # version the fixture builds
OLD="0.1.1"                      # version seeded as the retention window

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
mkdir -p scripts packages/fixture repo/x86_64
# The whole scripts/ tree: build-check and build-repo source lib/pkg.sh, and a
# probe that copies two files dies on the first `source`.
cp -R "$REPO/scripts/." scripts/
echo "workdir: $work"

# $1 = pkgver in the PKGBUILD, $2 = what the payload prints
make_pkgbuild() {
    cat > packages/fixture/PKGBUILD <<EOF
pkgname=$PKG
pkgver=$1
pkgrel=1
pkgdesc="probe"
arch=('x86_64')
license=('MIT')
options=('!strip' '!debug')
source=()

package() {
    install -Dm755 /dev/null "\$pkgdir/$BIN"
    printf '#!/bin/sh\necho "$PKG $2"\n' > "\$pkgdir/$BIN"
    chmod 755 "\$pkgdir/$BIN"
}
EOF
}

# An older build already in the published repository, as KEEP would leave it.
seed_old() {
    local d
    d="$(mktemp -d)"
    printf 'pkgname = %s\npkgbase = %s\npkgver = %s\npkgrel = 1\npkgdesc = d\nurl = u\nbuilddate = 1\nsize = 1\narch = x86_64\nlicense = MIT\n' \
        "$PKG" "$PKG" "$OLD" > "$d/.PKGINFO"
    touch "$d/usr"
    ( cd "$d" && bsdtar -czf "$work/repo/x86_64/$PKG-$OLD-1-x86_64.pkg.tar.zst" .PKGINFO usr )
}

run() { ./scripts/build-repo.sh repo/x86_64 packages/fixture; }

show() {
    bsdtar -xOf repo/x86_64/magpie.db.tar.gz 2>/dev/null | grep 'pkg.tar.zst' | sed 's/^/  db:   /'
    find repo/x86_64 -name '*.pkg.tar.zst' | sort | sed 's|.*/|  disk: |'
}

echo "########## 1. payload reports $VER (expect: pass, db=1, disk=2) ##########"
make_pkgbuild "$VER" "$VER"
seed_old
run
show

echo
echo "########## 2. payload reports $OLD for pkgver $VER (expect: refuse) ##########"
make_pkgbuild "$VER" "$OLD"
if run; then echo "!! FAIL: wrong version accepted"; exit 1; else echo "  ok: refused"; fi

echo
echo "########## 3. prefix collision: pkgver 0.1.41, payload says 0.1.412 (expect: refuse) ##########"
make_pkgbuild 0.1.41 0.1.412
if run; then echo "!! FAIL: prefix collision accepted"; exit 1; else echo "  ok: refused"; fi

echo
echo "########## 4. KEEP=1 drops the older build from db and disk ##########"
make_pkgbuild "$VER" "$VER"
KEEP=1 run
show
