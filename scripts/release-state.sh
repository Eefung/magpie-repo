#!/usr/bin/env bash
# Does v<版本> already have a GitHub Release? Answer with a value, not a status.
#
#   ./scripts/release-state.sh <版本> <owner/repo>
#   → release=present | release=absent
#   → tag=v<版本>
#
# The tag travels with the answer because it is the question this script
# asked: the step that creates, uploads and edits a Release spelled `v$版本`
# four times over -- a second place for a name the lookup already holds, and
# the one that would have drifted in silence, since nothing read it.
#
# The Release step used to branch on `gh release view "v$VER" >/dev/null 2>&1`:
# "this tag has no Release yet" and "the query itself failed" were the same
# signal, so a transient API failure took the create path and the run died
# reporting `already_exists` -- the one thing that was not true, after gh-pages
# had already been pushed.
#
# So the answer GitHub gives (the HTTP status) is read as a value. 200 is
# present, 404 is absent, anything else is an error to report: a 500 or a rate
# limit is not "no Release", and reading it as one is the bug this replaced.
#
# Reading `-i` output is deliberate: gh prints the status line for a 404 too, so
# the answer comes from the response rather than from an exit status.
#
# GH is the one adapter: the step in publish.yml runs the real gh, and
# tests/test_release_state.py runs a stub that answers whatever status it is
# told to.
set -euo pipefail

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"

version="${1:?usage: release-state.sh <版本> <owner/repo>}"
repo="${2:?usage: release-state.sh <版本> <owner/repo>}"
GH="${GH:-gh}"

# `|| true` because a non-2xx answer is expected on the absent path: the value
# is in the output, and the exit status would throw it away.
response="$("$GH" api -i "repos/$repo/releases/tags/v$version" 2>/dev/null || true)"
# The status line is the first line of an `-i` answer. Read that line only --
# no pipe, so a large body has no way to turn into a SIGPIPE that `pipefail`
# would then report as a failed lookup.
code="$(printf '%s\n' "$response" | sed -n '1s|^HTTP/[0-9.]* \([0-9]\{3\}\).*|\1|p')"

# Through the table's write gate: `release` is one of two words and `tag` is a
# 版本 with a `v` in front, so a reader downstream can act on them without a
# second check of its own.
case "$code" in
    200) frozen_write release present ;;
    404) frozen_write release absent ;;
    "")
        echo "the release lookup for v$version answered nothing; is gh authenticated?" >&2
        exit 1
        ;;
    *)
        echo "the release lookup for v$version answered HTTP $code" >&2
        exit 1
        ;;
esac
# The tag the lookup asked about, written once: the state is what the case
# decides, and both answers carry the same version back.
frozen_write tag "v$version"
