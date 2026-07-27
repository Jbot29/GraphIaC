#!/usr/bin/env bash
# Build the deployment package, fresh every time. Run before an infra run.
set -euo pipefail
cd "$(dirname "$0")"

OUT="$PWD/deployment.zip"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R lambda/. "$STAGE"

if [ -f lambda/requirements.txt ]; then
  python3 -m pip install -q -r lambda/requirements.txt -t "$STAGE"
fi

rm -f "$OUT"
(cd "$STAGE" && zip -q -r "$OUT" . -x "*__pycache__*" -x "*.pyc" -x "*.DS_Store")

echo "built ${OUT#"$PWD"/}:"
unzip -l "$OUT" | tail -n +2
