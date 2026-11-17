#!/usr/bin/env bash
# The shell side of what a rendered package is: its identity, the names of the
# package files built from it, what the built file's payload reports, what a
# build leaves behind in its directory, and the one-line answers the producers
# hand back.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/pkg.sh"
#   srcinfo_field pkgver "$srcinfo"
#
# Both build scripts run inside archlinux:base-devel, which ships no python3, so
# the shell does not parse a PKGBUILD at all: it asks makepkg. `pkg_field` used
# to read `pkgname=`/`pkgver=` out of the file, and its only callers were two
# lines in build-check.sh that already held `makepkg --printsrcinfo` -- the same
# answer, asked of the program that owns the parsing. bump.py keeps its own
# `pkg_field`, because it reads a tree no makepkg has touched. Any sed for a
# PKGBUILD field belongs there, not here.
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
# means differs per line (an absent `gates=` means a tree older than the
# contract, an absent `bump=` is a check that never concluded), so the caller
# decides with `line_field_or` and a named default rather than this function
# guessing.
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

# line_field_or <name> <file> <default> -- the line's value, or <default> when
# the producer wrote no such line at all.
#
# "Absent" and "broken" are different failures, and only the caller knows the
# first one's meaning: an absent `bump=` is a check with nothing to say, while
# two `bump=` lines is a producer that wrote twice. `line_field ... || true`
# collapses the two into the default, so a duplicated line read as "nothing to
# do" -- a check that broke now looks like a quiet day.
line_field_or() {
    local name="$1" file="$2" default="$3" count
    [ -r "$file" ] || { echo "no such file: $file" >&2; return 1; }
    count="$(grep -c "^${name}=" "$file" || true)"
    if [ "${count:-0}" -eq 0 ]; then
        printf '%s\n' "$default"
        return 0
    fi
    line_field "$name" "$file"
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

# srcinfo_lines <name> <text> -- every value that field of a srcinfo block has,
# one per line, in the order makepkg printed them.
#
# makepkg --printsrcinfo is the package's own answer to "what does this build
# declare", and build-check.sh captures it once. The dependencies need every
# line of their field (a build can declare several), which is why this is the
# line reader and the two below are answers over it.
srcinfo_lines() {
    printf '%s\n' "$2" | sed -n "s/^[[:space:]]*${1} = //p"
}

# srcinfo_field <name> <text> -- the one value a single-valued field has.
#
# pkgname, pkgver and pkgrel are one line each; zero lines is a block that does
# not answer (a build script that kept going would ship a name nobody decided),
# and a second line is a block two answers, which the caller cannot choose
# between. Refused by name, the way line_field refuses a duplicated line.
srcinfo_field() {
    local name="$1" values count
    values="$(srcinfo_lines "$name" "$2")"
    count="$(printf '%s\n' "$values" | grep -c . || true)"
    [ "$count" = 1 ] || {
        echo "the srcinfo block carries $count ${name} = lines, not one" >&2
        return 1
    }
    printf '%s\n' "$values"
}

# srcinfo_depends <text> -- the depends a srcinfo block lists, one per line.
#
# The runtime dependencies, and only those: makedepends/checkdepends/optdepends
# are not what a user has to have. The read used to write .SRCINFO to disk only
# to sed it back into memory, inside the root branch no offline test reaches;
# the text is an argument now, so the read is testable and the file never exists.
srcinfo_depends() { srcinfo_lines depends "$1"; }

# pkgfile_suffix / pkgfile_pattern -- the one spelling of "this is a 包文件".
#
# The extension and the ordering are one rule, and four places used to spell
# the glob themselves: pkgfiles here, build-check's sweep, build-repo's disk
# count, and the workflow's seed whitelist. `*.pkg.tar.*` also matches a `.sig`
# next to a package file, which repo-add would be handed the day signing lands
# -- and two spellings are how the seed and the retention window drift apart
# without either side noticing.
pkgfile_suffix() { printf '.pkg.tar.zst\n'; }
pkgfile_pattern() { printf '*%s\n' "$(pkgfile_suffix)"; }

# pkgfile_residue_pattern -- the archive shapes a build can leave behind.
#
# Not "everything a build leaves": makepkg's working trees and a stale
# `.SRCINFO` are the other half of the residue, and they are named where they
# are removed -- build-check.sh's LEFTOVERS, next to the trap that clears them.
# This one is a glob, and it answers the narrower question a glob can: which
# files in this directory look like a build's output.
#
# A different question from pkgfile_pattern, and deliberately wider: that one is
# the set this repository serves, this one is the set a build has to be able to
# clear before and after itself. makepkg writes `<pkg>.pkg.tar.zst.sig` beside
# the archive the day signing lands, and a hand-run makepkg with an older
# default writes `.xz`; both are residue, neither is a 包文件. The name is what
# makes the trap's reach a decision instead of an accident -- the glob
# build-check used to spell itself (`*.pkg.tar.*`) was this rule, unnamed -- and
# `packages/*/<pattern>` in .gitignore is the same set, held to it by
# tests/test_pkg_sh.py.
pkgfile_residue_pattern() { printf '*.pkg.tar.*\n'; }

# pkgfile_leftovers -- the names a build leaves in a package directory that are
# not 包文件, one per line.
#
# 残留 has two halves, and they are answered by two different things. A glob can
# answer "which files look like a build's output" (pkgfile_residue_pattern);
# only names can answer "which entries are a build's own byproducts" --
# makepkg's working trees, and a `.SRCINFO` an older checkout still has. Three
# places have to agree about that second set: build-check's trap, the stray
# check that runs before it hands the directory back, and `.gitignore`, for the
# run a kill caught before either could fire. They were three copies of four
# names until this function became the fourth reader of them.
pkgfile_leftovers() {
    printf '%s\n' src pkg .srcdest .SRCINFO
}

# pkgfiles_all <repo dir> -- every 包文件 in that directory, oldest 版本 first.
#
# The one glob. `pkgfiles` filters it per package and the window, the database
# entry and the release selection are thin answers over that one order; this is
# also what the workflow's seed asks, so the tree carried forward and the tree
# pruned are decided by the same rule.
pkgfiles_all() {
    local out="$1" pattern
    local -a files
    pattern="$(pkgfile_pattern)"
    # shellcheck disable=SC2086,SC2012  # the pattern is the rule being applied
    mapfile -t files < <(ls -1 "$out"/$pattern 2>/dev/null | sort -V)
    [ "${#files[@]}" -gt 0 ] || return 0
    printf '%s\n' "${files[@]}"
}

# pkgfiles <repo dir> <pkgname> -- that package's files in that directory,
# oldest version first. The naming (pkgname-pkgver-pkgrel-arch) and the
# ordering are one rule, so it lives here: the 保留窗口 counts 版本 from the head
# of this list and the database entry takes the newest 版本's files from its
# tail, and neither can disagree with the other about which build is current.
pkgfiles() {
    local out="$1" pkgname="$2" file name
    while IFS= read -r file; do
        name="${file##*/}"
        # The `-[0-9]` is the guard that keeps magpie-cli-bin's files out of
        # magpie-bin's window: a prefix alone would mix them.
        case "$name" in
            "$pkgname"-[0-9]*) printf '%s\n' "$file" ;;
        esac
    done < <(pkgfiles_all "$out")
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

# pkgfile_evr <pkgname> <包文件> -- `<pkgver>-<pkgrel>`, the build that file is.
#
# The name is `<pkgname>-<pkgver>-<pkgrel>-<arch><suffix>`, so peeling the
# suffix, the arch and the pkgname off leaves the two fields makepkg decided
# from the PKGBUILD. The database entry and the release both describe a build;
# `pkgfile_version` answers the other question (which 版本, i.e. which upstream
# release), and the window counts those.
pkgfile_evr() {
    local pkgname="$1" name="${2##*/}" base
    base="${name%"$(pkgfile_suffix)"}"
    base="${base%-*}"
    printf '%s\n' "${base#"$pkgname"-}"
}

# version_shape_ok <版本> -- the one accept-set for a 版本, in shell.
#
# Digits and dots, at least two parts, none of them empty: `0.1.3995`, not `1`,
# not `1.`, not `1..2`. bump.py's `VERSION_RE` is this same rule in python, and
# one table feeds both (tests/test_pkg_sh.py, VersionShapeTest).
#
# It was written twice in shell -- here and in publish-pages.sh -- as
# `*[!0-9.]*`, which accepted a 包文件 name no render can produce and a version
# the render refuses. Two readers of one question is how the pkgrel width drifted
# too, and the repair there was the same: one function, both callers.
version_shape_ok() {
    local version="$1"
    case "$version" in
        *[!0-9.]* | "" | .* | *. | *..*) return 1 ;;
    esac
    case "$version" in
        *.*) return 0 ;;
    esac
    return 1
}

