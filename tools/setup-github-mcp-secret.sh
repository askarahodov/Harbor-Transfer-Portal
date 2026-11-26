#!/usr/bin/env sh
# Provision the GitHub MCP token for Kilo without storing it in any tracked or
# project-local config file.
#
# Usage:
#   ./tools/setup-github-mcp-secret.sh
#
# Reads GITHUB_PERSONAL_ACCESS_TOKEN from the environment (or prompts), then
# writes a 0600 env file outside the repository. `.kilo/kilo.jsonc` only ever
# references that file path, never the token itself.
#
# The token must be read-only scoped for this project (GITHUB_READ_ONLY=1).
set -eu

secret_dir="${XDG_CONFIG_HOME:-$HOME/.config}/kilo/secrets"
secret_file="$secret_dir/github-mcp.env"

if [ -z "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" ]; then
    printf 'GITHUB_PERSONAL_ACCESS_TOKEN: ' >&2
    stty -echo 2>/dev/null || true
    IFS= read -r GITHUB_PERSONAL_ACCESS_TOKEN
    stty echo 2>/dev/null || true
    printf '\n' >&2
fi

case "$GITHUB_PERSONAL_ACCESS_TOKEN" in
    github_pat_*|ghp_*) : ;;
    *)
        printf 'error: GITHUB_PERSONAL_ACCESS_TOKEN does not look like a GitHub token\n' >&2
        exit 1
        ;;
esac

mkdir -p "$secret_dir"
chmod 700 "$secret_dir"
umask 077
printf 'GITHUB_PERSONAL_ACCESS_TOKEN=%s\nGITHUB_READ_ONLY=1\n' \
    "$GITHUB_PERSONAL_ACCESS_TOKEN" > "$secret_file"
chmod 600 "$secret_file"

printf 'wrote %s (mode 600)\n' "$secret_file" >&2
printf 'Ensure .kilo/kilo.jsonc references it via HTP_GITHUB_MCP_ENV_FILE.\n' >&2