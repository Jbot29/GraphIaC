#!/usr/bin/env bash
# Build the image and push it to the ECR repository webapp.giac declares.
#
#   ./push.sh [aws-profile]
#
# The repository has to exist first — run webapp.giac once and it will,
# along with the load balancer and the database. Then push, then run again:
# the service is BLOCKED until there is an image to pull anyway.
set -euo pipefail
cd "$(dirname "$0")"

PROFILE="${1:-graphiac}"
REGION="us-east-2"
REPO="yourco-web"          # matches the EcrRepository label in webapp.giac
TAG="${TAG:-latest}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text --profile "$PROFILE")"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:${TAG}"

echo "==> logging in to ${REGISTRY}"
aws ecr get-login-password --region "$REGION" --profile "$PROFILE" \
  | docker login --username AWS --password-stdin "$REGISTRY"

# --platform matters: Fargate is amd64, and an arm64 image from an Apple
# Silicon laptop fails at startup with an error ECS does not explain.
echo "==> building ${IMAGE}"
docker build --platform linux/amd64 -t "$IMAGE" app/

echo "==> pushing"
docker push "$IMAGE"

echo
echo "pushed ${IMAGE}"
echo "now:  python -m GraphIaC ${PROFILE} --infra_file webapp.giac run"
