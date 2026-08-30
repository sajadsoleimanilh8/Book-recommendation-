#!/bin/sh
# Keeps semantic search current as enrichment produces new text.
#
#   sh scripts/keepup.sh <log-dir>
#
# Deliberately never refits the model. Refitting produces a new vector space
# (F-37), and a periodic job that refit would leave the column holding a
# different space every 20 minutes. New chunks are embedded with the existing
# model, which is what keeps them comparable. Refitting is a manual,
# deliberate act followed by `embed_pass --redo`.
#
# Runs from the project venv, not system python: that is where
# sentence-transformers lives, and the corpus is MiniLM (OI-9). On the global
# interpreter this would fail on the import rather than mis-embed — but
# failing every 20 minutes into a log nobody reads is its own kind of silent,
# so it checks up front and says so.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BE="$ROOT/backend"
SP="${1:-$ROOT/.keepup}"
PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "keepup: no venv at $ROOT/.venv — refusing to run on system python"; exit 1; }

# GPU, explicitly. A full-corpus embed on CPU took this machine to 100 C;
# on the RTX 5070 Ti the same work is ~124x faster and the CPU stays idle.
# `cuda` here is a promise that is checked — SentenceTransformerBackend
# refuses to start rather than fall back to CPU silently, because a silent
# fallback looks exactly like "the corpus got bigger".
EMBEDDING_DEVICE="${EMBEDDING_DEVICE:-cuda}"
export EMBEDDING_DEVICE

MAXRUN=$((10 * 3600))
started=$(date +%s)
mkdir -p "$SP"
cd "$BE" || exit 1

while true; do
  now=$(date +%s)
  [ $((now - started)) -gt $MAXRUN ] && { echo "KEEPUP_MAX_RUNTIME"; break; }
  if docker exec digikitab-postgres pg_isready -U digikitab >/dev/null 2>&1; then
    {
      echo "--- $(date +%H:%M) ---"
      POSTGRES_DB=digikitab "$PY" -m chunk_pass --limit 5000 2>&1 | tail -3
      POSTGRES_DB=digikitab "$PY" -m chunk_pass --origin description --limit 20000 2>&1 | tail -3
      POSTGRES_DB=digikitab "$PY" -m embed_pass --limit 200000 2>&1 | tail -4
    } >> "$SP/keepup.log" 2>&1
  else
    echo "$(date +%H:%M) postgres unreachable, skipping this round" >> "$SP/keepup.log"
  fi
  sleep 1200
done
