#!/usr/bin/env bash
# Publish one or more package directories to the AUR.
#
#   ./scripts/publish.sh packages/magpie-bin packages/magpie-cli-bin
#
# Each directory name is the AUR pkgbase. AUR_BASE overrides the remote, which
# is what the local test harness points at a bare repository.
set -euo pipefail

AUR_BASE="${AUR_BASE:-ssh://aur@aur.archlinux.org}"
AUR_GIT_NAME="${AUR_GIT_NAME:-Eefung}"
AUR_GIT_EMAIL="${AUR_GIT_EMAIL:-eefung@tuta.io}"
WORKDIR="${WORKDIR:-$(mktemp -d)}"

[ "$#" -gt 0 ] || { echo "usage: $0 <package dir>..." >&2; exit 2; }

for dir in "$@"; do
    pkg="$(basename "$dir")"
    src="$(cd "$dir" && pwd)"
    for f in PKGBUILD .SRCINFO; do
        [ -s "$src/$f" ] || { echo "$pkg: missing $f" >&2; exit 1; }
    done
    ver="$(sed -n 's/^pkgver=//p' "$src/PKGBUILD" | tr -d "'\"")"
    rel="$(sed -n 's/^pkgrel=//p' "$src/PKGBUILD" | tr -d "'\"")"
    target="$WORKDIR/$pkg"

    rm -rf "$target"
    if git clone --quiet "$AUR_BASE/$pkg.git" "$target" 2>/dev/null; then
        echo "$pkg: updating the existing AUR repository"
        rm -f "$target/PKGBUILD" "$target/.SRCINFO"
    else
        echo "$pkg: creating a new AUR repository"
        mkdir -p "$target"
        git -C "$target" init --quiet --initial-branch=master
        git -C "$target" remote add origin "$AUR_BASE/$pkg.git"
    fi

    install -m644 "$src/PKGBUILD" "$src/.SRCINFO" "$target/"
    git -C "$target" add PKGBUILD .SRCINFO
    if git -C "$target" diff --cached --quiet; then
        echo "$pkg: already at $ver-$rel, nothing to push"
        continue
    fi
    git -C "$target" \
        -c user.name="$AUR_GIT_NAME" -c user.email="$AUR_GIT_EMAIL" \
        commit --quiet -m "upgpkg: $pkg $ver-$rel"
    git -C "$target" push --quiet origin HEAD:master
    echo "$pkg: pushed $ver-$rel to $AUR_BASE/$pkg.git"
done
