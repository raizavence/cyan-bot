"""
handlers/briefing_v2.py — Pipeline v2 do Cyan (CY6.4, atualizado CY7.3 + CY7.6).
Ligado SOMENTE no canal #briefing-teste. Canal de produção (#briefing-do-pedido) permanece v1.
"""
from __future__ import annotations
import json
import logging

import discord
from config import Config
from handlers.briefing_handler import BriefingHandler, ArtistCallView, DISCORD_MAX
from utils import order_state
from utils.briefing_schema import (
    Pedido, Arquivo, CampoVisual,
    pedido_from_json, pedido_to_json, pendencias_criticas,
)
from utils.classificacao import classificar_arquivo
from utils import render

logger = logging.getLogger("cyan.v2")

# ── Prompts aprovados em CY6.3 (2026-06-11) ──────────────────────────────────

_DOMAIN = """Você é o Cyan, assistente de briefing de arte da Copack — empresa de embalagens sustentáveis.
Processo de impressão: Offset (CMYK obrigatório).
Sistema de pedidos: Omie ERP. Atendimento: WhatsApp via Kommo.

MATERIAIS ACEITOS SEM RESTRIÇÃO:
• PSD e PSB: sempre aceitos — analisar visualmente como qualquer imagem.
• Vetoriais: AI, EPS, CDR, SVG, FH, PS — sempre produção.
• PNG com fundo transparente: aceito (não rejeitar por falta de CMYK nativo).
• PNG com fundo branco: aceito com alerta ⚠️.

RESPONSABILIDADE DO CLIENTE (verificar recebimento e formato, não gerar):
• QR Code, código de barras EAN, tabela nutricional.
• Alérgenos, ingredientes, selos de restrição — RDC 26/2015. Não questionar conteúdo.

OFFSET / CMYK: obrigatório para impressão. HEX informado → solicitar CMYK.
A Copack tem conversão automática — nunca rejeitar arquivo só por ser RGB.

ARQUIVOS > 10 MB: solicitar via Google Drive com link compartilhado.

TOM: objetivo, técnico. Emojis de status: ✅ ⚠️ 🔴 🟡 ➖."""

