# Cyan — Bot de Briefing Copack

## Visão Geral

Cyan é o assistente Pré-Arte da Copack — treinado no processo de arte final. Atua no Discord automatizando o briefing entre Atendimento e Arte, garantindo que o setor de Arte receba tudo que precisa sem informação faltando e sem retrabalho.

---

## Stack Técnica

| Componente | Tecnologia |
|---|---|
| Bot | Python + discord.py >= 2.3 |
| LLM | OpenAI GPT-4o (gpt-4o) — multimodal |
| Transcrição de áudio | OpenAI Whisper-1 |
| Hospedagem | VPS existente |
| Logs | cyan.log + stdout |

---

## Canais Discord

| Canal | Função |
|---|---|
| `#geral` | Apresentação fixada do Cyan + chamados de arte finalista + relato de erros |
| `#briefing-do-pedido` | Atendimento envia materiais → `/briefing` gera o briefing estruturado |
| `#análise-de-arquivos` | Cyan analisa cada arquivo tecnicamente via `/analisar` |

---

## Variáveis de Ambiente

```
DISCORD_TOKEN=
OPENAI_API_KEY=
BRIEFING_CHANNEL_ID=1508845843954794626
ANALYSIS_CHANNEL_ID=1508845789483368508
GERAL_CHANNEL_ID=1508828656804561062
RAIZA_DISCORD_ID=1412142843522191423
```

---

## Estrutura do Projeto

```
cyan-bot/
  main.py                    — bot, eventos, slash commands
  config.py                  — variáveis de ambiente e limites operacionais
  handlers/
    briefing_handler.py      — lógica do canal #briefing-do-pedido + Views/Modais
    analysis_handler.py      — lógica do canal #análise-de-arquivos
  utils/
    openai_client.py         — cliente GPT-4o + system prompt completo
    file_processor.py        — processamento de imagens, PDFs, áudios, PSDs
    order_state.py           — estado do briefing ativo por canal (expira em 2h)
    zip_generator.py         — geração do pacote ZIP (pausado)
  requirements.txt
  .env
  .env.example
  restart.sh
```

---

## Fluxo de Uso

### #briefing-do-pedido
O canal funciona **um pedido por vez**:
1. Atendimento envia materiais (prints WhatsApp, imagens, PDFs, arquivos)
2. Usa `/briefing` — bot coleta até 100 mensagens recentes, monta payload multimodal
3. GPT-4o gera briefing parcial e faz perguntas objetivas
4. Atendimento responde as perguntas normalmente no chat
5. Usa `/finalizar` — gera briefing completo como **texto** no canal (sem ZIP)
6. Usa `/limpar` — apaga as mensagens e reseta para o próximo pedido

**Importante:** sem `/briefing` ativo, qualquer mensagem recebe aviso e é ignorada.
**Importante:** estados expiram automaticamente após 2h de inatividade.

### Botões após cada rodada de perguntas
Dois botões cinza aparecem discretamente após cada rodada de perguntas:

**🎨 Chamar Arte Finalista**
1. Abre modal com filtro de confirmação + campo de descrição do problema
2. Opcionalmente pode enviar imagem no canal após confirmar
3. Cyan posta no **#geral** mencionando `<@RAIZA_DISCORD_ID>` com:
   - Problema relatado pelo atendimento
   - Entendimento do Cyan sobre o pedido
   - Imagem (se enviada)

**✓ Arte sem alterações — confirmado com o cliente**
1. Posta confirmação no canal
2. Informa o GPT que não há mais perguntas pendentes
3. GPT gera briefing final e posta como texto

### #análise-de-arquivos
- **Automático:** qualquer arquivo enviado é analisado imediatamente
- **Manual:** `/analisar` processa arquivos das últimas 20 mensagens

### #geral
- Mensagem de apresentação fixada automaticamente no startup
- Destino dos chamados de arte finalista
- Canal para relato de erros

---

## Comandos Slash

