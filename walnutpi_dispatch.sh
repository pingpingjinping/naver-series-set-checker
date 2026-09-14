#!/usr/bin/env bash
set -euo pipefail

REPO="pingpingjinping/naver-series-set-checker"
REF="main"
TOKEN_FILE="${GITHUB_TOKEN_FILE:-$HOME/.config/naver-series-set-checker/github_token}"
PAYLOAD="{\"ref\":\"$REF\"}"

case "${1:-}" in
  series) WORKFLOW="series.yml" ;;
  series-test)
    WORKFLOW="series.yml"
    PAYLOAD="{\"ref\":\"$REF\",\"inputs\":{\"test_ping\":\"true\"}}"
    ;;
  price) WORKFLOW="price.yml" ;;
  ridi) WORKFLOW="ridi.yml" ;;
  aladin) WORKFLOW="aladin.yml" ;;
  *)
    echo "usage: $0 {series|series-test|price|ridi|aladin}" >&2
    exit 2
    ;;
esac

if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  TOKEN="$GITHUB_TOKEN"
elif [[ -r "$TOKEN_FILE" ]]; then
  TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
else
  echo "GitHub token not found: $TOKEN_FILE" >&2
  exit 1
fi

TMP="/tmp/walnutpi-dispatch-response.$$"
HTTP_CODE="$(curl -sS -o "$TMP" -w '%{http_code}' \
  -X POST \
  -H 'Accept: application/vnd.github+json' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'X-GitHub-Api-Version: 2022-11-28' \
  "https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/dispatches" \
  -d "$PAYLOAD")"

if [[ "$HTTP_CODE" != "204" ]]; then
  echo "dispatch failed ($HTTP_CODE):" >&2
  cat "$TMP" >&2 || true
  rm -f "$TMP"
  exit 1
fi

rm -f "$TMP"
echo "$(date '+%F %T') dispatched $WORKFLOW (${1})"
