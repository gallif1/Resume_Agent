#!/bin/sh
# Seed config files onto a fresh persistent volume without overwriting user data.
set -eu

SEED_DIR="/app/ai-job-agent/seed-data"
DATA_DIR="/app/ai-job-agent/data"

mkdir -p "$DATA_DIR"

if [ -d "$SEED_DIR" ]; then
  for src in "$SEED_DIR"/*; do
    [ -e "$src" ] || continue
    name=$(basename "$src")
    dest="$DATA_DIR/$name"
    if [ ! -e "$dest" ]; then
      cp -a "$src" "$dest"
    fi
  done
fi

exec "$@"