| Comando | Onde usar | Função |
|---|---|---|
| `/briefing` | #briefing-do-pedido | Inicia análise dos materiais e questionário |
| `/finalizar` | #briefing-do-pedido | Encerra questionário e posta briefing completo como texto |
| `/limpar` | #briefing-do-pedido | Apaga mensagens e reseta para novo pedido |
| `/analisar` | #análise-de-arquivos | Analisa arquivos manualmente |
| `/ajuda` | qualquer canal | Responde dúvidas sobre arte final, arquivos e fluxo |

---

## O que está ATIVO vs PAUSADO

### Ativo
- Coleta e análise de materiais (imagens, PDFs, PSDs, textos, áudios)
- Transcrição de áudio via Whisper com prévia no Discord
- Questionário multi-turno com GPT-4o
- Briefing completo entregue como texto no canal
- Botão 🎨 Chamar Arte Finalista
- Análise técnica manual no #análise-de-arquivos

### Pausado (código existe mas desativado)
- ZIP com pacote de arquivos (`zip_generator.py` — pausado em `_deliver_final`)
- Reenvio automático de arquivos para #análise-de-arquivos após `/briefing`
- Classificação automática de arquivos por tipo (sem ZIP, não é necessária)

---

## Limites Operacionais

- Arquivos > 10 MB: orientar envio via Google Drive
- PDFs: converte até 3 páginas em imagem (pdf2image + poppler)
- Fallback PDF: extração de texto via pypdf
- Rate limit OpenAI: 30.000 tokens/min (Tier 1) — limita análise de muitos arquivos simultâneos
- Estado ativo por canal expira em 2 horas sem atividade

## Classificação de arquivos (para ZIP quando reativar)

Lógica em `zip_generator._classify_folder`:
- `.ai`, `.eps`, `.cdr`, `.svg` → Logo (fallback por extensão)
- `.psd`, `.psb`, `.tif` → Producao
- `.pdf` com "aprovacao/modelo" → Referencia/Modelos de Aprovacao
- `.mp3`, `.wav` etc → Audios
- Imagens com nome contendo logo/marca → Logo
- Demais → Outros

---

## Contexto do Problema

- O atendimento recebe pedidos de clientes por múltiplos canais (WhatsApp via Kommo)
- O processo de impressão da Copack é Offset (CMYK obrigatório)
- Sistema de pedidos: Omie (ERP) — número do pedido é a chave de identificação
- Gestão de projetos: Trello
- Produto principal: embalagens personalizadas para alimentos

---

## Apresentação Automática no #geral

Na inicialização, o Cyan verifica se já existe uma mensagem fixada dele no #geral.
Se não houver, posta a apresentação e tenta fixar automaticamente.
**Permissão necessária:** Gerenciar Mensagens (para fixar).

A identidade na mensagem: **"Cyan 🤓 — assistente Pré-Arte da Copack, treinado no processo de arte final."**

---

## Reiniciar o bot

```bash
cd /root/cyan-bot
bash restart.sh
```

Logs: `tail -f cyan.log`

---

## Status

- 2026-05-27: bot reconstruído do zero
- 2026-05-28: canal #geral integrado, apresentação automática, `/ajuda`
- 2026-05-29: chamado para arte finalista (botão 🎨, modal, menção no #geral)
- 2026-05-29: classificação por extensão (sem GPT para tipos óbvios)
- 2026-05-29: ZIP pausado — entrega só como texto no Discord
- 2026-05-29: reenvio automático para #análise-de-arquivos pausado
- 2026-05-29: expiração automática de estado após 2h
- 2026-05-29: aviso quando mensagem chega sem /briefing ativo
- 2026-05-29: RAIZA_DISCORD_ID=1412142843522191423 configurado no .env
- 2026-05-29: botão ✓ Arte sem alterações — confirmado com o cliente adicionado
- 2026-05-29: /limpar apaga em loop (sem limite fixo) + zera memória completamente
- 2026-05-29: fix múltiplos processos no restart.sh (matava só pelo PID, deixava processos antigos rodando)
- 2026-05-29: fix defer_update() → defer() (discord.py 2.7.1 não tem defer_update)
- 2026-05-29: fix PartialMessageable → usar interaction.client.get_channel(id) nos botões

## Pendente
- Permissão "Gerenciar Mensagens" no Discord para o Cyan fixar mensagens automaticamente
