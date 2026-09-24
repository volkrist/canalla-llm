#!/usr/bin/env bash
# Stage what a Linux release build has to carry, before Tauri bundles it.
#
# The Tor daemon Canalla ships comes from the pinned expert bundle (version, URL and SHA256 in
# scripts/tor-runtime.json); the backend sidecar and the headless host loop are built from this tree.
# A missing piece stops the build here, with the name of what is missing, instead of producing a
# package that installs and then cannot serve anything.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"

python3 "$repo/scripts/fetch-tor-runtime.py" --platform linux-x86_64 --dest "$here/runtime/tor"

missing=0
for required in "$here/sidecar/alex-backend/alex-backend" "$here/sidecar/alex-host-loop"; do
  if [ ! -f "$required" ]; then
    echo "missing resource: $required" >&2
    missing=1
  fi
done
if [ "$missing" != "0" ]; then
  cat >&2 <<'MESSAGE'
Build the packaged backend with PyInstaller (apps/backend/alex-backend.spec) and the host loop with
`cargo build --release --bin alex-host-loop`, then copy them into:
  apps/desktop/src-tauri/sidecar/alex-backend/   (the whole onedir tree)
  apps/desktop/src-tauri/sidecar/alex-host-loop
MESSAGE
  exit 1
fi

# The bundle must not carry a backend built from an older source tree; that shipped once on Windows
# and the packaged backend answered /health with the previous version. A warning would be ignored.
if ! python3 "$repo/scripts/backend-sidecar-stamp.py" check \
  --backend "$repo/apps/backend" \
  --stamp "$here/sidecar/alex-backend/build-stamp.json"; then
  echo "BACKEND_SIDECAR_STALE: rebuild the packaged backend before bundling" >&2
  exit 1
fi

echo "staged the bundled Tor runtime, the backend sidecar and the host loop"
