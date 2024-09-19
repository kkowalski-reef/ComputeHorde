#!/bin/bash
set -eux -o pipefail

IMAGE_NAME="kkowalskireef/compute-horde-validator-nginx:v0-latest"
docker build -t $IMAGE_NAME .
