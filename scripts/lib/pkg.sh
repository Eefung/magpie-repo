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
# write them, and every read and write goes through the gates below --
# frozen_var / frozen_list / frozen_write -- which consult the kinds table, so a
# key's 域, shape and "may it be absent" are stated once. `line_field` and
# `line_field_or` are their inner reads: no step or script outside this module
# calls them (tests/test_frozen.py holds that), and the one test-side reader
# that does is asking about names this table does not rule on.
#
# bump.py also writes the 包清单 as one `package <dir> <pkgname>` line per
# package. That shape has one reader here (pkglist_field) for the same reason:
# three steps used to split the same line and one of them read the pkgname back
# out of the PKGBUILD.

# frozen_count <键> <文件> -- how many lines of that key the file holds.
#
# The one place the counting rule lives: the reader, the reader-with-default and
# the gates' absent-or-broken split all ask this question, and each used to spell
# the grep itself.
frozen_count() { grep -c "^$1=" "$2" || true; }

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
    count="$(frozen_count "$name" "$file")"
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
# two `bump=` lines is a producer that wrote twice. No production caller is left
# -- `frozen_var` answers "may this key be absent" from the table -- and the one
# that remains is tests/frozen.py, which asks about a key it cannot name in
# advance and wants `None` for an absence the gates deliberately refuse to
# answer. That is why this stays rather than moving with the rest. `line_field ... || true`
# collapses the two into the default, so a duplicated line read as "nothing to
# do" -- a check that broke now looks like a quiet day.
line_field_or() {
    local name="$1" file="$2" default="$3" count
    [ -r "$file" ] || { echo "no such file: $file" >&2; return 1; }
    count="$(frozen_count "$name" "$file")"
    if [ "${count:-0}" -eq 0 ]; then
        printf '%s\n' "$default"
        return 0
    fi
    line_field "$name" "$file"
}

# ---- the kinds table -------------------------------------------------------
#
# A frozen line is a kind of thing, not a bare name: what a key may say, whether
# a missing line is a failure, whether the value names a file, and who produces
# it. Those answers used to belong to whoever happened to read the key -- the
# workflow carried its own `case` for `release=`, and an `assets=` naming a file
# that lists nothing uploaded the checksums alone and exited 0.
#
#   <键>|<域>|<形状>|<缺行>|<生产者>
#
# 域 is a word `frozen_domain_allows` interprets (version / flag /
# present-or-absent / v<version> / package-name / integer / gates), `-` where
# the shape is what matters. 形状 is value / path / list / pkglist. 缺行 is
# required / optional. 生产者 is what writes it: a script's name (release,
# release-state, build-check, probe-repo-build, check) or one of bump.py's modes
# (`entry-check`, `render`, `packages`). It is read by the guard that lands with
# the last of the moves, not by a gate -- and the entry check is `entry-check`
# rather than `check`, because `check` is check.sh's own `tests=`/`gates=`.
#
# The table is data: no branch lives here, and `frozen_var` / `frozen_list` /
# `frozen_write` are the only code that reads it. A key it does not name cannot
# be written or read.
frozen_table() {
    cat <<'TABLE'
upstream|version|value|required|entry-check
target|version|value|required|entry-check
bump|flag|value|required|entry-check
pin|flag|value|required|entry-check
ver|version|value|required|render
release|present-or-absent|value|required|release-state
tag|v<version>|value|required|release-state
pkgname|package-name|value|required|build-check
artifact|-|path|required|build-check
assets|-|list|required|release
sums|-|path|required|release
notes|-|path|required|release
assertions|integer|value|required|probe-repo-build
tests|integer|value|required|check
gates|gates|value|optional|check
package|-|pkglist|required|packages
TABLE
}

# Where the cells a gate reads land in the row `frozen_row` hands back, so the
# gate names them instead of spelling an index. These move with the header above
# and with nothing else; `frozen_field`'s 1..5 is the other question -- a column
# number the caller names, matching this order but not spelled from it.
FROZEN_DOMAIN=1 FROZEN_SHAPE=2 FROZEN_REQUIRED=3

