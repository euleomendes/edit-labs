#!/bin/bash
# Double-click once to connect the editor to Claude with your Claude account (Pro or Max) — no API key.
# Installs Claude Code if it is missing, then signs you in through the browser.
cd "$(dirname "$0")"
export PATH="$PWD/.runtime/env/bin:$HOME/.local/bin:$HOME/.claude/local:$HOME/.edite-reels/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN

echo "== Conectar o Claude ao Edit Labs =="
if ! command -v claude >/dev/null 2>&1; then
  echo "Instalando o Claude Code (instalador oficial da Anthropic)..."
  curl -fsSL https://claude.ai/install.sh | bash
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "Não consegui instalar o Claude Code. Veja https://code.claude.com/docs e tente de novo."; read -n 1 -s; exit 1
fi

if claude auth status 2>/dev/null | grep -q '"loggedIn": true'; then
  echo "Você já está conectado."
else
  echo "Vai abrir o navegador: entre com a sua conta Claude e autorize."
  claude auth login --claudeai
fi

echo
echo "Pronto. Volte ao editor e clique em “Verificar de novo”. Pode fechar esta janela."
