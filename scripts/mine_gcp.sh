#!/bin/bash
# Runs on a GCP VM: clone, build image, mine each new library. Usage: mine_gcp.sh repos.txt   (lines: name owner/repo [pkg])
cd ~/rb || exit 1
mkdir -p repos img
doit() {
  name=$1; slug=$2; pkg=${3:-$1}
  [ -d repos/$name ] || git clone -q https://github.com/$slug repos/$name || { echo "clone failed $name"; return; }
  printf 'FROM python:3.11-slim\nRUN pip install -q --no-cache-dir pytest hypothesis freezegun || true\nRUN pip install -q --no-cache-dir %s && pip uninstall -y -q %s || true\n' "$pkg" "$pkg" > img/Dockerfile.$name
  docker build -q -t rb-$name -f img/Dockerfile.$name img > build-$name.log 2>&1
  python3 mine_realbugs.py repos $name rb-$name mined-$name.jsonl 2000 > mine-$name.log 2>&1
  echo "done $name $(wc -l < mined-$name.jsonl)"
}
export -f doit 2>/dev/null
while read name slug pkg; do
  [ -z "$name" ] && continue
  doit $name $slug $pkg &
  while [ "$(jobs -r | wc -l)" -ge 4 ]; do sleep 5; done
done < "$1"
wait
echo ALLDONE > mine.done