# frozen_row <键> <数组名> -- one parse of the table, the key's five cells.
#
# The three gates read their row from here -- `frozen_var` takes three cells,
# `frozen_write` two, `frozen_list` one. The answer goes into the caller's
# array, the way `frozen_list` and `frozen_entries` hand theirs back -- printing
# it for the caller's `mapfile` would make the swallow this module exists to
# prevent writable again. The caller declares the array first (`cells=()`).
frozen_row() {
    # Every local here is prefixed `_frozen_`: a nameref resolves against this
    # function's own scope first, so an internal named after anything a caller
    # might hand in absorbs the assignment and the call still exits 0. `_row` --
    # which is exactly what `frozen_field` passes -- did that.
    local _key="$1" _target="$2" _frozen_line
    _frozen_line="$(frozen_table | awk -F'|' -v key="$_key" '$1 == key { print; found = 1; exit } END { exit !found }')" || {
        echo "no such frozen key: ${_key}" >&2
        return 1
    }
    local -a _frozen_cells=()
    IFS='|' read -r -a _frozen_cells <<<"$_frozen_line"
    # The nameref is a name on one line and an array on the next, as in
    # `frozen_entries`: that is what handing an array back through a name looks
    # like. It is `_frozen_out` rather than the module's usual `_out` because
    # the linter types a nameref by name, not by target: calling this one `_out`
    # made the string assignments in `frozen_var` and `frozen_list` read as
    # arrays (SC2178) in functions this ticket does not touch.
    # shellcheck disable=SC2178
    local -n _frozen_out="$_target"
    _frozen_out=("${_frozen_cells[@]}")
}

# frozen_field <键> <列> -- one cell of that key's row.
#
# No gate calls this any more: every gate reads its row, and this is the reader
# for a question about one column -- the suite's table assertions, and a caller
# that wants to name a cell without taking a row. Kept because it is that
# question's only answer, the way `line_field_or` is kept for the one test-side
# reader that asks a key no gate names.
#
# The column is checked before the key, so a call naming both a column the table
# has not got and a key it never heard of hears about the column. An unnamed key
# is refused by name, not treated as a bare name that happens to have no rules.
frozen_field() {
    local _key="$1" _column="$2"
    case "$_column" in
        1 | 2 | 3 | 4 | 5) ;;
        *) echo "frozen_field: the table has no column ${_column}" >&2; return 1 ;;
    esac
    local -a _row=()
    frozen_row "$_key" _row || return 1
    printf '%s\n' "${_row[$((_column - 1))]}"
}

# frozen_var <键> <文件> <调用方的变量名> [缺省] -- the key's value, assigned.
#
# The answer goes into the caller's variable rather than onto stdout, so the
# shape that swallows a reader's failure -- `mapfile -t x < <(reader)`, which
# exits 0 after the reader refused -- cannot be written at all.
#
# The caller declares that variable first (`local x=''`). An undeclared name is
# created at the top level instead, which is right for a step or a script and
# silently wrong inside a function that meant a local -- the answer lands in a
# global and the local it declared stays empty.
#
# The default is the caller's judgement (an absent `gates=` means a tree older
# than the contract), so it is only accepted for a key the table calls optional:
# a default on a required key would hide the absence it exists to report, and an
# optional key read without one leaves a caller with no answer at all.
frozen_var() {
    local _key="$1" _file="$2" _target="$3" _value
    local -a _row=()
    frozen_row "$_key" _row || return 1
    local _domain="${_row[$FROZEN_DOMAIN]}" _shape="${_row[$FROZEN_SHAPE]}"
    local _required="${_row[$FROZEN_REQUIRED]}"
    case "$_shape" in
        value | path) ;;
        *) frozen_wrong_door "$_key" value "$_shape"
           return 1 ;;
    esac
    if [ -n "${4+set}" ] && [ "$_required" != optional ]; then
        echo "${_key}= is required; a default would hide its absence" >&2
        return 1
    fi
    if [ -z "${4+set}" ] && [ "$_required" = optional ]; then
        echo "${_key}= may be absent; name the default to use when it is" >&2
        return 1
    fi
    _value="$(frozen_read "$_key" "$_file")" || return 1
    if [ "$_value" = "$FROZEN_ABSENT" ]; then
        # What an absence means is the row's answer, and the gate is the one
        # holding it (`frozen_read` no longer asks). A required key's absence is
        # this gate's complaint; an optional one's is the caller's judgement,
        # handed on as it came -- `pre-contract` is not a 版本, and judging it as
        # one would refuse the sentence the caller meant.
        if [ "$_required" != optional ]; then
            echo "no ${_key}= in ${_file}" >&2
            return 1
        fi
        _value="$4"
    else
        case "$_shape" in
            value) frozen_domain_allows "$_domain" "$_key" "$_value" || return 1 ;;
            path) frozen_must_be_file "$_key" "$_value" || return 1 ;;
        esac
    fi
    local -n _out="$_target"
    _out="$_value"
}

