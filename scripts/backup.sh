#!/usr/bin/env bash
# Nightly SQLite backup. Drops in via cron.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${REPO_ROOT}/app_data/articles.db"
DEST="${REPO_ROOT}/backups"

mkdir -p "${DEST}"

if [[ ! -f "${DB}" ]]; then
  echo "no DB at ${DB}; nothing to back up" >&2
  exit 0
fi

stamp="$(date +%F)"
target="${DEST}/articles-${stamp}.db"
sqlite3 "${DB}" ".backup '${target}'" 2>/dev/null || cp "${DB}" "${target}"

# retain 14 days
find "${DEST}" -maxdepth 1 -name 'articles-*.db' -mtime +14 -delete
echo "backup written: ${target}"
