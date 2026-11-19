#!/bin/sh
# Build the four images on the Docker host (the stack uses them as local images).
#   SRC=/path/to/sources ./build-images.sh     (default: the folders next to wombat/)
# After building, redeploy the stack (Portainer: Stacks → family → Update) to use the new images.
set -e
SRC="${SRC:-$(cd "$(dirname "$0")/../.." && pwd)}"
build() { # image folder
  if [ ! -f "$SRC/$2/Dockerfile" ]; then echo "Missing $SRC/$2/Dockerfile" >&2; exit 1; fi
  if [ -d "$SRC/$2/.git" ] && [ ! -f "$SRC/$2/backend/famkit/__init__.py" ]; then
    git -C "$SRC/$2" submodule update --init   # famkit, the shared code
  fi
  echo "== $1 ($SRC/$2)"
  docker build -t "$1:latest" "$SRC/$2"
}
build wombat wombat
build kooka "${KOOKA_DIR:-kooka}"
build wallaby "${WALLABY_DIR:-wallaby}"
build pelican "${PELICAN_DIR:-pelican}"
echo "Done. Images: wombat, kooka, wallaby, pelican (tag latest)."