_EXTRACTION_SYSTEM = _DOMAIN + """

Analise os materiais do pedido e devolva JSON com dois campos: "pedido" e "perguntas".

REGRAS DE EXTRAÇÃO:

NÚMERO DO PEDIDO (pedido.numero_omie):
• Buscar PRIMEIRO no texto do atendimento (título do card, mensagem).
• Modelos de aprovação têm o número do PEDIDO ANTERIOR no nome — só usar provisoriamente
  se nenhum número aparecer no texto; incluir em inconsistencias[]:
  "Confirme: o número Omie deste pedido é [X]? Encontrei no modelo de aprovação."
• NUNCA usar CNPJ, CPF ou número do cliente.

PRODUTO (pedido.produto): tipo do produto + volumetria em uma string. Ex: "Bandeja 500ml".

TIPO DE ARTE por modelo (modelos[].tipo_arte):
• Sem menção de reimpressão → "arte_nova"
• "reimpressão", "igual ao anterior", "mesma arte" → "reimpressao"
• "com alteração", "ajuste", "mudança" → "reimpressao_com_alteracao"
• "mil desta arte e mil daquela" → dois modelos distintos com tipo_arte diferente
• Dúvida → "pendente"

REFERÊNCIA VISUAL (modelos[].arquivo_referencia):
• PDF com "Modelo_Aprovacao" ou número de pedido anterior no nome = arquivo de referência.
  Quando presente: logo, fundo, cor, redes_sociais → estado "resolvido_pela_referencia".

ESTADOS DE CAMPO (logo, fundo, cor, redes_sociais, qr_code, ean, tabela_nutricional, selos, box):
• "pendente": desconhecido — será perguntado
• "preenchido": valor conhecido — preencher campo "valor"
• "resolvido_pela_referencia": está no arquivo de referência, não é pendência
• "identificado_na_referencia_aguardando_arquivo": QR/EAN/box visível na ref, arquivo não recebido
• "nao_se_aplica": confirmado que não existe neste pedido

LOGO em arte nova: se contexto indica estampa ou arte fechada sem logo → "nao_se_aplica".
Se desconhecido → "pendente" (será perguntado uma vez).

COR em arte nova: se cliente não especificou e só enviou logo → estado "preenchido",
valor "fundo branco (padrão — cliente não especificou)". Só "pendente" se cliente
mencionou cores ou referência é colorida.

ARQUIVOS (arquivos[].classe): "producao" | "referencia" | "indefinido"
QR/EAN/box APENAS na referência → estado "identificado_na_referencia_aguardando_arquivo".

INCONSISTÊNCIAS: conflito REAL entre dados. Informação faltando é pendência, não inconsistência.

ARTE DE IA → só referência, nunca produção.

ANÁLISE TÉCNICA POR ARQUIVO (preencher status_tecnico, flag e recomendacao de cada Arquivo):
Use os blocos [DADOS TÉCNICOS {filename}: ...] presentes no contexto + avaliação visual da imagem.

RESOLUÇÃO / NITIDEZ:
• Imagem nítida e clara → flag "ok"
• Leve serrilhado visível mas usável → flag "atencao", recomendacao: "Verificar qualidade ao ampliar"
• Pixelização clara ou resolução nitidamente baixa → flag "recusar", recomendacao: "Solicitar arquivo em maior resolução"

FUNDO DE LOGO:
• Fundo transparente (indicado nos DADOS TÉCNICOS ou visível) → flag "ok" (preferido — NUNCA rejeitar por falta de CMYK)
• Fundo branco → flag "atencao", recomendacao: "Solicitar versão com fundo transparente, se disponível"
• Fundo colorido/estampado quando a aplicação exige transparente → flag "recusar"

LEQUE / MOCKUP 3D (regra Raíza 2026-06-12 — aplicar sempre que identificar):
• Arte em leque (forma cônica/leque) ou mockup 3D em bitmap → OBRIGATÓRIO:
  - classe: "referencia"
  - flag: "atencao"
  - status_tecnico: "Arte em formato leque (bitmap) — não editável; usar como referência visual"
  - No modelo correspondente: tipo_arte = "arte_nova", arquivo_referencia = nome deste arquivo
  - Adicionar em acoes_para_arte: "Recriar arte plana semelhante à referência em leque, com auxílio de ferramenta de IA"
  - NÃO gerar pendência de reenvio — recriação é o fluxo normal, não um problema

QR/EAN BITMAP DE BAIXA RESOLUÇÃO:
• flag: "recusar", recomendacao: "Solicitar QR Code ou EAN em vetor ou PNG de alta resolução (min. 300 dpi)"

CLASSIFICAÇÃO PELO CONTEÚDO VISUAL (nunca pelo nome do arquivo):
• Logo visível (símbolo/texto de marca) com fundo transparente e boa resolução → classe "producao", flag "ok"
• status_tecnico deve descrever o arquivo: ex. "PNG 2048×1536, fundo transparente — utilizável como produção"
• Se não for possível avaliar visualmente → flag "" (vazio), status_tecnico: "Avaliação visual não conclusiva"

Formato de resposta — SOMENTE JSON:
{
  "pedido": {
    "numero_omie": "...",
    "cliente": "...",
    "produto": "...",
    "quantidade_total": "...",
    "modelos": [
      {
        "nome": "...",
        "quantidade": "...",
        "tipo_arte": "arte_nova|reimpressao|reimpressao_com_alteracao|pendente",
        "arquivo_referencia": "nome_do_arquivo.pdf ou null",
        "logo": {"estado": "pendente|preenchido|resolvido_pela_referencia|identificado_na_referencia_aguardando_arquivo|nao_se_aplica", "valor": null},
        "fundo": {"estado": "...", "valor": null},
        "cor": {"estado": "...", "valor": null},
        "redes_sociais": {"estado": "...", "valor": null},
        "qr_code": {"estado": "...", "valor": null},
        "ean": {"estado": "...", "valor": null},
        "tabela_nutricional": {"estado": "...", "valor": null},
        "selos": {"estado": "...", "valor": null},
        "box": {"estado": "...", "valor": null},
        "acoes_para_arte": ["instrução completa de montagem para o arte finalista"]
      }
    ],
    "arquivos": [
      {"nome": "nome_exato.ext", "url": "", "classe": "producao|referencia|indefinido", "status_tecnico": "PNG 1920×1080, fundo transparente — utilizável como produção", "flag": "ok|atencao|recusar|", "recomendacao": ""}
    ],
    "inconsistencias": []
  },
  "perguntas": "Perguntas diretas ao atendimento, ou string vazia se briefing completo"
}"""

