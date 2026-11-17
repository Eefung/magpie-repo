#!/usr/bin/env bash
# Publish the assembled tree to the branch the 软件源 is served from.
#
#   ./scripts/publish-pages.sh <repo dir> <版本> <remote> <branch>
#
# `<branch>` is required rather than defaulted. The workflow's seed fetches
# the same branch before this writes it, and both read the job env's
# PAGES_BRANCH: a default here would be a second spelling of one name, and
# the seed's copy is the one that fails quietly -- it reports `no branch
# yet`, starts a fresh repository, and the 保留窗口 comes out empty while
# the run stays green.
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

# shellcheck source=lib/pkg.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pkg.sh"

dir="${1:?usage: publish-pages.sh <repo dir> <version> <remote>}"
version="${2:?usage: publish-pages.sh <repo dir> <version> <remote>}"
remote="${3:?usage: publish-pages.sh <repo dir> <version> <remote> <branch>}"
branch="${4:?usage: publish-pages.sh <repo dir> <version> <remote> <branch>}"

# The commit this makes is signed by whatever the workflow's env declares the
# bot to be; a hand-run must name one rather than this script inventing it.
# An identity literal here would be a second source for the fact the job env
# holds, and it is the same persona as the main-branch commit the workflow makes.
GIT_NAME="${GIT_NAME:?publish-pages.sh needs GIT_NAME: the committer name}"
GIT_EMAIL="${GIT_EMAIL:?publish-pages.sh needs GIT_EMAIL: the committer email}"

[ -d "$dir" ] || { echo "no such tree: $dir" >&2; exit 1; }
# The same accept-set the render applies to a pkgver and the name reader applies
# to a 包文件, from pkg.sh: this script's own `*[!0-9.]*` was a third spelling.
version_shape_ok "$version" || { echo "not a 版本: '$version'" >&2; exit 1; }

cd "$dir"

# The branch is a deployment, not a history: init, commit once, force push.
git init --quiet --initial-branch="$branch"
git config user.name "$GIT_NAME"
git config user.email "$GIT_EMAIL"
touch .nojekyll
git add --all
git commit --quiet -m "build($branch): magpie $version"
git push --quiet --force "$remote" "HEAD:$branch"

echo "published $version to $branch: $(git rev-parse --short HEAD)"
