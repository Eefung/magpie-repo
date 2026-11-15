#!/usr/bin/env bash
# Turn one version's package files into what a GitHub release is made of.
#
#   ./scripts/release.sh <repo dir> <version> <包清单 file> <out dir>
#
#   ARCH=x86_64 REPO_NAME=magpie PAGES_URL=https://host/repo \
#     ./scripts/release.sh repo/x86_64 0.1.4178 /tmp/packages.txt /tmp/rel
#
# `gh release create` belongs to the workflow step; everything it is handed is
# this script's answer:
#
#   - the assets of *this* 版本 only. The repo directory also keeps the previous
#     build for the 保留窗口, and those files do not belong in a release.
#   - a SHA256SUMS built from exactly those files, so the notes and the sums
#     cannot come from two different sets.
#   - the notes, whose install lines are generated from the 包清单 rather than
#     spelled out, so a third package appears without an edit here.
#
# The last three lines are frozen, read through line_field like every other
# handover:
#
#   assets=<file listing the asset paths, one per line>
#   sums=<file with the sha256 of exactly those>
#   notes=<file with the release body>
#
# ARCH, REPO_NAME and PAGES_URL are required: they are what this 软件源 is
# served as, and the notes quote all three in the block a user pastes into
# pacman.conf. REPO_NAME used to be a literal `[magpie]` in the prose below --
# a copy of a fact this script was never handed, and the only place a user reads
# it besides README.
set -euo pipefail

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"

dir="${1:?usage: release.sh <repo dir> <version> <包清单 file> <out dir>}"
version="${2:?usage: release.sh <repo dir> <version> <包清单 file> <out dir>}"
packages="${3:?usage: release.sh <repo dir> <version> <包清单 file> <out dir>}"
out="${4:?usage: release.sh <repo dir> <version> <包清单 file> <out dir>}"

ARCH="${ARCH:?release.sh needs ARCH: the name of the served directory}"
REPO_NAME="${REPO_NAME:?release.sh needs REPO_NAME: the 软件源 section name}"
PAGES_URL="${PAGES_URL:?release.sh needs PAGES_URL: where the 软件源 is served}"
[ -d "$dir" ] || { echo "no such repository directory: $dir" >&2; exit 1; }

mkdir -p "$out"

# The names come from the 包清单, one per line, in its order -- not from the
# PKGBUILDs, which this step has no reason to open. Captured first, not through
# a process substitution: `mapfile < <(...)` exits 0 when the reader fails, so a
# malformed 包清单 would read as "no packages" instead of as its own error.
names="$(pkglist_names "$packages")" || exit 1
[ -n "$names" ] || {
    echo "the 包清单 names no packages: $packages" >&2
    exit 1
}
mapfile -t pkgnames <<<"$names"

# One package at a time, in 包清单 order, so the caller gets a stable list.
assets=()
for pkgname in "${pkgnames[@]}"; do
    mapfile -t -O "${#assets[@]}" assets < <(pkgfiles_at "$dir" "$pkgname" "$version")
done
[ "${#assets[@]}" -gt 0 ] || {
    echo "no package files for v$version in $dir" >&2
    exit 1
}

printf '%s\n' "${assets[@]}" >"$out/assets.txt"
# Built in the directory the files live in, from their names: the digest is of
# the bytes that will be served, and pkgrel/arch stay makepkg's to decide.
(cd "$dir" && sha256sum -- "${assets[@]##*/}") >"$out/SHA256SUMS"

# The install block is the whole reason this is generated: it is the part of
# the notes a user copies verbatim, and it restates the same four facts README
# does. Emitted from ARCH / REPO_NAME / PAGES_URL rather than written out, so a
# differently named 软件源 cannot ship notes that tell the user to write
# someone else's section -- and tests/test_inventory.py holds README's block and
# this one to the same literals.
#
# It is fenced, and nothing outside the fence repeats its three lines: the guard
# reads the fence (tests/install_block.py), so a `[magpie]` left in the prose
# beside it would stand in for the line a user copies. README learned the same
# lesson -- its install block is asserted from the block, not from the file.
cat >"$out/notes.md" <<EOF
magpie $version 的 $ARCH pacman 软件包。

/etc/pacman.conf 末尾加：

~~~ini
[$REPO_NAME]
SigLevel = Optional
Server = $PAGES_URL/$ARCH
~~~

然后：

    # 先同步所有源，别单独 -Sy
    sudo pacman -Syu
    # 桌面版与终端版，可分开装
    sudo pacman -S ${pkgnames[*]}

校验和见下方的 SHA256SUMS；上游版本说明见
https://github.com/yetone/magpie-releases/releases/tag/v$version
EOF

echo "assets=$out/assets.txt"
echo "sums=$out/SHA256SUMS"
echo "notes=$out/notes.md"
