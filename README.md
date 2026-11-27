# Edit Labs

*Seu laboratório de edição.*

Editor de Reels no navegador: corte pelo texto, looks, legendas, sons, efeitos e exportação em MP4 — com transcrição automática (Whisper) e edição pelo Claude.

## Instalar num computador novo (Mac)

Abra o **Terminal** (Spotlight → "Terminal"), cole a linha abaixo e aperte Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/euleomendes/edit-labs/main/install.sh | bash
```

Pronto. Ele baixa o editor para a pasta **Edit Labs** (na sua pasta de usuário), instala o que falta (Python, ffmpeg e os 51 efeitos de vídeo — sem Homebrew e sem senha), coloca o app **Edit Labs** em Aplicativos e abre o editor. Leva de 3 a 8 minutos na primeira vez. Depois, é só abrir o **Edit Labs** pelo Launchpad ou Spotlight (deixe a janela do Terminal aberta enquanto edita).

Para atualizar, rode a mesma linha de novo: o código é atualizado e os seus projetos ficam.

> **Pedindo ao Claude Code para instalar?** Basta passar o link do repositório e pedir para instalar: a instalação é exatamente o comando acima.

Precisa de: macOS e Google Chrome. A transcrição baixa o modelo de fala (80–250 MB) na primeira vez.

## Como usar

- **Import video** (topo direito) → escolha o idioma → **Transcribe**. Pausas, palavras de enchimento e repetições são cortadas sozinhas.
- **Cut**: clique numa palavra para ir até ela; passe o mouse para cortar, dividir ou anotar.
- **Visuals**: hooks de texto editáveis (8 modelos), legendas que você arrasta no preview, Cover a face (detecta e segue o rosto), Lookbook, Sandbox e **Effects library** (51 efeitos com prévia).
- **Sound**: passe o mouse numa palavra → **♪ Add a sound** — bibliotecas Real SFX (936), earshot (252) e Openverse (busca).
- **Finish**: exporta MP4 (com os efeitos de vídeo) ou um rascunho para o CapCut; carrossel, story e trial reels.
- **Idioma** (🌐 no topo direito): Português, English ou Español. O botão ◐ ao lado troca entre claro e escuro.
- **Claude** (topo direito): usa a **sua conta Claude** (Pro ou Max) pelo Claude Code instalado no computador — sem chave de API. Na primeira vez, clique em **Instalar e entrar** (ou dê dois cliques em `Conectar Claude.command`), entre na conta no navegador e clique em **Verificar de novo**. Vale para o **✦ Edit with Claude**, notas e Sandbox.

## Efeitos e recorte do apresentador

- **119 efeitos de vídeo**: os 67 do Edit Labs (`effects/labs_fx.py`, etiqueta azul **EDIT LABS** na biblioteca) e os 52 do [claude-video-effects](https://github.com/Vibha-Ramprakash/claude-video-effects) (MIT). Todos rodam pelo mesmo motor.
- **Recorte do apresentador**: os efeitos que ficam atrás de você usam o [Robust Video Matting](https://github.com/PeterL1n/RobustVideoMatting) (licença GPL-3.0), que recorta cabelo, mãos e microfone com precisão e não treme entre os quadros. A instalação baixa o modelo (15 MB) para `effects/models/` e instala o `onnxruntime`. Se ele faltar, os efeitos usam o recorte básico do MediaPipe. Para forçar o básico: `EDIT_LABS_CUTOUT=basic`.

## Arquivos

| Arquivo | O que é |
|---|---|
| `visual-builder.html` | o editor |
| `server.py` | servidor local (só no seu computador) que serve o editor e roda os efeitos |
| `Abrir Editor.command` | abre o editor |
| `Conectar Claude.command` | instala o Claude Code e entra na sua conta Claude (uma vez) |
| `install.sh` | instalação de uma linha (baixa do GitHub, instala tudo e cria o app) |
| `Instalar.command` / `setup.sh` | instala as dependências numa pasta já baixada |
| `sounds/` | Real SFX (CC0) e earshot (MIT), com as licenças |
| `effects-previews/` | prévias animadas dos 51 efeitos |
| `i18n.js` | a interface em inglês e espanhol (gerado — não edite à mão) |
| `tools/i18n.tsv` | as traduções: português, inglês e espanhol, uma por linha. Depois de editar, rode `python3 tools/build_i18n.py` |
| `assets/logo-edit-labs.png` | o logo |
| `tools/make_fx_previews.py` | regenera as prévias dos efeitos |
| `effects/labs_fx.py` | os 67 efeitos do Edit Labs e o recorte avançado do apresentador |

## Créditos

- Efeitos de vídeo: [claude-video-effects](https://github.com/Vibha-Ramprakash/claude-video-effects) por @vibha.in.progress (MIT), baixado pelo `setup.sh`. As prévias em `effects-previews/` foram renderizadas a partir da imagem de exemplo desse projeto.
- Sons: [Real SFX / uisfx](https://github.com/Clipwing/skills) (CC0) e [earshot-sfx-lite](https://github.com/Dodai0419/earshot-sfx-lite) (MIT); busca via [Openverse](https://openverse.org).
- Transcrição: Whisper via [transformers.js](https://github.com/huggingface/transformers.js); detecção de rosto: [MediaPipe](https://ai.google.dev/edge/mediapipe).