# pkgfile_pkgname <包文件> -- the pkgname in that file's name.
#
# The inverse of the naming rule, for the question pkg.sh did not answer: which
# packages does this directory carry. makepkg writes <pkgname>-<pkgver>-<pkgrel>-
# <arch>, and pkgname itself carries dashes (magpie-bin), so the three fields
# makepkg appends are peeled off the right and whatever is left is the package.
# A name that does not parse is a change to the rule, not a package to guess at.
#
# The version half is `version_shape_ok`, the same rule the render applies to a
# pkgver and the same one publish-pages.sh refuses a hand-run 版本 with: a name
# carrying `0.1..2` is a change to the rule, not a package to read a 版本 from.
#
# The whole-number pkgrel here is the model GLOSSARY states, and it is decided
# where pkgrel enters (bump.py's check_pkgrel, which names the template's own
# pkgrel=): widening this reader alone would leave the two disagreeing again --
# a `-2.1-` file name this one refuses, that one renders and publishes. Say so
# in the glossary first.
pkgfile_pkgname() {
    local name="${1##*/}" suffix base arch rel version
    suffix="$(pkgfile_suffix)"
    case "$name" in
        *"$suffix") base="${name%"$suffix"}" ;;
        *) return 1 ;;
    esac
    arch="${base##*-}"; base="${base%-*}"
    rel="${base##*-}"; base="${base%-*}"
    version="${base##*-}"; base="${base%-*}"
    [ -n "$base" ] || return 1
    version_shape_ok "$version" || return 1
    case "$rel" in
        "" | *[!0-9]*) return 1 ;;
    esac
    [ -n "$arch" ] || return 1
    printf '%s\n' "$base"
}

