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

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

out="${1:?usage: build-repo.sh <repo x86_64 dir> <package dir>...}"
shift
[ "$#" -gt 0 ] || { echo "name at least one package directory" >&2; exit 2; }

REPO_NAME="${REPO_NAME:-magpie}"
KEEP="${KEEP:-2}"          # versions of each package kept in the repository
out="$(mkdir -p "$out" && cd "$out" && pwd)"

# --- build -------------------------------------------------------------------
# build-check owns its package directory and answers with frozen lines: which
# package it just built and where the package file is. Taking those is the whole
# handover -- no globbing for whatever happens to be lying there, no guessing by
# timestamp, and no second read of the PKGBUILD for a name the producer already
# had.
# BUILD_CHECK is the one seam: tests point it at a stub so this script's window
# and database handling can run offline, the way build-check cannot.
BUILD_CHECK="${BUILD_CHECK:-$here/build-check.sh}"
built=""
built_name=""
build_one() {
    local dir="$1" log
    log="$(mktemp)"
    if ! "$BUILD_CHECK" "$dir" >"$log" 2>&1; then
        cat "$log" >&2
        rm -f "$log"
        echo "building $dir failed" >&2
        return 1
    fi
    cat "$log"
    # Not `local`: the caller moves the file this names, and files the package
    # under the name this names.
    if ! built="$(line_field artifact "$log")" \
        || ! built_name="$(line_field pkgname "$log")"; then
        rm -f "$log"
        echo "building $dir failed" >&2
        return 1
    fi
    rm -f "$log"
}

# One walk over the package directories: build each, then name the package once.
# The window and the database below read this list instead of parsing the same
# PKGBUILD a second and third time.
declare -a pkgnames=()
for dir in "$@"; do
    build_one "$dir"
    mv -f "$built" "$out/"
    pkgnames+=("$built_name")
done

# --- keep only the newest KEEP builds of every package ------------------------
prune() {
    local pkgname="$1" file
    while IFS= read -r file; do
        echo "pruning $(basename "$file")"
        rm -f "$file"
    done < <(pkgfiles_prune "$out" "$pkgname" "$KEEP")
}

for pkgname in "${pkgnames[@]}"; do
    prune "$pkgname"
done

# --- rebuild the database from what is on disk -------------------------------
# Only the newest build of each package belongs in the database. The older
# files stay on disk for clients whose db still names them (the retention
# window), but an entry in the db would also let a pinned install reach back
# to them, which is not what the window is for.
mapfile -t current < <(for pkgname in "${pkgnames[@]}"; do pkgfiles_current "$out" "$pkgname"; done)
[ "${#current[@]}" -gt 0 ] || { echo "no package files to put in the database" >&2; exit 1; }

rm -f "$out/$REPO_NAME.db" "$out/$REPO_NAME.db.tar.gz" \
      "$out/$REPO_NAME.files" "$out/$REPO_NAME.files.tar.gz"
repo-add -q "$out/$REPO_NAME.db.tar.gz" "${current[@]}"
# shellcheck disable=SC2012
echo "repository $REPO_NAME: ${#current[@]} database entries, \
$(ls -1 "$out"/*.pkg.tar.* | wc -l) file(s) on disk, $(du -sh "$out" | cut -f1)"