# A value no producer writes, so "the line is absent" stays a fact here.
FROZEN_ABSENT="__absent__"

# frozen_read <键> <文件> -- the line's value, or FROZEN_ABSENT.
#
# This answers one question -- what the file says this key is -- and hands the
# absence back as a value for the caller to interpret: the gate already holds
# the row's `required` cell, and asking the table again here made "may this key
# be absent" a second answer point. A line written twice or written empty stays
# a protocol break, which is `line_field`'s complaint.
frozen_read() {
    local _key="$1" _file="$2" _count
    [ -r "$_file" ] || { echo "no such file: ${_file}" >&2; return 1; }
    _count="$(frozen_count "$_key" "$_file")"
    if [ "${_count:-0}" -eq 0 ]; then
        printf '%s\n' "$FROZEN_ABSENT"
        return 0
    fi
    line_field "$_key" "$_file"
}

# frozen_list <键> <文件> <调用方的数组名> -- the paths a list-shaped line names.
#
# Two refusals belong to the shape and not to the caller: a key whose value file
# lists nothing, and one that lists a path that is not there. Both used to be
# silent -- `mapfile` made an empty array of the first, and `gh release` took the
# empty `"${assets[@]}"` as "no assets to upload" and exited 0, so a Release with
# the checksums and none of the packages was a green run.
frozen_list() {
    local _key="$1" _file="$2" _target="$3" _value
    local -a _row=()
    frozen_row "$_key" _row || return 1
    local _shape="${_row[$FROZEN_SHAPE]}"
    if [ "$_shape" != list ]; then
        frozen_wrong_door "$_key" list "$_shape"
        return 1
    fi
    _value="$(line_field "$_key" "$_file")" || return 1
    frozen_must_be_file "$_key" "$_value" || return 1
    local -a _entries=()
    frozen_entries "$_key" "$_value" _entries || return 1
    local -n _out="$_target"
    _out=("${_entries[@]}")
}

# frozen_entries <键> <文件> <调用方的数组名> -- the paths a list file names.
#
# One rule, two callers -- the read that hands the list on and the write that
# refuses to hand over a list naming nothing. They disagreed once: the write gate
# took any non-empty file, and the read then refused its entries.
frozen_entries() {
    local _key="$1" _file="$2" _target="$3" _entry
    local -a _found=()
    while IFS= read -r _entry; do
        [ -n "$_entry" ] || continue
        [ -f "$_entry" ] || {
            echo "${_key}=${_file} lists ${_entry}, and that is not a file" >&2
            return 1
        }
        _found+=("$_entry")
    done <"$_file"
    [ "${#_found[@]}" -gt 0 ] || {
        echo "${_key}=${_file} lists nothing" >&2
        return 1
    }
    # The nameref is a name on one line and an array on the next: that is what
    # handing an array back through a name looks like, and the alternative --
    # printing it for the caller's `mapfile` -- is the swallow this gate exists
    # to make unwritable.
    # shellcheck disable=SC2178
    local -n _out="$_target"
    _out=("${_found[@]}")
}

