#!/usr/bin/env bash
# Drive the build scripts against a synthesized package: real makepkg, real
# repo-add, no network, no container, no root.
#
#   ./scripts/probe-repo-build.sh            # this repository
#   ./scripts/probe-repo-build.sh <repo root>
#
# What it proves: makepkg names and builds the file, build-check hands over
# pkgname= / artifact=, build-repo prunes to KEEP *版本* and hands the database
# one entry per package, and a payload reporting the wrong version -- or a
# longer one sharing its prefix -- is refused before anything is published. What
# it cannot reach: the tools `BUILD_AS=root` calls (useradd, chown, su, pacman),
# which only exist inside the publish container -- the adapter around them and
# its order run offline in tests/test_build_check.py.
#
# Five cases; any of them failing prints "!! FAIL" and exits 1. Every case is
# asserted -- the two that used to only print their state are the retention
# window and the database entry, which is where a broken prune() hides.
set -euo pipefail

REPO="$(cd "${1:-$(dirname "${BASH_SOURCE[0]}")/..}" && pwd)"
PKG="magpie-bin"                 # pkgname the fixture pretends to be
BIN="usr/bin/magpie"             # path inside the package the build check asserts on
VER="0.1.2"                      # version the fixture builds
OLD="0.1.1"                      # version seeded as the retention window
# A name of its own, not the repository's: the database's file name is what
# proves REPO_NAME crossed into the script and reached repo-add, and with the
# real name the probe could not tell a passed variable from a defaulted one.
REPO_NAME="probe"
export REPO_NAME

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

# What case 1 and case 4 claim, asked of the tree rather than read by eye --
# printing the state and letting a human compare was how a broken retention
# window passed this probe with exit 0.
db_entries() { bsdtar -xOf "repo/x86_64/$REPO_NAME.db.tar.gz" | grep -c 'pkg.tar.zst' || true; }
disk_files() { find repo/x86_64 -name '*.pkg.tar.zst' | wc -l; }

# expect <expected> <actual> <label>
expect() {
    if [ "$1" = "$2" ]; then
        echo "  ok: $3 = $2"
    else
        echo "!! FAIL: $3 = $2, expected $1"
        exit 1
    fi
}

expect_gone() {
    if [ -e "$1" ]; then
        echo "!! FAIL: $1 is still on disk"
        exit 1
    fi
    echo "  ok: $(basename "$1") is gone"
}

show() {
    bsdtar -xOf "repo/x86_64/$REPO_NAME.db.tar.gz" 2>/dev/null | grep 'pkg.tar.zst' | sed 's/^/  db:   /'
    find repo/x86_64 -name '*.pkg.tar.zst' | sort | sed 's|.*/|  disk: |'
}

echo "########## 1. payload reports $VER (expect: pass, db=1, disk=2) ##########"
make_pkgbuild "$VER" "$VER"
seed_old
run
expect 1 "$(db_entries)" "database entries"
expect 2 "$(disk_files)" "files on disk"
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
echo "########## 4. release.sh hands over this 版本 alone (expect: 1 asset, 1 sum, the install line) ##########"
# The tree still carries the 保留窗口's older build from case 1. A release that
# took it would advertise two versions at once, so the one thing this case is
# about is that it does not.
mkdir -p rel
printf 'package packages/fixture %s\n' "$PKG" > packages.txt
ARCH=x86_64 PAGES_URL=https://example.invalid/repo \
    ./scripts/release.sh repo/x86_64 "$VER" packages.txt rel > release.txt
cat release.txt
expect 1 "$(wc -l < rel/assets.txt)" "assets"
if grep -q -- "$PKG-$OLD" rel/assets.txt; then
    echo "!! FAIL: the 保留窗口's older build is in the release"
    exit 1
fi
echo "  ok: the older build is not in the release"
expect "$(sha256sum "repo/x86_64/$PKG-$VER-1-x86_64.pkg.tar.zst" | cut -d' ' -f1)" \
    "$(cut -d' ' -f1 < rel/SHA256SUMS)" "the checksum of the served bytes"
if grep -q "pacman -S $PKG" rel/notes.md; then
    echo "  ok: the notes carry the install line from the 包清单"
else
    echo "!! FAIL: the notes lost the install line"
    exit 1
fi

echo
echo "########## 5. KEEP=1 drops the older build from db and disk ##########"
make_pkgbuild "$VER" "$VER"
KEEP=1 run
expect 1 "$(db_entries)" "database entries"
expect 1 "$(disk_files)" "files on disk"
expect_gone "repo/x86_64/$PKG-$OLD-1-x86_64.pkg.tar.zst"
show
