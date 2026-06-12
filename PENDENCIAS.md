# PENDÊNCIAS — leia antes de qualquer trabalho no Cyan

> **CY8 EXECUTADO (2026-06-12, Sonnet 4.6):** CY8.1–CY8.4 implementados e
> testados localmente. Bot reiniciado sem erros. Aguarda reteste de Raíza no
> #briefing-teste (CY8.5): enviar pedido com cor prateada + checklist de
> box/selos/EAN e verificar: (1) bloco ⚠️ LIMITAÇÕES DE IMPRESSÃO no briefing;
> (2) botões ✅/❌ item a item; (3) confirmação final com "Não" → modal →
> observação no briefing + chamado no #geral.

> **PLANO DE EXECUÇÃO PRONTO (2026-06-12, Fable 5):** os 3 problemas abaixo
> viraram as etapas **CY7.1–CY7.5 no `/root/PLANO.md`** (seção "CY7 — ANÁLISE
> TÉCNICA DE ARQUIVO NO PIPELINE V2", Fase H da ordem global). O executor
> (Sonnet) deve seguir o PLANO.md — este arquivo é o diagnóstico de origem.
> Restrição de Raíza: sem custos extras (zero instalações, zero chamadas GPT
> novas — a análise pega carona na chamada de extração existente).
> Regra nova de negócio (Raíza, 2026-06-12): arte em leque/bitmap = só
> referência; arte final recriada semelhante com auxílio de IA; logo PNG
> transparente de boa qualidade = produção.

> **Contexto:** Pipeline v2 (CY6.1–CY6.5) implementado e testado no canal
> `#briefing-teste` em 2026-06-12. Três problemas identificados no teste.
> A faxina do v1 (CY6.6) está **bloqueada** até estes problemas serem
> resolvidos no v2 e validados por Raíza (CY7.5).

---

## PROBLEMA 1 — Análise técnica de arquivo ausente no v2

**O que o v1 fazia:** `analyze_file` (handlers/analysis_handler.py) analisava
cada arquivo tecnicamente — resolução, modo de cor, formato, flags 🔴🟡✅⚠️
("arquivo em baixa resolução", "modo RGB, converter para CMYK", etc.).

**O que o v2 faz:** o schema `Arquivo` tem o campo `status_tecnico` (string),
mas o prompt de extração (`_EXTRACTION_SYSTEM` em handlers/briefing_v2.py)
não instrui o modelo a preenchê-lo com análise técnica. Resultado: o campo
fica sempre vazio e o render exibe `—` para todos os arquivos.

**O file_processor já faz metade do trabalho:** ao processar uma imagem,
ele tem dimensões, modo de cor e tamanho em memória (logado internamente
em `cyan.file_processor`). Esse dado não é repassado ao GPT nem ao usuário.

**O que precisa ser resolvido:**
- Definir onde a análise técnica entra no pipeline v2 (no prompt de extração?
  em uma etapa separada após a extração? no file_processor, que já tem os dados?)
- Garantir que `status_tecnico` chegue preenchido ao render com informação útil
  (ex: "PNG 1536×1024, sem fundo — ✅ utilizável como produção")
- O `analysis_handler.py` (com o `/analisar`) continua ativo e saudável —
  pode ser reaproveitado como referência ou chamado internamente

**Arquivos relevantes:**
- `handlers/briefing_v2.py` — prompts `_EXTRACTION_SYSTEM` e `_UPDATE_SYSTEM`
- `utils/briefing_schema.py` — dataclass `Arquivo` (campo `status_tecnico`)
- `utils/render.py` — onde `status_tecnico` é exibido
- `utils/file_processor.py` — já captura dimensões/modo; não repassa
- `handlers/analysis_handler.py` — análise técnica do v1 (referência)

---

## PROBLEMA 2 — Leque bitmap e logo PNG sem identificação confiável

### 2a — Leque bitmap

**Situação:** cliente enviou uma imagem com a arte completa da embalagem, mas
em formato de **leque** (fan-shaped) e em **bitmap** — não é editável, não
serve como produção, deve ser tratada como referência apenas.

**O que o v2 faz:** o prompt de extração tem a regra
`LEQUE → não aceitar, solicitar versão plana`, mas:
- Depende do GPT reconhecer visualmente que a imagem é um leque — sem garantia
- Não há lógica de código detectando isso
- O `status_tecnico` não registra "arquivo em leque bitmap — não editável"
- A regra de `classificacao.py` não tem conhecimento de leque

**O que precisa:** identificação robusta de leque — seja no prompt de extração
(instrução mais explícita com consequência clara: `classe = "referencia"` +
`status_tecnico = "Arte em formato leque — não editável. Usar como referência visual."`),
seja em uma verificação dedicada.

### 2b — Logo PNG com nome genérico (UUID)

**Situação:** cliente enviou um logo em PNG com fundo transparente e boa
qualidade, mas o nome do arquivo era um UUID gerado pelo iPhone
(`DAF3CEBF-22C9-4229-B53C-F0DD66479749_-_Mauricio_Rodrigues.png`) — sem
palavras-chave como "logo", "marca", "qr".

**O que o v2 faz:** `classificacao.py` não acha keyword → vai para LLM
mini-prompt com só `gpt-4o-mini` e o nome do arquivo. Pode acertar ou não.
Não há análise de "fundo transparente" nem "qualidade boa/utilizável".

**O que precisa:** o classificador deve receber o conteúdo visual da imagem
(já disponível em `parts` quando vindo do file_processor) para julgar pelo
visual, não só pelo nome. A função `classificar_arquivo` já aceita `parts`
e `openai_client` opcionalmente — mas na extração do v2
(`_extract_pedido` em briefing_v2.py, linha ~383) o classificador é chamado
com `parts=None` para verificação determinística, e a chamada com visual
só acontece se `parts` for passado. Verificar se o fluxo está passando
`parts` corretamente quando a classificação LLM é necessária.

**Arquivos relevantes:**
- `utils/classificacao.py` — regras determinísticas + LLM mini-prompt
- `handlers/briefing_v2.py` — `_extract_pedido` (linha ~378–386) onde
  `classificar_arquivo` é chamado
- `utils/briefing_schema.py` — dataclass `Arquivo`

---

## PROBLEMA 3 — Olhar crítico e questionamentos ao atendimento ausentes no v2

Esta é a capacidade mais importante do Cyan original e que **não foi absorvida
pelo pipeline v2**: após analisar os arquivos, o Cyan deve se posicionar
tecnicamente — aprovar, alertar ou rejeitar — e fazer perguntas concretas ao
atendimento com base no que viu.

**O que o v1 fazia (via `analyze_file` + `ANALYSIS_PROMPT_TEMPLATE` em
`utils/openai_client.py:372–428`):**

Para cada arquivo recebido, o Cyan emitia um laudo com:
- `✅ Aprovado para produção` / `⚠️ Requer atenção` / `🔴 Recusar — inadequado`
- Análise técnica visual: nitidez, fundo (transparente / branco / colorido),
  resolução aparente, se é arte plana ou leque/mockup 3D, se QR é vetor ou bitmap
- Recomendação concreta: o que pedir ao cliente

Regras que governavam a análise (hoje em `ANALYSIS_PROMPT_TEMPLATE`):
- PNG com fundo transparente = preferido para logos raster — **nunca rejeitar**
- JPEG/PNG com fundo branco = aceito com ⚠️ (pedir versão transparente se possível)
- Resolução: ✅ nítida / ⚠️ duvidosa (alertar) / 🔴 pixelizada (recusar)
- **Sempre rejeitar:** leque/mockup 3D, resolução baixa com pixelização visível,
  logo com fundo colorido quando precisar de transparente, QR bitmap de baixa res
- Nunca rejeitar só por ser PNG, por falta de CMYK nativo, nem por transparência

**O que o v2 faz hoje:** nenhuma dessas avaliações. O campo `status_tecnico`
existe no schema mas fica sempre `""`. O render exibe `—`. O atendimento não
recebe nenhum feedback técnico sobre os arquivos que enviou.

**O que precisa ser incorporado ao v2:**
- A análise técnica por arquivo deve acontecer dentro do pipeline v2, produzindo
  o `status_tecnico` de cada `Arquivo` com o laudo (flag + descrição + recomendação)
- As perguntas resultantes da análise (ex: "o cliente tem o logo em PNG transparente?",
  "tem versão plana da arte?") devem entrar no questionário como pendências
- A lógica de rejeitar leque é parte desta análise — não do prompt de extração
- O `ANALYSIS_PROMPT_TEMPLATE` atual é a referência de regras a preservar;
  pode ser adaptado para produzir saída estruturada (JSON) em vez de texto livre
- O `file_processor.py` já tem as dimensões em memória — esses dados devem
  chegar ao modelo de análise para enriquecer o laudo

**Arquivos relevantes:**
- `utils/openai_client.py:372–428` — `ANALYSIS_PROMPT_TEMPLATE` (regras completas)
- `handlers/analysis_handler.py` — implementação atual da análise técnica (v1)
- `utils/briefing_schema.py` — dataclass `Arquivo` (campo `status_tecnico`)
- `utils/file_processor.py` — captura dimensões/modo internamente, não repassa
- `handlers/briefing_v2.py` — onde a análise precisa ser chamada no pipeline

---

## Estado atual do pipeline v2

- `CYAN_FLOW=v1` no `.env` — produção ainda no v1 (CY6.5 implementada mas
  não ativada)
- `#briefing-teste` roda v2 — é onde os problemas foram encontrados
- CY6.6 (faxina do v1) bloqueada até Problemas 1 e 2 resolvidos e validados
- O v2 está **funcional** para extração de pedido e questionário dirigido —
  os problemas são específicos à análise técnica de arquivo
