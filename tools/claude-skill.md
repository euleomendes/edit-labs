---
name: edit-labs
description: Abrir, iniciar, atualizar ou reinstalar o editor de vídeo Edit Labs neste Mac. Use quando a pessoa disser algo como "abrir Edit Labs", "abre o editor", "abrir o editor de reels", "open Edit Labs" ou "atualizar o Edit Labs".
---

# Edit Labs

O Edit Labs está instalado em: `@DIR@`

## Abrir o editor
1. Veja se já está rodando: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8777/api/status`
2. Se respondeu 200, só abra o navegador: `open http://localhost:8777/visual-builder.html`
3. Se não, abra o editor numa janela do Terminal (ela mantém o editor ligado e abre o navegador):
   `open -a Terminal "@DIR@/Abrir Editor.command"`
   Não rode o server.py pelo seu próprio terminal: ele desligaria quando a conversa terminasse.
4. Diga à pessoa, em uma frase, que o editor está abrindo e que a janela do Terminal precisa ficar aberta enquanto ela edita.

## Atualizar ou reinstalar
Rode `curl -fsSL https://raw.githubusercontent.com/euleomendes/edit-labs/main/install.sh | bash`
(atualiza o código e mantém os projetos). Use isso também se a pasta acima não existir mais.
