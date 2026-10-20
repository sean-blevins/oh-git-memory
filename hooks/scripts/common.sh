#!/bin/sh
# Shared setup for the oh-git-memory hooks.
# Sourced by the ohgm-*.sh task scripts - not run directly.
#
# Sets, after a successful ohgm_init:
#   OHGM_DIR      clone location
#   OHGM_GLOBAL   path to global/MEMORY.md
#   OHGM_PROJECT  path to agents/<key>/MEMORY.md

SCRIPT_DIR=$(dirname "$0")

# The clone lives in $HOME (per-conversation persistence) and is reused across
# restarts. The repo is what is shared, not the working copy.
ohgm_dir() { printf '%s' "${OH_GIT_MEMORY_DIR:-$HOME/.oh-git-memory}"; }

# Resolve config, clone if needed, and create the folders and files this
# conversation needs. Returns 1 if the plugin is not configured, so the caller can
# exit 0 quietly.
#
# Publishing is deliberately left to the caller: the Stop sequence must not
# commit anything until the notes have been staged and filed.
ohgm_init() {
  OHGM_DIR=$(ohgm_dir)
  OHGM_REPO="${OH_GIT_MEMORY_REPO:-}"
  [ -n "$OHGM_REPO" ] || return 1

  # A private https repo needs a token; a local path or file:// URL does not.
  OHGM_TOKEN="${OH_GIT_MEMORY_TOKEN:-${GITHUB_TOKEN:-}}"
  case "$OHGM_REPO" in
    https://*)
      [ -n "$OHGM_TOKEN" ] || return 1
      OHGM_URL="https://x-access-token:${OHGM_TOKEN}@${OHGM_REPO#https://}"
      ;;
    *) OHGM_URL="$OHGM_REPO" ;;
  esac
  export GIT_TERMINAL_PROMPT=0
  # Backstop for the whole hook chain: a .pyc dropped next to the scripts is picked
  # up by the installer and shipped, so writing bytecode here is never wanted.
  export PYTHONDONTWRITEBYTECODE=1

  # Which folder this conversation writes to: always the agent profile id, so the
  # only things a user must supply are the repo URL and a token.
  OHGM_KEY=$(python3 "$SCRIPT_DIR/profile_key.py" 2>/dev/null)
  [ -n "$OHGM_KEY" ] || OHGM_KEY=default

  if [ ! -d "$OHGM_DIR/.git" ]; then
    timeout 25 git clone -q "$OHGM_URL" "$OHGM_DIR" 2>/dev/null || return 1
  fi
  git -C "$OHGM_DIR" config user.email agent@openhands.local 2>/dev/null
  git -C "$OHGM_DIR" config user.name openhands-agent 2>/dev/null

  OHGM_GLOBAL="$OHGM_DIR/global/MEMORY.md"
  OHGM_PROJECT="$OHGM_DIR/agents/$OHGM_KEY/MEMORY.md"

  # Bootstrap: the user's repo may start completely empty.
  mkdir -p "$OHGM_DIR/global" "$OHGM_DIR/agents/$OHGM_KEY"
  [ -f "$OHGM_DIR/.gitattributes" ] || printf '*.md merge=union\n' > "$OHGM_DIR/.gitattributes"
  # Scratch state (transcript mark, agent inputs, staged notes) lives in .ohgm/ and
  # must never be committed: the repo is for memory, not plugin bookkeeping.
  [ -f "$OHGM_DIR/.gitignore" ] || : > "$OHGM_DIR/.gitignore"
  grep -qx '.ohgm/' "$OHGM_DIR/.gitignore" 2>/dev/null || printf '.ohgm/\n' >> "$OHGM_DIR/.gitignore"
  [ -f "$OHGM_GLOBAL" ] || : > "$OHGM_GLOBAL"
  [ -f "$OHGM_PROJECT" ] || : > "$OHGM_PROJECT"
}

ohgm_pull() { timeout 12 git -C "$OHGM_DIR" pull --rebase --autostash -q 2>/dev/null || true; }

# Commit and push anything changed. -u sets upstream on the first push, so this
# also works against a brand-new empty repo. Bounded and best-effort.
ohgm_publish() {
  git -C "$OHGM_DIR" add -A 2>/dev/null || return 0
  git -C "$OHGM_DIR" diff --cached --quiet 2>/dev/null && return 0
  git -C "$OHGM_DIR" commit -q -m "memory: update $(date -u +%FT%TZ)" 2>/dev/null || return 0
  timeout 12 git -C "$OHGM_DIR" push -q -u origin HEAD 2>/dev/null && return 0
  ohgm_pull
  timeout 12 git -C "$OHGM_DIR" push -q -u origin HEAD 2>/dev/null || true
}
