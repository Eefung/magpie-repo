#!/usr/bin/env bash
# Build the packages and assemble a pacman repository out of them.
#
#   ./scripts/build-repo.sh <repo x86_64 dir> <package dir>...
#   ./scripts/build-repo.sh repo/x86_64 packages/magpie-bin packages/magpie-cli-bin
#
# The result is what GitHub Pages serves, so pacman needs the database to be
# named after the repository section: REPO_NAME=magpie → magpie.db.tar.gz must
# sit next to the packages and be reachable at <Server>/magpie.db.
#
# The database is always rebuilt from the package files present, which keeps it
# free of entries for packages that were pruned.
set -euo pipefail

out="${1:?usage: build-repo.sh <repo x86_64 dir> <package dir>...}"
shift
[ "$#" -gt 0 ] || { echo "name at least one package directory" >&2; exit 2; }

REPO_NAME="${REPO_NAME:-magpie}"
KEEP="${KEEP:-2}"          # versions of each package kept in the repository
out="$(mkdir -p "$out" && cd "$out" && pwd)"
root="$(dirname "$out")"

pkg_name() { sed -n 's/^pkgname=//p' "$1/PKGBUILD" | head -1 | tr -d "'\""; }

# --- build -------------------------------------------------------------------
for dir in "$@"; do
    CLEAN=0 ./scripts/build-check.sh "$dir"
done

for dir in "$@"; do
    install -m644 "$dir"/*.pkg.tar.* "$out"/
done

# --- keep only the newest KEEP builds of every package ------------------------
prune() {
    local pkgname="$1" file
    local -a files
    # shellcheck disable=SC2012  # package filenames are pkgname-pkgver-pkgrel-arch
    mapfile -t files < <(ls -1 "$out/$pkgname"-[0-9]*.pkg.tar.* 2>/dev/null | sort -V)
    [ "${#files[@]}" -gt "$KEEP" ] || return 0
    for file in "${files[@]:0:${#files[@]}-KEEP}"; do
        echo "pruning $(basename "$file")"
        rm -f "$file"
    done
}

for dir in "$@"; do
    prune "$(pkg_name "$dir")"
done

# --- rebuild the database from what is on disk -------------------------------
rm -f "$out/$REPO_NAME.db" "$out/$REPO_NAME.db.tar.gz" \
      "$out/$REPO_NAME.files" "$out/$REPO_NAME.files.tar.gz"
repo-add -q "$out/$REPO_NAME.db.tar.gz" "$out"/*.pkg.tar.*
# shellcheck disable=SC2012
echo "repository $REPO_NAME: $(ls -1 "$out"/*.pkg.tar.* | wc -l) package(s), $(du -sh "$out" | cut -f1)"

(cd "$out" && sha256sum -- *.pkg.tar.* > "$root/SHA256SUMS" && echo "checksums: $root/SHA256SUMS")

# --- leave the tree as it was found -----------------------------------------
for dir in "$@"; do
    rm -rf "$dir"/src "$dir"/pkg "$dir"/.srcdest "$dir"/*.pkg.tar.*
    # build-check ran makepkg as `builder` when we are root; hand it back.
    if [ "$(id -u)" -eq 0 ] && [ -d /src ]; then
        chown -R "$(stat -c %u /src)" "$dir"
    fi
done
