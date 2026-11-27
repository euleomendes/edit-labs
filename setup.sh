#!/bin/bash
# One-time setup on a new computer: installs what the editor and the video effects need.
# Safe to run again. On a Mac without Python 3.10+ or ffmpeg it installs both inside this folder
# (.runtime/), so no Homebrew, Xcode or admin password is needed.
set -e
cd "$(dirname "$0")"
RT="$PWD/.runtime"
export PATH="$RT/env/bin:$HOME/.edite-reels/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "== Instalação do Edit Labs =="

# A usable Python is 3.10+ and not Apple's /usr/bin stub (which asks to install Xcode tools).
good_python() {
  for v in python3.13 python3.12 python3.11 python3.10 python3; do
    p="$(command -v "$v" 2>/dev/null)" || continue
    [ "$p" = /usr/bin/python3 ] && continue
    "$p" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null && return 0
  done
  return 1
}

if ! good_python || ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "Instalando Python e ffmpeg (só na primeira vez, 2–5 minutos)..."
  if [ "$(uname -m)" = arm64 ]; then plat=osx-arm64; else plat=osx-64; fi
  mkdir -p "$RT"
  if [ ! -x "$RT/bin/micromamba" ]; then
    curl -fsSL "https://micro.mamba.pm/api/micromamba/$plat/latest" | tar -xj -C "$RT" bin/micromamba
  fi
  MAMBA_ROOT_PREFIX="$RT/mamba" "$RT/bin/micromamba" create -y -q -p "$RT/env" -c conda-forge python=3.12 ffmpeg >/dev/null
  rm -rf "$RT/mamba/pkgs"
fi

mkdir -p libraries work
if [ ! -d libraries/claude-video-effects ]; then
  echo "Baixando os 51 efeitos de vídeo (github.com/Vibha-Ramprakash/claude-video-effects)..."
  curl -fsSL https://github.com/Vibha-Ramprakash/claude-video-effects/archive/refs/heads/main.tar.gz | tar -xz -C libraries
  mv libraries/claude-video-effects-main libraries/claude-video-effects
fi

echo "Preparando os efeitos (na primeira vez leva 1–3 minutos)..."
bash libraries/claude-video-effects/scripts/setup.sh

# Sharper presenter cutout for the effects that sit behind you (Robust Video Matting, GPL-3.0).
# Optional: if it fails, the effects keep their built-in cutout.
echo "Instalando o recorte avançado do apresentador..."
mkdir -p effects/models
libraries/claude-video-effects/.venv/bin/python -m pip install -q onnxruntime || echo "Aviso: não instalei o onnxruntime; o recorte básico continua funcionando."
if [ ! -f effects/models/rvm_mobilenetv3_fp32.onnx ]; then
  curl -fsSL -o effects/models/rvm_mobilenetv3_fp32.onnx \
    https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx \
    || { rm -f effects/models/rvm_mobilenetv3_fp32.onnx; echo "Aviso: não baixei o modelo de recorte; o recorte básico continua funcionando."; }
fi

echo
echo "Instalação concluída."