_UPDATE_SYSTEM = _DOMAIN + """

O atendimento respondeu. Atualize o pedido com as novas informações.

Formato de resposta — SOMENTE JSON:
{
  "atualizacoes": {
    "pedido": {"produto": null, "numero_omie": null, "quantidade_total": null},
    "modelos": [
      {
        "indice": 0,
        "campos": {
          "quantidade": "1500",
          "cor": {"estado": "preenchido", "valor": "CMYK 47/70/75/53"},
          "logo": {"estado": "nao_se_aplica", "valor": null}
        }
      }
    ],
    "novos_arquivos": [
      {"nome": "arquivo.png", "url": "", "classe": "producao|referencia|indefinido", "status_tecnico": "...", "flag": "ok|atencao|recusar|", "recomendacao": "..."}
    ],
    "inconsistencias": []
  },
  "perguntas": "Próximas perguntas, ou string vazia se tudo resolvido"
}

REGRAS:
• Atualizar SOMENTE campos que a resposta resolve. Não inventar.
• Logo não existe (estampa/arte fechada): logo.estado = "nao_se_aplica"
• QR Code ou EAN recebido: estado = "preenchido". Não vai enviar: "nao_se_aplica"
• "modelos" só com índices que sofreram atualização
• "pedido" só com campos que a resposta resolve (null = não atualizar)
• novos_arquivos: se arquivo já existe no pedido (mesmo nome), atualizar seus campos técnicos
• Máximo 3 perguntas por rodada.
• Para novos arquivos recebidos com imagem visível: aplicar as mesmas regras de ANÁLISE TÉCNICA
  do prompt de extração (leque, fundo, resolução, classificação pelo conteúdo visual)."""


# ── Handler V2 ────────────────────────────────────────────────────────────────

