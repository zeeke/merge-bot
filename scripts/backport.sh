#!/usr/bin/env bash
set -euo pipefail

usage() {
	cat <<-EOF
	Usage: $(basename "$0") [OPTIONS]

	Cherry-pick all commits from a source branch that are missing in a target
	branch, push them to a work branch, and open a pull request.

	Required:
	  --repo <owner/repo>         GitHub repository (e.g. openshift-kni/openperouter)
	  --source <branch>           Branch containing the commits to backport
	  --target <branch>           Branch to backport into
	  --github-token <path>       Path to a file containing a GitHub token

	Optional:
	  --fork <owner/repo>         Fork to push the work branch to (defaults to --repo)
	  --skip-pattern <regex>      Skip commits whose subject matches this grep regex
	  --dry-run                   List commits that would be backported, then exit
	  -h, --help                  Show this help message
	EOF
}

REPO=""
SOURCE=""
TARGET=""
TOKEN_FILE=""
FORK=""
SKIP_PATTERN=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
	case "$1" in
		--repo)        REPO="$2";         shift 2 ;;
		--source)      SOURCE="$2";       shift 2 ;;
		--target)      TARGET="$2";       shift 2 ;;
		--github-token) TOKEN_FILE="$2";  shift 2 ;;
		--fork)        FORK="$2";         shift 2 ;;
		--skip-pattern) SKIP_PATTERN="$2"; shift 2 ;;
		--dry-run)     DRY_RUN=true;      shift   ;;
		-h|--help)     usage; exit 0              ;;
		*)             echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
	esac
done

if [[ -z "$REPO" || -z "$SOURCE" || -z "$TARGET" || -z "$TOKEN_FILE" ]]; then
	echo "Error: --repo, --source, --target, and --github-token are required." >&2
	usage >&2
	exit 1
fi

if [[ ! -f "$TOKEN_FILE" ]]; then
	echo "Error: token file not found: $TOKEN_FILE" >&2
	exit 1
fi

TOKEN=$(cat "$TOKEN_FILE")
FORK="${FORK:-$REPO}"

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

echo "Cloning $REPO into $WORKDIR..."
git clone --no-tags \
	"https://x-access-token:${TOKEN}@github.com/${REPO}.git" \
	"$WORKDIR/repo"
cd "$WORKDIR/repo"

git fetch origin "$SOURCE" "$TARGET"

mapfile -t COMMITS < <(
	git log --cherry-pick --right-only --no-merges \
		--pretty=tformat:%H \
		"origin/${TARGET}...origin/${SOURCE}" \
	| tac
)

if [[ -n "$SKIP_PATTERN" ]]; then
	FILTERED=()
	for c in "${COMMITS[@]}"; do
		subject=$(git log --pretty=tformat:%s -1 "$c")
		if echo "$subject" | grep -qE "$SKIP_PATTERN"; then
			echo "Skipping: $(git log --oneline -1 "$c")"
		else
			FILTERED+=("$c")
		fi
	done
	COMMITS=("${FILTERED[@]+"${FILTERED[@]}"}")
fi

if [[ ${#COMMITS[@]} -eq 0 ]]; then
	echo "No commits to backport — $SOURCE and $TARGET are in sync."
	exit 0
fi

echo "Found ${#COMMITS[@]} commit(s) to backport:"
for c in "${COMMITS[@]}"; do
	git --no-pager log --oneline -1 "$c"
done

if $DRY_RUN; then
	echo "(dry-run) Exiting without making changes."
	exit 0
fi

WORK_BRANCH="backport/${SOURCE}-to-${TARGET}"
git checkout -b "$WORK_BRANCH" "origin/${TARGET}"

for c in "${COMMITS[@]}"; do
	echo "Cherry-picking $(git log --oneline -1 "$c")..."
	if ! git cherry-pick "$c"; then
		echo "Error: cherry-pick failed on commit $c" >&2
		echo "Aborting." >&2
		git cherry-pick --abort 2>/dev/null || true
		exit 1
	fi
done

if [[ "$FORK" != "$REPO" ]]; then
	git remote add fork "https://x-access-token:${TOKEN}@github.com/${FORK}.git"
	PUSH_REMOTE=fork
else
	PUSH_REMOTE=origin
fi

echo "Pushing $WORK_BRANCH to $FORK..."
git push --force "$PUSH_REMOTE" "$WORK_BRANCH"

FORK_OWNER="${FORK%%/*}"
PR_HEAD="${FORK_OWNER}:${WORK_BRANCH}"

echo "Creating pull request..."
export GH_TOKEN="$TOKEN"
PR_URL=$(gh pr create \
	--repo "$REPO" \
	--base "$TARGET" \
	--head "$PR_HEAD" \
	--title "Backport $SOURCE to $TARGET" \
	--body "Automated backport of all commits from \`$SOURCE\` missing in \`$TARGET\`.")

echo "Pull request created: $PR_URL"