# frozen_wrong_door <键> <这个门读的形状> <键的形状> -- the refusal a gate gives a
# key whose shape belongs to another door (the 包清单's rows have
# `pkglist_field`). The shape is an argument because every caller has read it
# already: asking the table again here would make a refused call parse it twice.
frozen_wrong_door() {
    echo "${1}= is a ${3}, not a ${2}; read it through its own door" >&2
    return 1
}

# frozen_must_be_file <键> <值> -- a path-shaped value is only read if it is one.
frozen_must_be_file() {
    [ -f "$2" ] || {
        echo "${1}=${2} is not a file" >&2
        return 1
    }
}

# frozen_write <键> <值> -- one frozen line, or the reason it cannot be written.
#
# The producer's half of the same table: a value outside its 域, a path naming no
# file, and a list naming no paths are refused where they are produced rather
# than where a consumer trips over them.
frozen_write() {
    local _key="$1" _value="$2"
    local -a _row=()
    frozen_row "$_key" _row || return 1
    local _domain="${_row[$FROZEN_DOMAIN]}" _shape="${_row[$FROZEN_SHAPE]}"
    [ -n "$_value" ] || {
        echo "${_key}= needs a value; it would write an empty line" >&2
        return 1
    }
    case "$_shape" in
        value) frozen_domain_allows "$_domain" "$_key" "$_value" || return 1 ;;
        path) frozen_must_be_file "$_key" "$_value" || return 1 ;;
        list)
            frozen_must_be_file "$_key" "$_value" || return 1
            local -a _unused=()
            frozen_entries "$_key" "$_value" _unused || return 1 ;;
        *) echo "the table gives ${_key} the shape ${_shape}, which no gate writes" >&2
           return 1 ;;
    esac
    printf '%s=%s\n' "$_key" "$_value"
}

# frozen_domain_allows <域> <键> <值> -- does the value fit that 域.
#
# One interpreter for the table's vocabulary, so the tokens stay data: a token
# nobody interprets is a refusal here, not a value that slips through. The 域 is
# an argument because the caller has already read the row it lives in; reading
# it again here made one gate call parse the table twice. The key stays for the
# sentence, and the sentence is the interface.
frozen_domain_allows() {
    local _domain="$1" _key="$2" _value="$3"
    case "$_domain" in
        -) return 0 ;;
        version)
            version_shape_ok "$_value" || {
                echo "${_key}=${_value} is not a 版本" >&2
                return 1
            } ;;
        flag)
            case "$_value" in 0 | 1) return 0 ;; esac
            echo "${_key}=${_value} is not 0 or 1" >&2
            return 1 ;;
        present-or-absent)
            case "$_value" in present | absent) return 0 ;; esac
            echo "${_key}=${_value} is not present or absent" >&2
            return 1 ;;
        'v<version>')
            case "$_value" in
                v?*) version_shape_ok "${_value#v}" && return 0 ;;
            esac
            echo "${_key}=${_value} is not v<版本>" >&2
            return 1 ;;
        package-name)
            case "$_value" in
                "" | */* | *[[:space:]]*) ;;
                *) return 0 ;;
            esac
            echo "${_key}=${_value} is not a 包名" >&2
            return 1 ;;
        integer)
            case "$_value" in
                "" | *[!0-9]*) ;;
                *) return 0 ;;
            esac
            echo "${_key}=${_value} is not an integer" >&2
            return 1 ;;
        gates)
            case "$_value" in
                "" | *[!0-9/]* | /* | */ | */*/*) ;;
                */*) return 0 ;;
            esac
            echo "${_key}=${_value} is not <整数>/<整数>" >&2
            return 1 ;;
        *)
            echo "the table gives ${_key} the 域 '${_domain}', which no gate interprets" >&2
            return 1 ;;
    esac
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
