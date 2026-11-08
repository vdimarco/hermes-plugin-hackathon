#!/usr/bin/env bash
# Open one NousResearch/hermes-agent PR per plugin, each adding plugin-catalog/<name>.yaml.
#
# Run it from a clone of vdimarco/hermes-plugin-hackathon, on your own machine,
# signed in with `gh auth login` as the owner of this repo (catalog rule 5).
#
#   scripts/open-catalog-prs.sh            # dry run: validate, prepare branches, show the PRs
#   scripts/open-catalog-prs.sh --open     # also push the branches and open the PRs
#   scripts/open-catalog-prs.sh --open contract   # only one plugin
#
# Needs: git, gh. Uses hermes (if installed) to validate each plugin at the pinned sha.
set -euo pipefail

OPEN=0
PLUGINS=()
for arg in "$@"; do
  case "$arg" in
    --open) OPEN=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) PLUGINS+=("$arg") ;;
  esac
done
[ ${#PLUGINS[@]} -eq 0 ] && PLUGINS=(contract multiverse)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UPSTREAM="NousResearch/hermes-agent"
WORK="${WORK:-$ROOT/.catalog-work}"
ME="$(gh api user --jq .login)"

echo "GitHub user: $ME"
mkdir -p "$WORK"

# 1. Validate each plugin exactly as the catalog CI will: at the pinned sha.
for name in "${PLUGINS[@]}"; do
  entry="$ROOT/catalog/$name.yaml"
  [ -f "$entry" ] || { echo "no catalog/$name.yaml"; exit 1; }
  sha="$(sed -n 's/^sha: *//p' "$entry")"
  subdir="$(sed -n 's/^subdir: *//p' "$entry")"
  case "$sha" in [0-9a-f]*) ;; *) echo "$name: bad sha"; exit 1 ;; esac
  [ ${#sha} -eq 40 ] || { echo "$name: sha must be 40 characters"; exit 1; }
  git -C "$ROOT" cat-file -e "$sha:$subdir/plugin.yaml" || { echo "$name: $sha has no $subdir/plugin.yaml (git fetch first?)"; exit 1; }
  if command -v hermes >/dev/null 2>&1; then
    snap="$WORK/validate-$name"
    rm -rf "$snap" && mkdir -p "$snap"
    git -C "$ROOT" archive "$sha" "$subdir" | tar -x -C "$snap"
    echo "== hermes plugins validate $name @ ${sha:0:12}"
    hermes plugins validate "$snap/$subdir" --install-deps
  else
    echo "!! hermes is not installed: skipping validation of $name (the catalog CI will run it)"
  fi
done

# 2. Fork and clone hermes-agent (shallow).
if ! gh repo view "$ME/hermes-agent" >/dev/null 2>&1; then
  if [ $OPEN -eq 1 ]; then
    gh repo fork "$UPSTREAM" --clone=false
  else
    echo "(dry run) would fork $UPSTREAM to $ME/hermes-agent"
  fi
fi
CLONE="$WORK/hermes-agent"
if [ ! -d "$CLONE/.git" ]; then
  git clone --depth 1 "https://github.com/$UPSTREAM.git" "$CLONE"
fi
git -C "$CLONE" fetch --depth 1 origin main
if ! git -C "$CLONE" remote get-url fork >/dev/null 2>&1; then
  git -C "$CLONE" remote add fork "https://github.com/$ME/hermes-agent.git"
fi

# 3. One branch, one file, one PR per plugin.
for name in "${PLUGINS[@]}"; do
  branch="plugin-catalog/add-$name"
  target="plugin-catalog/$name.yaml"
  title="feat(plugin-catalog): add $name"
  git -C "$CLONE" checkout -q -B "$branch" origin/main
  if git -C "$CLONE" cat-file -e "origin/main:$target" 2>/dev/null; then
    echo "!! $target already exists upstream: skipping $name"
    continue
  fi
  grep -v '^#' "$ROOT/catalog/$name.yaml" > "$CLONE/$target"   # drop the local draft comments
  git -C "$CLONE" add "$target"
  git -C "$CLONE" commit -q -m "$title"
  echo
  echo "== $name: branch $branch, file $target"
  git -C "$CLONE" show --stat --oneline HEAD | head -3
  body="$ROOT/docs/catalog-prs/$name.md"
  if [ $OPEN -eq 1 ]; then
    git -C "$CLONE" push -u fork "$branch" --force-with-lease
    gh pr create --repo "$UPSTREAM" --base main --head "$ME:$branch" --title "$title" --body-file "$body"
  else
    echo "(dry run) would push $branch to $ME/hermes-agent and run:"
    echo "  gh pr create --repo $UPSTREAM --base main --head $ME:$branch --title \"$title\" --body-file docs/catalog-prs/$name.md"
  fi
done

echo
if [ $OPEN -eq 1 ]; then
  echo "Done. Put each PR link in the hackathon form: http://form.typeform.com/to/FwJLvOln"
else
  echo "Dry run finished. Re-run with --open to push and open the PRs."
fi
