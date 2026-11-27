#!/bin/bash
# One-line install for a new Mac. Paste this in Terminal:
#   curl -fsSL https://raw.githubusercontent.com/euleomendes/edit-labs/main/install.sh | bash
# Downloads Edit Labs into ~/Edit Labs, installs what it needs (Python + ffmpeg, the video effects),
# adds "Edit Labs" to Applications and opens the editor. Safe to run again: it updates the code
# and keeps your projects. Nothing comes from a zip, so macOS does not show the "malware" warning.
# The whole script is inside main() so that `curl | bash` only runs it once it is fully downloaded.

main() {
  set -e
  REPO="euleomendes/edit-labs"
  DIR="${EDIT_LABS_DIR:-$HOME/Edit Labs}"
  SRC="${EDIT_LABS_SRC:-https://github.com/$REPO/archive/refs/heads/main.tar.gz}"

  echo "== Instalando o Edit Labs em: $DIR =="
  [ "$(uname -s)" = Darwin ] || { echo "O Edit Labs é feito para Mac."; exit 1; }

  echo "Baixando o editor..."
  tmp="$(mktemp -d)"
  curl -fsSL "$SRC" | tar -xz -C "$tmp"
  mkdir -p "$DIR"
  # Copies the new code over the old one; your videos (work/) and the installed tools stay.
  rsync -a "$tmp"/*/ "$DIR"/
  rm -rf "$tmp"
  xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true
  chmod +x "$DIR"/*.command "$DIR"/*.sh 2>/dev/null || true

  bash "$DIR/setup.sh" </dev/null

  make_app
  make_skill
  echo
  echo "Pronto! Abra o \"Edit Labs\" na pasta Aplicativos (ou pelo Launchpad/Spotlight)."
  echo "Abrindo o editor agora..."
  [ -n "$EDIT_LABS_NO_OPEN" ] || open "$APP"
}

# A small app that opens the editor, with the Edit Labs logo as its icon.
make_app() {
  if [ -w /Applications ]; then APPDIR=/Applications; else APPDIR="$HOME/Applications"; mkdir -p "$APPDIR"; fi
  APPDIR="${EDIT_LABS_APPDIR:-$APPDIR}"
  APP="$APPDIR/Edit Labs.app"
  rm -rf "$APP"
  osacompile -o "$APP" -e "do shell script \"open -a Terminal \" & quoted form of \"$DIR/Abrir Editor.command\"" >/dev/null 2>&1
  if [ -f "$DIR/assets/app-icon.icns" ]; then
    cp "$DIR/assets/app-icon.icns" "$APP/Contents/Resources/applet.icns"
    codesign --force -s - "$APP" >/dev/null 2>&1 || true
    touch "$APP"
  fi
  echo "App criado: $APP"
}

# Teaches Claude Code on this Mac to open the editor: just say "abrir Edit Labs".
make_skill() {
  SK="$HOME/.claude/skills/edit-labs"
  mkdir -p "$SK"
  sed "s|@DIR@|$DIR|g" "$DIR/tools/claude-skill.md" > "$SK/SKILL.md"
  echo "Claude configurado: diga \"abrir Edit Labs\" no Claude para abrir o editor."
}

main "$@"