# pkgfiles_packages <repo dir> -- the packages that directory carries, each
# once, in name order.
#
# build-repo's window only ever counted the packages it had just built, so a
# package removed from the 包清单 was built by nobody, pruned by nobody, and
# carried forward by the seed on every publish -- its last two builds served
# forever. The directory itself is now asked what it holds.
pkgfiles_packages() {
    local out="$1" file pkgname
    local -A seen=()
    while IFS= read -r file; do
        if ! pkgname="$(pkgfile_pkgname "$file")"; then
            echo "not a 包文件 name: ${file##*/}" >&2
            return 1
        fi
        [ -z "${seen[$pkgname]:-}" ] || continue
        seen["$pkgname"]=1
        printf '%s\n' "$pkgname"
    done < <(pkgfiles_all "$out")
}

# pkgfiles_prune <repo dir> <pkgname> <keep> <anchor 包文件> -- the files the
# 保留窗口 drops, oldest first: every 版本 outside the window that ends at the
# build this run published.
#
# GLOSSARY defines the window as 每个包同时保留的版本数, so it counts 版本 and not
# files: the same 版本 built for two arches is one version, and both files stay
# or go together. Counting files kept a single version in a two-arch repository
# and dropped one arch of the older one.
#
# The window ends at <anchor> -- the 包文件 this run built and moved here -- and
# not at the greatest 版本 in the directory. The two are the same answer while
# versions only climb, and they are not the same answer when a human pins one
# (`WANTED`): the pinned 版本 is the oldest file in the directory, so a window
# that started at the maximum deleted the very build the run had just made, and
# the release step then found nothing to publish -- after gh-pages had already
# been force-pushed. Anything above the anchor goes too: it is no longer what
# the database names. The decision lives here, next to the order it reads.
pkgfiles_prune() {
    local out="$1" pkgname="$2" keep="$3" anchor="$4" file version current
    local -a versions=()
    local -A dropped=()
    local index current_index="" first

    while IFS= read -r file; do
        version="$(pkgfile_version "$pkgname" "$file")"
        [ "${#versions[@]}" -gt 0 ] && [ "${versions[${#versions[@]}-1]}" = "$version" ] \
            || versions+=("$version")
    done < <(pkgfiles "$out" "$pkgname")
    # An empty directory has nothing to prune and nothing to anchor on: this is
    # the first publish of a package.
    [ "${#versions[@]}" -gt 0 ] || return 0
    current="$(pkgfile_version "$pkgname" "$anchor")"
    for index in "${!versions[@]}"; do
        if [ "${versions[$index]}" = "$current" ]; then
            current_index="$index"
            break
        fi
    done
    if [ -z "$current_index" ]; then
        echo "the build this run made is not in $out: ${anchor##*/}" >&2
        return 1
    fi
    first=$((current_index - keep + 1))
    [ "$first" -ge 0 ] || first=0
    for index in "${!versions[@]}"; do
        if [ "$index" -lt "$first" ] || [ "$index" -gt "$current_index" ]; then
            dropped["${versions[$index]}"]=1
        fi
    done
    [ "${#dropped[@]}" -gt 0 ] || return 0
    while IFS= read -r file; do
        version="$(pkgfile_version "$pkgname" "$file")"
        if [ -n "${dropped["$version"]:-}" ]; then
            printf '%s\n' "$file"
        fi
    done < <(pkgfiles "$out" "$pkgname")
}

# pkgfiles_current <repo dir> <pkgname> -- the files that belong in the database:
# every arch of the newest build, read off the tail of the same order the window
# reads its head from.
#
# A build is `<pkgver>-<pkgrel>`, not `<pkgver>`: rebuilding 0.1.9 as pkgrel 2
# leaves both files on disk (the window keeps 版本, and they are one 版本), and
# handing repo-add both of them would enter one pkgname twice. The database
# lists the build a client gets today, so it takes the newest `<pkgver>-<pkgrel>`
# and one file per arch.
pkgfiles_current() {
    local out="$1" pkgname="$2" file build newest
    local -a files=()
    mapfile -t files < <(pkgfiles "$out" "$pkgname")
    [ "${#files[@]}" -gt 0 ] || return 0
    newest="$(pkgfile_evr "$pkgname" "${files[${#files[@]}-1]}")"
    for file in "${files[@]}"; do
        build="$(pkgfile_evr "$pkgname" "$file")"
        if [ "$build" = "$newest" ]; then
            printf '%s\n' "$file"
        fi
    done
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
