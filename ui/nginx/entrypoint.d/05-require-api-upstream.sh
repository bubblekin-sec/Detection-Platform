#!/bin/sh
# Fail at startup, not on the first request, when the API origin is missing or malformed. The value is pasted into
# the nginx config, so characters nginx or its URL parser would read as syntax are refused too. Messages never echo
# the value, which could carry credentials.
set -eu

case "${API_UPSTREAM:-}" in
  *[\?\#\$\;\{\}\ \"\'\\]*)
    echo "API_UPSTREAM must be a plain http(s) origin such as http://api:8000 (no query, fragment, quotes or \$)." >&2
    exit 1
    ;;
  http://*/* | https://*/*)
    echo "API_UPSTREAM must be an origin with no path, such as http://api:8000." >&2
    exit 1
    ;;
  http://?* | https://?*) ;;
  *)
    echo "API_UPSTREAM is not set to an http(s) origin such as http://api:8000; nginx cannot proxy /api." >&2
    exit 1
    ;;
esac
