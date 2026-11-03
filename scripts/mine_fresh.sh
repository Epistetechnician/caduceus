#!/bin/bash
# Mine fix commits newer than SINCE from the already-cloned/built libraries into $OUT (one file per repo).
# Usage: SINCE=2025-07-01 OUT=dir mine_fresh.sh <repos_dir>
mkdir -p "$OUT"
for d in "$1"/*/; do
  n=$(basename "$d")
  docker image inspect rb-$(echo $n | tr A-Z a-z) >/dev/null 2>&1 || continue
  git -C "$d" fetch -q --all 2>/dev/null && git -C "$d" merge -q --ff-only FETCH_HEAD 2>/dev/null
  MINE_THREADS=2 python3 "$(dirname "$0")/mine_realbugs.py" "$1" "$n" rb-$(echo $n | tr A-Z a-z) "$OUT/fresh-$n.jsonl" 2000 > "$OUT/log-$n.txt" 2>&1
done
echo ALLDONE > "$OUT/done"
