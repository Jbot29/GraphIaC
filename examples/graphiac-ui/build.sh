#!/usr/bin/env bash
# The "grab" step: pull GraphIaC (and its deps) out of PyPI as Lambda-
# compatible wheels, add the thin handler, zip. Run before an infra run;
# after upgrading GraphIaC, rebuild + run to redeploy the hosted UI.
#
#   ./build.sh              # latest published GraphIaC
#   ./build.sh 'GraphIaC==0.0.42'   # pin a version
set -euo pipefail
cd "$(dirname "$0")"

SPEC="${1:-GraphIaC}"
OUT="$PWD/deployment.zip"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Lambda-compatible wheels (pydantic ships compiled code — the platform/
# implementation pins make pip fetch linux wheels, not your Mac's)
python3 -m pip install -q "$SPEC" --target "$STAGE" \
    --platform manylinux2014_x86_64 --implementation cp \
    --python-version 3.13 --only-binary=:all:

cp lambda/handler.py lambda/miniui.py "$STAGE"/

rm -f "$OUT"
(cd "$STAGE" && zip -qr "$OUT" . -x "*__pycache__*" -x "*.pyc" -x "*.dist-info/RECORD")

echo "built ${OUT#"$PWD"/} ($(du -h "$OUT" | cut -f1)) from $SPEC"