class BriefingV2Handler:
    """Pipeline v2: extração estruturada → loop dirigido por dados → render por template."""

    def __init__(self, config: Config, v1_handler: BriefingHandler):
        self.config = config
        self.v1 = v1_handler          # reusa _build_content, send_artist_call, _send_chunked
        self.ai = v1_handler.ai       # OpenAIClient
        self.file_processor = v1_handler.file_processor

    # ── /briefing ─────────────────────────────────────────────────────────────

    async def handle(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer()
        except discord.errors.HTTPException:
            return

        try:
            messages = await self.v1._collect_messages(interaction.channel)
            if not messages:
                await interaction.followup.send(
                    "❌ Nenhum material encontrado. Envie os materiais antes de usar **/briefing**."
                )
                return

            await interaction.followup.send(
                f"🔍 Coletei **{len(messages)}** mensagens. Extraindo briefing estruturado — aguarde..."
            )

            content_parts, file_records, audio_transcripts = await self.v1._build_content(messages)

            for filename, transcript in audio_transcripts:
                await self.v1._post_audio_preview(interaction.channel, transcript=transcript, filename=filename)

            pedido, perguntas = await self._extract_pedido(content_parts, file_records)

            criticas = pendencias_criticas(pedido)
            order_state.save_v2(
                interaction.channel.id,
                pedido_to_json(pedido),
                json.dumps([]),
                "complete" if not criticas else "questionnaire",
            )

            if not criticas:
                await self._finalize_and_post(interaction.channel, pedido)
            else:
                await self._post_pedido_parcial(interaction.channel, pedido, perguntas)
                view = ArtistCallView(self.v1, interaction.channel.id)
                await interaction.channel.send("_Se precisar acionar a arte finalista:_", view=view)

        except Exception as exc:
            logger.error(f"[v2] Erro no /briefing: {exc}", exc_info=True)
            await interaction.channel.send(f"❌ Erro ao processar os materiais: `{exc}`")

    # ── on_message: resposta do atendimento ──────────────────────────────────

    async def handle_response(self, message: discord.Message) -> None:
        row = order_state.get_v2_raw(message.channel.id)
        if not row:
            return
        pedido_json, conv_json, stage = row
        if stage != "questionnaire":
            return

        pedido = pedido_from_json(pedido_json)
        conversation = json.loads(conv_json)
        conversation.append({"role": "user", "content": message.content})

        try:
            async with message.channel.typing():
                pedido, perguntas = await self._update_pedido(pedido, message.content, conversation)

            conversation.append({"role": "assistant", "content": perguntas or "(sem perguntas)"})
            criticas = pendencias_criticas(pedido)
            new_stage = "complete" if not criticas else "questionnaire"

            order_state.save_v2(
                message.channel.id,
                pedido_to_json(pedido),
                json.dumps(conversation),
                new_stage,
            )

            if not criticas:
                await self._finalize_and_post(message.channel, pedido)
            else:
                await self._post_pedido_parcial(message.channel, pedido, perguntas)
                view = ArtistCallView(self.v1, message.channel.id)
                await message.channel.send("_Se precisar acionar a arte finalista:_", view=view)

        except Exception as exc:
            logger.error(f"[v2] Erro ao processar resposta: {exc}", exc_info=True)
            await message.channel.send(f"❌ Erro ao processar resposta: `{exc}`")

    # ── on_message: arquivo durante questionário (resolve C-BUG4) ─────────────

    async def handle_attachment(self, message: discord.Message) -> None:
        """Arquivo chegou durante questionário v2 — integra ao Pedido com análise visual (CY7.3)."""
        row = order_state.get_v2_raw(message.channel.id)
        if not row:
            return
        pedido_json, conv_json, stage = row
        if stage != "questionnaire":
            return

        pedido = pedido_from_json(pedido_json)
        conversation = json.loads(conv_json)
        novos = []
        all_visual_parts: list[dict] = []

        for att in message.attachments:
            try:
                parts = await self.file_processor.process_attachment(att)
                all_visual_parts.extend(parts)   # acumula partes visuais para o GPT
                classe = await classificar_arquivo(att.filename, parts, self.ai.client)
                arq = Arquivo(nome=att.filename, url=att.url, classe=classe, status_tecnico="")
                # Evitar duplicata por nome
                if not any(a.nome == att.filename for a in pedido.arquivos):
                    pedido.arquivos.append(arq)
                    novos.append(att.filename)
            except Exception as exc:
                logger.warning(f"[v2] Falha ao processar anexo {att.filename}: {exc}")

        if not novos:
            return

        nomes_fmt = ", ".join(f"**{n}**" for n in novos)
        update_msg = f"Arquivo(s) recebido(s) durante o questionário: {nomes_fmt}"
        conversation.append({"role": "user", "content": update_msg})

        try:
            async with message.channel.typing():
                # Passa partes visuais para GPT analisar tecnicamente (CY7.3)
                pedido, perguntas = await self._update_pedido(
                    pedido, update_msg, conversation, image_parts=all_visual_parts
                )

            conversation.append({"role": "assistant", "content": perguntas or "(sem perguntas)"})
            criticas = pendencias_criticas(pedido)
            new_stage = "complete" if not criticas else "questionnaire"

            order_state.save_v2(
                message.channel.id,
                pedido_to_json(pedido),
                json.dumps(conversation),
                new_stage,
            )

            if not criticas:
                await self._finalize_and_post(message.channel, pedido)
            else:
                await self._post_pedido_parcial(message.channel, pedido, perguntas)
                view = ArtistCallView(self.v1, message.channel.id)
                await message.channel.send("_Se precisar acionar a arte finalista:_", view=view)

        except Exception as exc:
            logger.error(f"[v2] Erro ao integrar anexo: {exc}", exc_info=True)
            await message.channel.send(
                f"📎 {nomes_fmt} recebido(s) mas não foi possível integrar automaticamente. "
                "Mencione o arquivo na sua próxima resposta para que eu atualize o briefing."
            )

    # ── /finalizar ────────────────────────────────────────────────────────────

    async def finalize(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer()
        except discord.errors.HTTPException:
            return

        row = order_state.get_v2_raw(interaction.channel.id)
        if not row:
            await interaction.followup.send(
                "❌ Nenhum briefing v2 ativo neste canal. Use **/briefing** primeiro."
            )
            return

        pedido_json, _, _ = row
        pedido = pedido_from_json(pedido_json)

        try:
            await interaction.followup.send("📦 Gerando briefing final — aguarde...")
            await self._finalize_and_post(interaction.channel, pedido)
            order_state.save_v2(
                interaction.channel.id,
                pedido_to_json(pedido),
                "[]",
                "complete",
            )
        except Exception as exc:
            logger.error(f"[v2] Erro no /finalizar: {exc}", exc_info=True)
            await interaction.channel.send(f"❌ Erro ao finalizar: `{exc}`")

    # ── chamada de extração ────────────────────────────────────────────────────

    async def _extract_pedido(
        self, content_parts: list, file_records: dict
    ) -> tuple[Pedido, str]:
        """Extração estruturada: multimodal → JSON do Pedido + perguntas."""
        response = await self.ai.client.chat.completions.create(
            model=self.ai.model,
            messages=[
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": content_parts},
            ],
            response_format={"type": "json_object"},
            max_tokens=4096,
        )
        raw = (response.choices[0].message.content or "").strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"[v2] Extração devolveu JSON inválido: {raw[:300]}")
            raise ValueError("GPT devolveu JSON inválido na extração do pedido.")

        pedido = pedido_from_json(data.get("pedido", {}))
        perguntas = data.get("perguntas", "")

        # Patch URLs e classificação canônica por extensão (vetorial/PSD/PDF prevalece;
        # rasters mantêm a decisão visual do GPT — CY7.3)
        for arq in pedido.arquivos:
            rec = file_records.get(arq.nome)
            if rec:
                arq.url = rec.url
            classe_det = await classificar_arquivo(arq.nome, None)
            if classe_det != "indefinido":
                # Determinístico só para formatos não-raster (vetor, PSD, PDF)
                arq.classe = classe_det

        return pedido, perguntas

    # ── chamada de atualização ────────────────────────────────────────────────

    async def _update_pedido(
        self,
        pedido: Pedido,
        resposta: str,
        conversation: list,
        image_parts: list | None = None,
    ) -> tuple[Pedido, str]:
        """Atualiza o Pedido com a resposta do atendimento via GPT estruturado.

        image_parts: partes visuais (text + image_url) de anexos recebidos no turno atual.
        Quando presentes, o GPT vê as imagens e aplica análise técnica (CY7.3).
        """
        text_content = (
            f"Estado atual do pedido:\n```json\n{pedido_to_json(pedido)}\n```\n\n"
            f"Resposta do atendimento: {resposta}"
        )
        if image_parts:
            user_content: str | list = [{"type": "text", "text": text_content}, *image_parts]
        else:
            user_content = text_content

        response = await self.ai.client.chat.completions.create(
            model=self.ai.model,
            messages=[
                {"role": "system", "content": _UPDATE_SYSTEM},
                *conversation[:-1],  # contexto sem a última mensagem (já inclusa no user_content)
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            max_tokens=2048,
        )
        raw = (response.choices[0].message.content or "").strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"[v2] Atualização devolveu JSON inválido: {raw[:300]}")
            raise ValueError("GPT devolveu JSON inválido na atualização do pedido.")

        pedido = _apply_updates(pedido, data.get("atualizacoes", {}))
        perguntas = data.get("perguntas", "")
        return pedido, perguntas

    # ── entrega final ─────────────────────────────────────────────────────────

    async def _finalize_and_post(self, channel: discord.TextChannel, pedido: Pedido) -> None:
        """Posta o briefing final completo com cabeçalho de conclusão (CY7.6)."""
        briefing_text = render.briefing(pedido)  # sem perguntas abertas
        await channel.send(
            f"✅ **Briefing v2 finalizado!**\n"
            f"Cliente: **{pedido.cliente or '?'}** | Pedido: **{pedido.numero_omie or '?'}**\n"
            f"{'─' * 40}"
        )
        await self.v1._send_chunked(channel, briefing_text)
        await channel.send(
            "_Use **/limpar** para apagar as mensagens e liberar o canal para o próximo pedido._"
        )

    # ── helpers ────────────────────────────────────────────────────────────────

    async def _post_pedido_parcial(
        self, channel: discord.TextChannel, pedido: Pedido, perguntas: str = ""
    ) -> None:
        """Posta o briefing em andamento com perguntas embutidas na seção ❓ (CY7.6)."""
        texto = render.briefing(pedido, perguntas)
        await self.v1._send_chunked(channel, texto)


# ── Aplicador de atualizações ─────────────────────────────────────────────────

def _apply_updates(pedido: Pedido, atualizacoes: dict) -> Pedido:
    """Aplica o JSON de atualizações ao Pedido in-place e retorna o pedido."""

    # Campos do Pedido (nivel raiz)
    for campo in ("numero_omie", "cliente", "produto", "quantidade_total"):
        val = (atualizacoes.get("pedido") or {}).get(campo)
        if val:
            setattr(pedido, campo, str(val))

    # Atualizações por modelo
    for upd in atualizacoes.get("modelos", []):
        idx = upd.get("indice", 0)
        if not isinstance(idx, int) or idx >= len(pedido.modelos):
            continue
        modelo = pedido.modelos[idx]
        for campo, valor in (upd.get("campos") or {}).items():
            if campo == "quantidade":
                modelo.quantidade = str(valor) if valor else None
            elif campo == "tipo_arte":
                modelo.tipo_arte = str(valor)
            elif campo == "arquivo_referencia":
                modelo.arquivo_referencia = str(valor) if valor else None
            elif campo == "acoes_para_arte" and isinstance(valor, list):
                modelo.acoes_para_arte = valor
            elif campo == "nome":
                modelo.nome = str(valor)
            else:
                cv = getattr(modelo, campo, None)
                if cv is None:
                    continue
                if isinstance(valor, dict):
                    cv.estado = valor.get("estado", cv.estado)
                    cv.valor = valor.get("valor", cv.valor)
                elif isinstance(valor, str):
                    cv.estado = "preenchido"
                    cv.valor = valor

    # Arquivos: atualiza existentes (mesmo nome) ou adiciona novos (CY7.3)
    for arq_data in atualizacoes.get("novos_arquivos", []):
        nome = arq_data.get("nome", "")
        if not nome:
            continue
        existing = next((a for a in pedido.arquivos if a.nome == nome), None)
        if existing:
            # Atualiza campos técnicos do arquivo já presente
            if arq_data.get("classe"):
                existing.classe = arq_data["classe"]
            if arq_data.get("status_tecnico"):
                existing.status_tecnico = arq_data["status_tecnico"]
            if "flag" in arq_data:
                existing.flag = arq_data["flag"]
            if "recomendacao" in arq_data:
                existing.recomendacao = arq_data["recomendacao"]
        else:
            pedido.arquivos.append(Arquivo(
                nome=nome,
                url=arq_data.get("url", ""),
                classe=arq_data.get("classe", "indefinido"),
                status_tecnico=arq_data.get("status_tecnico", ""),
                flag=arq_data.get("flag", ""),
                recomendacao=arq_data.get("recomendacao", ""),
            ))

    # Inconsistências novas
    for inc in atualizacoes.get("inconsistencias", []):
        if inc and inc not in pedido.inconsistencias:
            pedido.inconsistencias.append(inc)

    return pedido
