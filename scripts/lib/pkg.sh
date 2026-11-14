#!/usr/bin/env bash
# The shell side of what a rendered package is: its identity, the names of the
# package files built from it, what the built file's payload reports, and the
# one-line answers the producers hand back.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/pkg.sh"
#   pkg_field packages/magpie-bin/PKGBUILD pkgver
#
# Both build scripts run inside archlinux:base-devel, which ships no python3, so
# this is the one place the shell reads a PKGBUILD field -- and the one place it
# reads what makepkg printed about one (srcinfo_depends). bump.py reads the same
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
#
# The value is the first token of the first matching line, with a matching pair
# of quotes taken off. That is the shape the renderer writes, and it is the
# rule bump.py's own reader applies (`^pkgver=(\S+)` then strip), so the two
# sides answer alike on a quoted value or a trailing space. A test feeds one
# table of shapes to both; before that, their agreement was a comment.
pkg_field() {
    local file="$1" name="$2" value
    [ -r "$file" ] || { echo "no such PKGBUILD: $file" >&2; return 1; }
    value="$(sed -n "s/^${name}=//p" "$file" | head -1)"
    read -r value _ <<<"$value" || true
    value="${value%\'}"; value="${value#\'}"
    value="${value%\"}"; value="${value#\"}"
    [ -n "$value" ] || { echo "no ${name}= in ${file}" >&2; return 1; }
    printf '%s\n' "$value"
}

# srcinfo_depends <text> -- the depends a srcinfo block lists, one per line.
#
# makepkg --printsrcinfo is the package's own answer to "what does this build
# need at runtime". build-check.sh used to write that answer to .SRCINFO only to
# sed it back into memory, inside the root branch no offline test reaches; the
# text is an argument now, so the read is testable and the file never exists.
srcinfo_depends() {
    printf '%s\n' "$1" | sed -n 's/^[[:space:]]*depends = //p'
}

# pkgfiles <repo dir> <pkgname> -- that package's files in that directory,
# oldest version first. The naming (pkgname-pkgver-pkgrel-arch) and the
# ordering are one rule, so it lives here: the 保留窗口 counts 版本 from the head
# of this list and the database entry takes the newest 版本's files from its
# tail, and neither can disagree with the other about which build is current.
pkgfiles() {
    local out="$1" pkgname="$2"
    local -a files
    # shellcheck disable=SC2012  # names here are pkgname-pkgver-pkgrel-arch
    mapfile -t files < <(ls -1 "$out/$pkgname"-[0-9]*.pkg.tar.* 2>/dev/null | sort -V)
    [ "${#files[@]}" -gt 0 ] || return 0
    printf '%s\n' "${files[@]}"
}

# pkgfile_version <pkgname> <包文件> -- the 版本 in that file's name.
#
# A 版本 is digits and dots only (渲染前强制的那条不变量), so it never contains
# a dash: the run between `<pkgname>-` and the next dash is the version, and
# pkgrel and arch -- both makepkg's to decide -- are not read here.
pkgfile_version() {
    local pkgname="$1" name="${2##*/}"
    printf '%s\n' "${name#"$pkgname"-}" | cut -d- -f1
}

# pkgfiles_prune <repo dir> <pkgname> <keep> -- the files the 保留窗口 drops,
# oldest first: every file of a 版本 that falls out of the newest <keep> 版本.
#
# GLOSSARY defines the window as 每个包同时保留的版本数, so it counts 版本 and not
# files: the same 版本 built for two arches is one version, and both files stay
# or go together. Counting files kept a single version in a two-arch repository
# and dropped one arch of the older one. The decision lives here, next to the
# ordering it depends on.
pkgfiles_prune() {
    local out="$1" pkgname="$2" keep="$3" file version
    local -a versions=()
    local -A dropped=()
    while IFS= read -r file; do
        version="$(pkgfile_version "$pkgname" "$file")"
        [ "${#versions[@]}" -gt 0 ] && [ "${versions[${#versions[@]}-1]}" = "$version" ] \
            || versions+=("$version")
    done < <(pkgfiles "$out" "$pkgname")
    [ "${#versions[@]}" -gt "$keep" ] || return 0
    for version in "${versions[@]:0:${#versions[@]}-keep}"; do
        dropped["$version"]=1
    done
    while IFS= read -r file; do
        version="$(pkgfile_version "$pkgname" "$file")"
        if [ -n "${dropped["$version"]:-}" ]; then
            printf '%s\n' "$file"
        fi
    done < <(pkgfiles "$out" "$pkgname")
}

