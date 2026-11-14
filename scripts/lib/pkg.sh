#!/usr/bin/env bash
# The shell side of what a rendered package is: its identity, the names of the
# package files built from it, and the one-line answers the producers hand back.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/pkg.sh"
#   pkg_field packages/magpie-bin/PKGBUILD pkgver
#
# Both build scripts run inside archlinux:base-devel, which ships no python3, so
# this is the one place the shell reads a PKGBUILD field. bump.py reads the same
# file from the other side; it renders it, so it owns the writer's half. Any
# third sed for `pkgver=` belongs here instead.
#
# The same goes for the frozen `name=value` lines: bump.py and build-check.sh
# write them, and every reader goes through line_field rather than its own sed.
#
# bump.py also writes the 包清单 as one `package <dir> <pkgname>` line per
# package. That shape has one reader here (pkglist_field) for the same reason:
# three steps used to split the same line and one of them read the pkgname back
# out of the PKGBUILD.

# line_field <name> <file> -- the one `name=value` line a frozen producer wrote.
#
# A producer writes its line once, so two matches is a protocol break to report,
# not a value to pick from. A missing line is a failure here too: what "absent"
# means differs per line (an absent `bump=` is "nothing to do", an absent `ver=`
# is a broken render), so the caller decides with `|| true` and a default rather
# than this function guessing.
line_field() {
    local name="$1" file="$2" count value
    [ -r "$file" ] || { echo "no such file: $file" >&2; return 1; }
    count="$(grep -c "^${name}=" "$file" || true)"
    count="${count:-0}"
    if [ "$count" -eq 0 ]; then
        echo "no ${name}= in ${file}" >&2
        return 1
    fi
    if [ "$count" -gt 1 ]; then
        echo "${count} ${name}= lines in ${file}" >&2
        return 1
    fi
    value="$(sed -n "s/^${name}=//p" "$file")"
    [ -n "$value" ] || { echo "empty ${name}= in ${file}" >&2; return 1; }
    printf '%s\n' "$value"
}

# pkglist_field <file> <index> -- one column of the 包清单, in its order.
#
# The line is `package <dir> <pkgname>`, which bump.py writes once per package.
# A line that is not that shape is a protocol break and fails the caller; an
# empty file is "no packages", which is why it is not an error here.
pkglist_field() {
    local file="$1" index="$2" line
    local -a parts
    [ -r "$file" ] || { echo "no such file: $file" >&2; return 1; }
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        read -r -a parts <<<"$line"
        if [ "${#parts[@]}" -ne 3 ] || [ "${parts[0]}" != "package" ]; then
            echo "not a 包清单 line: $line" >&2
            return 1
        fi
        printf '%s\n' "${parts[$index]}"
    done <"$file"
}

# pkglist_dirs <file> -- the package directories to build, in 包清单 order.
pkglist_dirs() { pkglist_field "$1" 1; }

# pkglist_names <file> -- each package's pkgname, in the same order, so the
# release step names files without reading the PKGBUILD again.
pkglist_names() { pkglist_field "$1" 2; }

# pkg_field <pkgbuild> <field> -- one field of a rendered PKGBUILD, unquoted.
pkg_field() {
    local file="$1" name="$2" value
    [ -r "$file" ] || { echo "no such PKGBUILD: $file" >&2; return 1; }
    value="$(sed -n "s/^${name}=//p" "$file" | head -1 | tr -d "'\"")"
    [ -n "$value" ] || { echo "no ${name}= in ${file}" >&2; return 1; }
    printf '%s\n' "$value"
}

# pkgfiles <repo dir> <pkgname> -- that package's files in that directory,
# oldest version first. The naming (pkgname-pkgver-pkgrel-arch) and the
# ordering are one rule, so it lives here: the retention window takes the head
# of this list and the database entry takes its tail, and neither can disagree
# with the other about which build is current.
pkgfiles() {
    local out="$1" pkgname="$2"
    local -a files
    # shellcheck disable=SC2012  # names here are pkgname-pkgver-pkgrel-arch
    mapfile -t files < <(ls -1 "$out/$pkgname"-[0-9]*.pkg.tar.* 2>/dev/null | sort -V)
    [ "${#files[@]}" -gt 0 ] || return 0
    printf '%s\n' "${files[@]}"
}

# pkgfiles_prune <repo dir> <pkgname> <keep> -- the files the retention window
# drops, oldest first: what is left when the newest <keep> are kept. Empty while
# the window still has room. The decision lives here, next to the ordering it
# depends on, instead of in the script that happens to call it.
pkgfiles_prune() {
    local out="$1" pkgname="$2" keep="$3"
    local -a files
    mapfile -t files < <(pkgfiles "$out" "$pkgname")
    [ "${#files[@]}" -gt "$keep" ] || return 0
    printf '%s\n' "${files[@]:0:${#files[@]}-keep}"
}

# pkgfiles_current <repo dir> <pkgname> -- the one file that belongs in the
# database: the tail of the same order the window reads its head from.
pkgfiles_current() {
    pkgfiles "$1" "$2" | tail -1
}

# pkgfiles_at <repo dir> <pkgname> <pkgver> -- that version's files only, in the
# same order. The release step publishes exactly these and asks here, instead of
# rebuilding the name with a pkgrel and an arch of its own (both are makepkg's
# to decide, not the workflow's).
pkgfiles_at() {
    local out="$1" pkgname="$2" pkgver="$3" file name
    while IFS= read -r file; do
        name="${file##*/}"
        case "$name" in
            "$pkgname-$pkgver-"*) printf '%s\n' "$file" ;;
        esac
    done < <(pkgfiles "$out" "$pkgname")
}
