#!/usr/bin/env bash
# Build whisper.cpp and llama.cpp natively with Metal at pinned versions.
# Shallow clones + only the targets EYWA needs, to save disk space.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TP="$ROOT/third_party"
WHISPER_TAG="v1.9.5"
LLAMA_TAG="b12066"
JOBS="$(sysctl -n hw.ncpu)"

mkdir -p "$TP"

clone() { # repo tag dir
  if [ ! -d "$TP/$3/.git" ]; then
    git clone --depth 1 --branch "$2" "https://github.com/$1.git" "$TP/$3"
  fi
  (cd "$TP/$3" && echo "$3 @ $(git describe --tags --always) $(git rev-parse HEAD)")
}

clone ggml-org/whisper.cpp "$WHISPER_TAG" whisper.cpp
clone ggml-org/llama.cpp   "$LLAMA_TAG"   llama.cpp

echo "== building whisper.cpp (Metal)"
cmake -S "$TP/whisper.cpp" -B "$TP/whisper.cpp/build" \
  -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DWHISPER_BUILD_TESTS=OFF -DWHISPER_SDL2=OFF
cmake --build "$TP/whisper.cpp/build" -j "$JOBS" --target whisper-cli whisper-bench whisper-server

echo "== building llama.cpp (Metal)"
cmake -S "$TP/llama.cpp" -B "$TP/llama.cpp/build" \
  -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_CURL=OFF
cmake --build "$TP/llama.cpp/build" -j "$JOBS" --target llama-cli llama-bench llama-server

echo "== done"
ls -la "$TP/whisper.cpp/build/bin" "$TP/llama.cpp/build/bin"
