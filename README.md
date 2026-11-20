# Edit Labs

*Seu laboratório de edição.*

Editor de Reels no navegador: corte pelo texto, looks, legendas, sons, efeitos e exportação em MP4 — com transcrição automática (Whisper) e edição pelo Claude.

## Instalar num computador novo (Mac)

1. Copie o **`visual-builder.zip`** para o computador (pendrive, AirDrop, Google Drive…) e dê dois cliques para descompactar. Mova a pasta **visual-builder** para onde quiser (ex.: Documentos).
2. Dê dois cliques em **`Instalar.command`** (só na primeira vez). Ele instala o ffmpeg (se faltar) e os 51 efeitos de vídeo.
   - Se o Mac bloquear: clique com o botão direito → **Abrir**.
3. Dê dois cliques em **`Abrir Editor.command`** sempre que quiser usar. Deixe a janela do Terminal aberta enquanto edita.

Precisa de: macOS, Python 3, Google Chrome. A transcrição baixa o modelo de fala (80–250 MB) na primeira vez.

## Como usar

- **Import video** (topo direito) → escolha o idioma → **Transcribe**. Pausas, palavras de enchimento e repetições são cortadas sozinhas.
- **Cut**: clique numa palavra para ir até ela; passe o mouse para cortar, dividir ou anotar.
- **Visuals**: hooks de texto editáveis (8 modelos), legendas que você arrasta no preview, Cover a face (detecta e segue o rosto), Lookbook, Sandbox e **Effects library** (51 efeitos com prévia).
- **Sound**: passe o mouse numa palavra → **♪ Add a sound** — bibliotecas Real SFX (936), earshot (252) e Openverse (busca).
- **Finish**: exporta MP4 (com os efeitos de vídeo) ou um rascunho para o CapCut; carrossel, story e trial reels.
- **Claude** (topo direito): cole sua chave de API de console.anthropic.com para o **✦ Edit with Claude**, notas e Sandbox. A chave fica só no seu navegador.

## Arquivos

| Arquivo | O que é |
|---|---|
| `visual-builder.html` | o editor |
| `server.py` | servidor local (só no seu computador) que serve o editor e roda os efeitos |
| `Abrir Editor.command` | abre o editor |
| `Instalar.command` / `setup.sh` | instalação num computador novo |
| `sounds/` | Real SFX (CC0) e earshot (MIT), com as licenças |
| `effects-previews/` | prévias animadas dos 51 efeitos |
| `tools/make_fx_previews.py` | regenera as prévias dos efeitos |

## Créditos

- Efeitos de vídeo: [claude-video-effects](https://github.com/Vibha-Ramprakash/claude-video-effects) por @vibha.in.progress (MIT), baixado pelo `setup.sh`. As prévias em `effects-previews/` foram renderizadas a partir da imagem de exemplo desse projeto.
- Sons: [Real SFX / uisfx](https://github.com/Clipwing/skills) (CC0) e [earshot-sfx-lite](https://github.com/Dodai0419/earshot-sfx-lite) (MIT); busca via [Openverse](https://openverse.org).
- Transcrição: Whisper via [transformers.js](https://github.com/huggingface/transformers.js); detecção de rosto: [MediaPipe](https://ai.google.dev/edge/mediapipe).
