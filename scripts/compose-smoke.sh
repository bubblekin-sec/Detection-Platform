#!/usr/bin/env bash
# End-to-end check of the compose stack through nginx: build, start, probe, and recover from an API restart.
# Run from anywhere: bash scripts/compose-smoke.sh   (needs Docker and curl; leaves the stack running)
set -euo pipefail

cd "$(dirname "$0")/.."
base="http://127.0.0.1:${UI_PORT:-8080}"
failures=0

check() {  # check <description> <expected status> <path> [content-type prefix]
  local description=$1 expected=$2 path=$3 type=${4:-}
  local result status content_type
  result=$(curl -s -o /dev/null -w "%{http_code} %{content_type}" "$base$path" || true)
  status=${result%% *}
  content_type=${result#* }
  if [[ $status == "$expected" && $content_type == "$type"* ]]; then
    echo "ok    $description ($path -> $status $content_type)"
  else
    echo "FAIL  $description ($path -> $status $content_type, expected $expected ${type:-any})"
    failures=$((failures + 1))
  fi
}

wait_for() {  # wait_for <path> <expected status> <seconds>
  local deadline=$((SECONDS + $3))
  until [[ $(curl -s -o /dev/null -w "%{http_code}" "$base$1" || true) == "$2" ]]; do
    if ((SECONDS > deadline)); then
      echo "FAIL  $1 did not return $2 within $3 s"
      docker compose ps -a
      exit 1
    fi
    sleep 2
  done
}

echo "Building and starting db, migrate, api and ui..."
docker compose up -d --build
wait_for /api/health 200 180

check "API through nginx" 200 /api/health application/json
check "API readiness (database)" 200 /api/ready application/json
check "API data through nginx" 200 /api/rules/counts application/json
check "API 404 stays JSON" 404 /api/nope application/json
check "client route falls back to the app" 200 /articles/1 text/html
check "missing asset is a 404" 404 /assets/missing.js
check "nginx liveness" 200 /healthz text/plain

migrate_exit=$(docker compose ps -a --format '{{.ExitCode}}' migrate)
if [[ $migrate_exit == 0 ]]; then echo "ok    migrate exited 0"; else echo "FAIL  migrate exit code: $migrate_exit"; failures=$((failures + 1)); fi
if docker compose exec -T ui nginx -t >/dev/null 2>&1; then echo "ok    nginx -t"; else echo "FAIL  nginx -t"; failures=$((failures + 1)); fi
if docker compose ps --services --status running | grep -qx worker; then
  echo "FAIL  the worker is running without --profile worker"; failures=$((failures + 1))
else
  echo "ok    the worker is opt-in (not running)"
fi

location=$(curl -s -o /dev/null -w "%{redirect_url}" "$base/api/articles/" || true)
if [[ -z $location || $location == "$base/api/"* ]]; then
  echo "ok    upstream redirects stay on this origin (${location:-no redirect})"
else
  echo "FAIL  an upstream redirect points elsewhere: $location"; failures=$((failures + 1))
fi

echo "Recreating the API on a new address: nginx should follow it without a restart..."
api_ip() { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(docker compose ps -q api)"; }
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$(docker compose ps -q api)")
ui_started=$(docker inspect -f '{{.State.StartedAt}}' "$(docker compose ps -q ui)")
old_ip=$(api_ip)
docker compose rm -sf api >/dev/null 2>&1
check "API down gives 502" 502 /api/health
check "nginx still alive" 200 /healthz text/plain
placeholder=$(docker run -d --rm --network "$network" alpine:3 sleep 120)  # takes the freed address
docker compose up -d --no-deps api >/dev/null 2>&1
wait_for /api/health 200 90
new_ip=$(api_ip)
docker rm -f "$placeholder" >/dev/null
check "API back on $new_ip (was $old_ip)" 200 /api/health application/json
if [[ $new_ip == "$old_ip" ]]; then
  echo "FAIL  the API kept its address, so re-resolution was not exercised"; failures=$((failures + 1))
elif [[ $(docker inspect -f '{{.State.StartedAt}}' "$(docker compose ps -q ui)") != "$ui_started" ]]; then
  echo "FAIL  nginx was restarted"; failures=$((failures + 1))
else
  echo "ok    nginx followed the API to a new address without a restart"
fi

if ((failures)); then
  echo "$failures check(s) failed"
  exit 1
fi
echo "All checks passed. The stack is still running at $base (docker compose --profile worker down to stop it)."