# pkgfiles_current <repo dir> <pkgname> -- the files that belong in the database:
# every arch of the newest 版本, read off the tail of the same order the window
# reads its head from. Today's single arch makes that one file; a second arch
# gets an entry of its own instead of being left out of the db.
pkgfiles_current() {
    local out="$1" pkgname="$2" file version newest
    local -a files=()
    mapfile -t files < <(pkgfiles "$out" "$pkgname")
    [ "${#files[@]}" -gt 0 ] || return 0
    newest="$(pkgfile_version "$pkgname" "${files[${#files[@]}-1]}")"
    for file in "${files[@]}"; do
        version="$(pkgfile_version "$pkgname" "$file")"
        if [ "$version" = "$newest" ]; then
            printf '%s\n' "$file"
        fi
    done
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

# payload_member <包文件> -- the one program it installs under usr/bin/.
#
# The templates decide where that program goes; this asks the built file rather
# than restating the path as a regex the way build-check.sh used to. Exactly one
# is accepted: two binaries is a package change someone has to look at, not a
# value to pick the first of.
payload_member() {
    local artifact="$1" tar_bin="${PAYLOAD_TAR:-bsdtar}" listing
    local -a members
    [ -r "$artifact" ] || { echo "no such 包文件: $artifact" >&2; return 1; }
    if ! listing="$("$tar_bin" -tf "$artifact")"; then
        echo "cannot read $artifact with $tar_bin" >&2
        return 1
    fi
    mapfile -t members < <(printf '%s\n' "$listing" | grep '^usr/bin/.' | grep -v '/$' || true)
    if [ "${#members[@]}" -eq 0 ]; then
        echo "$(basename "$artifact") installs nothing under usr/bin/" >&2
        return 1
    fi
    if [ "${#members[@]}" -gt 1 ]; then
        echo "$(basename "$artifact") installs ${#members[@]} programs under usr/bin/:" >&2
        printf '  %s\n' "${members[@]}" >&2
        return 1
    fi
    printf '%s\n' "${members[0]}"
}

# payload_reports <包文件> <expected pkgver> -- the version its program reports,
# when that is <expected>.
#
# This is the judgement that can publish a wrong 版本: a binary taken from the
# wrong asset, or out of a stale SRCDEST, still runs and still loads. The first
# dotted number it prints is compared exactly -- a substring test would accept
# 0.1.412 for 0.1.41 -- and the program's own output is quoted back on a
# mismatch.
#
# PAYLOAD_TAR is the extractor, because the seam has two adapters: bsdtar in the
# build container, GNU tar on the machine that runs the offline tests.
payload_reports() {
    local artifact="$1" expected="$2" tar_bin="${PAYLOAD_TAR:-bsdtar}"
    local member tmp output reported
    member="$(payload_member "$artifact")" || return 1
    tmp="$(mktemp)" || return 1
    if ! "$tar_bin" -xOf "$artifact" "$member" >"$tmp"; then
        rm -f "$tmp"
        echo "cannot extract $member from $(basename "$artifact")" >&2
        return 1
    fi
    chmod +x "$tmp"
    if ! output="$(timeout 90 "$tmp" --version 2>&1)"; then
        echo "$(basename "$artifact") failed to run --version:" >&2
        printf '%s\n' "$output" >&2
        rm -f "$tmp"
        return 1
    fi
    rm -f "$tmp"
    reported="$(printf '%s\n' "$output" | grep -oE '[0-9]+(\.[0-9]+)+' | head -1)" || true
    if [ "$reported" != "$expected" ]; then
        echo "$(basename "$artifact") reports '${reported:-nothing}', expected $expected:" >&2
        printf '%s\n' "$output" >&2
        return 1
    fi
    printf '%s\n' "$reported"
}
