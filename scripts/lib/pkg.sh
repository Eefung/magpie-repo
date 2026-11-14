#!/usr/bin/env bash
# The shell side of what a rendered package is: its identity, and the names of
# the package files built from it.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/pkg.sh"
#   pkg_field packages/magpie-bin/PKGBUILD pkgver
#
# Both build scripts run inside archlinux:base-devel, which ships no python3, so
# this is the one place the shell reads a PKGBUILD field. bump.py reads the same
# file from the other side; it renders it, so it owns the writer's half. Any
# third sed for `pkgver=` belongs here instead.

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
