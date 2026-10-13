#!/bin/sh
# Does the image the GPU Pod runs actually satisfy the pinned llama.cpp build?
#
# Run *inside* the container, with the staged runtime mounted read-only at /runtime:
#
#   docker run --rm --entrypoint sh \
#     -v "$STAGED:/runtime:ro" -v "$PWD/scripts/probe-runtime-in-image.sh:/probe.sh:ro" \
#     runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404 /probe.sh
#
# It exits non-zero if the image cannot satisfy the build. A missing libcuda.so.1 is *expected*
# without a GPU: that is the driver, and the NVIDIA runtime supplies it at real GPU start. Anything
# else missing is a real defect in the image, not a test artefact.
set -u
RUNTIME=${RUNTIME:-/runtime}
fail=0

bad() {
  echo "CANALLA_IMG_FAIL $1"
  fail=1
}

. /etc/os-release 2>/dev/null || true
echo "CANALLA_IMG_OS=${PRETTY_NAME:-unknown}"
echo "CANALLA_IMG_GLIBC=$(ldd --version 2>/dev/null | head -n1)"
echo "CANALLA_IMG_CUDA_DIRS=$(ls -d /usr/local/cuda*/ 2>/dev/null | tr '\n' ' ')"

# Without the binary ldd cannot report a missing library, so its silence would read as success.
for needed in llama-server libggml-cuda.so libcudart.so.12; do
  [ -e "$RUNTIME/$needed" ] || bad "the staged runtime has no $needed"
done

if [ -e /usr/lib/x86_64-linux-gnu/libgomp.so.1 ]; then
  echo "CANALLA_IMG_LIBGOMP_PRESENT $(wc -c < /usr/lib/x86_64-linux-gnu/libgomp.so.1 | tr -d ' ')"
else
  bad "libgomp.so.1 is absent from the image"
fi

SERVER_MISSING=$(LD_LIBRARY_PATH="$RUNTIME" ldd "$RUNTIME/llama-server" 2>&1 | awk '/not found/{print $1}' | tr '\n' ' ')
echo "CANALLA_IMG_LDD_SERVER=[${SERVER_MISSING}]"
[ -z "$SERVER_MISSING" ] || bad "llama-server misses: ${SERVER_MISSING}"

CUDA_MISSING=$(LD_LIBRARY_PATH="$RUNTIME" ldd "$RUNTIME/libggml-cuda.so" 2>&1 | awk '/not found/{print $1}' | tr '\n' ' ')
echo "CANALLA_IMG_LDD_CUDA=[${CUDA_MISSING}]"
for lib in $CUDA_MISSING; do
  [ "$lib" = "libcuda.so.1" ] || bad "libggml-cuda misses: ${lib}"
done

VERSION=$(LD_LIBRARY_PATH="$RUNTIME" "$RUNTIME/llama-server" --version 2>&1 | head -n1)
echo "CANALLA_IMG_VERSION=${VERSION}"
case "$VERSION" in
  *"build 11565"*) ;;
  *) bad "unexpected version: ${VERSION}" ;;
esac

HELP=$(LD_LIBRARY_PATH="$RUNTIME" "$RUNTIME/llama-server" --help 2>&1)
for flag in --model --alias --n-gpu-layers --ctx-size --parallel --flash-attn --jinja --host --port; do
  printf '%s' "$HELP" | grep -q -- "$flag" || bad "the help output lacks ${flag}"
done

if [ "$fail" -eq 0 ]; then
  echo CANALLA_IMG_OK
else
  echo CANALLA_IMG_FAILED
fi
echo CANALLA_IMG_DONE
exit "$fail"
