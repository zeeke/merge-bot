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

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
WORKDIR="${SCRIPT_DIR}/../.backport-work/${REPO}"

if [[ -d "$WORKDIR/repo/.git" ]]; then
	echo "Reusing existing clone in $WORKDIR..."
	cd "$WORKDIR/repo"
	git fetch origin "$SOURCE" "$TARGET"
else
	rm -rf "$WORKDIR"
	mkdir -p "$WORKDIR"
	echo "Cloning $REPO into $WORKDIR..."
	git clone --no-tags \
		"https://x-access-token:${TOKEN}@github.com/${REPO}.git" \
		"$WORKDIR/repo"
	cd "$WORKDIR/repo"
	git fetch origin "$SOURCE" "$TARGET"
fi

VERSION_NOISE='-4-22|-4-21|-5-0|4\.22|4\.21|5\.0|createdAt'

mapfile -t ALL_COMMITS < <(
	git log --cherry-pick --right-only --no-merges \
		--pretty=tformat:%H \
		"origin/${TARGET}...origin/${SOURCE}" \
	| tac
)

COMMITS=()
for c in "${ALL_COMMITS[@]}"; do
	subject=$(git log --pretty=tformat:%s -1 "$c")

	if [[ -n "$SKIP_PATTERN" ]] && echo "$subject" | grep -qE "$SKIP_PATTERN"; then
		echo "Skipping (subject match): $(git log --oneline -1 "$c")"
		continue
	fi

	meaningful=$(git diff "$c^" "$c" --unified=0 \
		| grep '^[+-]' | grep -vE '^(\+\+\+|---)' \
		| grep -cvE -- "$VERSION_NOISE" || true)
	if [[ "$meaningful" -eq 0 ]]; then
		echo "Skipping (version-only): $(git log --oneline -1 "$c")"
		continue
	fi

	COMMITS+=("$c")
done

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
if git show-ref --verify --quiet "refs/heads/$WORK_BRANCH"; then
	echo "Resuming existing branch $WORK_BRANCH..."
	git checkout "$WORK_BRANCH"
else
	git checkout -b "$WORK_BRANCH" "origin/${TARGET}"
fi

declare -A STILL_NEEDED
while IFS= read -r h; do
	STILL_NEEDED[$h]=1
done < <(git log --cherry-pick --right-only --no-merges --pretty=tformat:%H "HEAD...origin/${SOURCE}")

for c in "${COMMITS[@]}"; do
	if [[ -z "${STILL_NEEDED[$c]:-}" ]]; then
		echo "Already picked: $(git log --oneline -1 "$c")"
		continue
	fi
	echo "Cherry-picking $(git log --oneline -1 "$c")..."
	if ! git cherry-pick "$c"; then
		CONFLICTED=$(git diff --name-only --diff-filter=U)
		COMMIT_MSG=$(git log --pretty=tformat:%s -1 "$c")
		echo "Conflicts detected, asking Claude to resolve..."
		claude -p "You are resolving git cherry-pick conflicts during a backport from branch '$SOURCE' to '$TARGET'.
The commit being cherry-picked is: $COMMIT_MSG ($c)

The following files have conflicts:
$CONFLICTED

Rules:
- For clusterserviceversion files pick our version of the 'createAt' field (the $TARGET branch side).
- For all other files, resolve the conflicts intelligently by understanding the intent of the cherry-picked commit and preserving it while keeping it compatible with the target branch.
- Edit each conflicted file to remove all conflict markers (<<<<<<< HEAD, =======, >>>>>>> ...) and produce a valid, working file.
- After editing, run 'git add' on each resolved file.
- Do NOT run 'git cherry-pick --continue' or '--skip', just resolve and stage." --allowedTools 'Bash(git:*)' 'Edit' 'Read'

		if git diff --name-only --diff-filter=U | grep -q .; then
			echo "Error: Claude could not resolve all conflicts." >&2
			echo "Resolve manually in: $(pwd)" >&2
			exit 1
		fi

		if git diff --cached --quiet; then
			echo "No changes after resolving conflicts, skipping commit."
			git cherry-pick --skip
		else
			GIT_EDITOR=true git cherry-pick --continue
		fi
	fi
done

if [[ "$FORK" != "$REPO" ]]; then
	if ! git remote get-url fork &>/dev/null; then
		git remote add fork "https://x-access-token:${TOKEN}@github.com/${FORK}.git"
	fi
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
