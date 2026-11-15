#!/usr/bin/env bash
# Publish the assembled tree to the branch the 软件源 is served from.
#
#   ./scripts/publish-pages.sh <repo dir> <版本> <remote>
#   ./scripts/publish-pages.sh repo 0.1.4249 "https://x-access-token:...@github.com/o/r.git"
#
# The tree is rebuilt from scratch on every run -- the workflow seeds only the
# 包文件 across, and the database is re-created from them -- so the branch's
# history is one commit that replaces the previous one: a copy of every 30 MB
# package ever served would otherwise stay in the branch forever. That is a
# property of the caller, not of this script: run it twice against the same
# directory and the second commit lands on top of the first, because `git init`
# on an existing repository is a no-op. CI always hands a fresh `$TREE`.
#
# `.nojekyll` is what keeps Pages from putting the tree through Jekyll: without
# it every published file is handed to a static-site build that skips names it
# does not understand and need not be run at all.
#
# `<remote>` is whatever `git push` accepts, a URL or a path. The token URL is
# the workflow's business and never this script's: it is passed in, so the
# script itself holds no secret and the same code runs against a local bare
# repository in tests/test_publish_pages.py.
set -euo pipefail

dir="${1:?usage: publish-pages.sh <repo dir> <version> <remote>}"
version="${2:?usage: publish-pages.sh <repo dir> <version> <remote>}"
remote="${3:?usage: publish-pages.sh <repo dir> <version> <remote>}"

[ -d "$dir" ] || { echo "no such tree: $dir" >&2; exit 1; }
case "$version" in
    "" | *[!0-9.]*) echo "not a 版本: '$version'" >&2; exit 1 ;;
esac

cd "$dir"

# The branch is a deployment, not a history: init, commit once, force push.
git init --quiet --initial-branch=gh-pages
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
touch .nojekyll
git add --all
git commit --quiet -m "build(gh-pages): magpie $version"
git push --quiet --force "$remote" HEAD:gh-pages

echo "published $version to gh-pages: $(git rev-parse --short HEAD)"
