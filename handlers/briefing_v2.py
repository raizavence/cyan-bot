"""
handlers/briefing_v2.py — Pipeline v2 do Cyan (CY6.4, atualizado CY7.3 + CY7.6 + CY8).
Ligado SOMENTE no canal #briefing-teste. Canal de produção (#briefing-do-pedido) permanece v1.
"""
from __future__ import annotations
import json
import logging

import discord
from config import Config
from handlers.briefing_handler import BriefingHandler, ArtistCallModal, DISCORD_MAX
from utils import order_state
from utils.briefing_schema import (
    Pedido, Arquivo, CampoVisual,
    pedido_from_json, pedido_to_json,
    pendencias_criticas, itens_checklist, proxima_fase,
)
from utils.classificacao import classificar_arquivo
from utils import render

logger = logging.getLogger("cyan.v2")

# Rótulos legíveis dos campos de checklist (D2/D4)
_CAMPO_NOME_CHECKLIST = {
    "redes_sociais": "Redes sociais",
    "qr_code": "QR Code",
    "ean": "EAN / Código de barras",
    "tabela_nutricional": "Tabela nutricional",
    "selos": "Selos",
    "box": "Box para escrita",
}

# ── Prompts aprovados em CY6.3 (2026-06-11), atualizados CY8.3 (2026-06-12) ──

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

LIMITAÇÕES DE IMPRESSÃO (Offset CMYK — A Copack NÃO produz):
• Cores metálicas: prateado, dourado, Pantone metálico
• Brilho, glitter, efeitos luminosos, neon, fluorescente
• Acabamento fosco, verniz, relevo, hot stamping, foil

SE o cliente solicitar qualquer um desses:
→ NÃO rejeitar o pedido nem o arquivo — registrar em alertas_impressao[] uma frase completa
  (ex.: "Cliente solicitou cor prateada — impossível em Offset CMYK; definir cor substituta com o cliente")
→ Marcar o campo afetado (cor, fundo...) como pendente e formular pergunta de substituição.

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
• "pendente": desconhecido — será perguntado ou resolvido pelo checklist
• "preenchido": valor conhecido — preencher campo "valor"
• "resolvido_pela_referencia": está no arquivo de referência, não é pendência
• "identificado_na_referencia_aguardando_arquivo": QR/EAN/box visível na ref, arquivo não recebido
• "nao_se_aplica": confirmado que não existe neste pedido
• "aguardando_material": atendimento confirmou que vai ter; material/dado ainda não recebido

DIVISÃO DE TRABALHO COM O CHECKLIST (IMPORTANTE):
NÃO formule perguntas do tipo "vai ter X?" para: redes_sociais, qr_code, ean,
tabela_nutricional, selos e box — o sistema pergunta esses itens por botões de confirmação.
Deixe esses campos "pendente" quando desconhecidos. Suas perguntas cobrem:
quantidade, logo, cor/substituição, tipo de arte, e qualquer outra coisa não listada acima.
(Se o GPT perguntar mesmo assim, não há conflito — resposta em texto resolve o campo.)

LOGO em arte nova:
• Se arquivo recebido for VISUALMENTE identificado como logotipo/símbolo da marca (mesmo com
  nome genérico ou UUID) → logo.estado = "preenchido", logo.valor = nome_exato_do_arquivo.
  NÃO deixar pendente quando o logo já está em mãos — isso é o erro mais comum.
• Se contexto indica estampa ou arte fechada sem logo → "nao_se_aplica".
• Só "pendente" quando realmente desconhecido (nenhuma imagem de logo recebida e tipo não claro).

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
    "alertas_impressao": [],
    "modelos": [
      {
        "nome": "...",
        "quantidade": "...",
        "tipo_arte": "arte_nova|reimpressao|reimpressao_com_alteracao|pendente",
        "arquivo_referencia": "nome_do_arquivo.pdf ou null",
        "logo": {"estado": "pendente|preenchido|resolvido_pela_referencia|identificado_na_referencia_aguardando_arquivo|nao_se_aplica|aguardando_material", "valor": null},
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

DIVISÃO DE TRABALHO COM O CHECKLIST:
NÃO formule perguntas "vai ter X?" para redes_sociais, qr_code, ean, tabela_nutricional, selos, box
— o sistema pergunta por botões. Deixe esses campos "pendente"; suas perguntas cobrem o resto.

ESTADO aguardando_material: atendimento confirmou que vai ter; material/dado ainda não recebido.
(ex.: QR Code confirmado mas não enviado ainda → qr_code.estado = "aguardando_material")

Formato de resposta — SOMENTE JSON:
{
  "atualizacoes": {
    "pedido": {
      "produto": null,
      "numero_omie": null,
      "quantidade_total": null,
      "alertas_impressao": []
    },
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
• Arquivo visualmente identificado como logo (símbolo/logotipo de marca, mesmo com nome UUID):
  → novos_arquivos com classe "producao"; E modelos[idx].campos com
    logo = {"estado": "preenchido", "valor": nome_do_arquivo}.
  NÃO deixar logo pendente quando o arquivo de logo já está em mãos.
• Logo não existe (estampa/arte fechada): logo.estado = "nao_se_aplica"
• QR Code ou EAN recebido: estado = "preenchido". Não vai enviar: "nao_se_aplica"
• "modelos" só com índices que sofreram atualização
• "pedido" só com campos que a resposta resolve (null = não atualizar)
• alertas_impressao: lista SOMENTE alertas NOVOS (código deduplica)
• novos_arquivos: se arquivo já existe no pedido (mesmo nome), atualizar seus campos técnicos
• Máximo 3 perguntas por rodada.
• Para novos arquivos recebidos com imagem visível: aplicar as mesmas regras de ANÁLISE TÉCNICA
  do prompt de extração (leque, fundo, resolução, classificação pelo conteúdo visual)."""


# ── Views e Modals CY8 ────────────────────────────────────────────────────────

class ArtistCallViewV2(discord.ui.View):
    """Botão 🎨 no v2 — sem '✓ Arte sem alterações' (papel absorvido pela confirmação final, D8)."""

    def __init__(self, handler: "BriefingV2Handler", channel_id: int):
        super().__init__(timeout=43200)
        self.handler = handler
        self.channel_id = channel_id

    @discord.ui.button(label="🎨 Chamar Arte Finalista", style=discord.ButtonStyle.secondary)
    async def call_artist(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = order_state.get_v2_raw(self.channel_id)
        if not row:
            await interaction.response.send_message("❌ Nenhum briefing ativo.", ephemeral=True)
            return
        pedido = pedido_from_json(row[0])
        await interaction.response.send_modal(ArtistCallV2Modal(self.handler, self.channel_id, pedido))


class ArtistCallV2Modal(discord.ui.Modal, title="Chamado para Arte Finalista"):
    """Modal v2 — usa Pedido object para entendimento, não _extract_tag (D7)."""

    problem = discord.ui.TextInput(
        label="Descreva o que bloqueia o pedido",
        placeholder=(
            "Confirme antes: tentei resolver com o Cyan e busquei com o cliente. "
            "Se sim — descreva aqui."
        ),
        style=discord.TextStyle.long,
        max_length=1000,
        required=True,
    )

    def __init__(self, handler: "BriefingV2Handler", channel_id: int, pedido: Pedido):
        super().__init__()
        self.handler = handler
        self.channel_id = channel_id
        self.pedido = pedido

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        channel = interaction.client.get_channel(self.channel_id) or interaction.channel
        await self.handler._post_to_geral_v2(
            channel, self.pedido, self.problem.value, interaction.user.display_name
        )
        await channel.send("✅ Arte finalista acionada. O chamado foi enviado para o **#geral**.")


class ChecklistView(discord.ui.View):
    """Pergunta binária de presença para um item do checklist (D3/D4/D6)."""

    def __init__(
        self,
        handler: "BriefingV2Handler",
        channel_id: int,
        modelo_idx: int,
        campo: str,
        campo_label: str,
        modelo_label: str,
    ):
        super().__init__(timeout=43200)
        self.handler = handler
        self.channel_id = channel_id
        self.modelo_idx = modelo_idx
        self.campo = campo
        self.campo_label = campo_label
        self.modelo_label = modelo_label

    async def _apply(self, interaction: discord.Interaction, novo_estado: str) -> None:
        row = order_state.get_v2_raw(self.channel_id)
        if not row:
            await interaction.response.send_message("❌ Nenhum briefing ativo.", ephemeral=True)
            return

        pedido = pedido_from_json(row[0])
        conversation = json.loads(row[1])

        if self.modelo_idx < len(pedido.modelos):
            cv = getattr(pedido.modelos[self.modelo_idx], self.campo, None)
            if cv is not None:
                cv.estado = novo_estado

        if novo_estado == "aguardando_material":
            result_text = (
                f"**{self.modelo_label}** — {self.campo_label}: "
                "✅ vai ter — aguardando material do cliente"
            )
        else:
            result_text = f"**{self.modelo_label}** — {self.campo_label}: ❌ não vai ter"

        # D6: edita a própria mensagem com o resultado
        await interaction.response.edit_message(content=result_text, view=None)

        channel = interaction.client.get_channel(self.channel_id) or interaction.channel
        fase = proxima_fase(pedido)
        order_state.save_v2(self.channel_id, pedido_to_json(pedido), json.dumps(conversation), fase)

        if fase == "checklist":
            items = itens_checklist(pedido)
            if items:
                idx, campo = items[0]
                modelo = pedido.modelos[idx]
                mlabel = f"Modelo {idx+1}" + (f" ({modelo.nome})" if modelo.nome else "")
                clabel = _CAMPO_NOME_CHECKLIST.get(campo, campo)
                view = ChecklistView(self.handler, self.channel_id, idx, campo, clabel, mlabel)
                await channel.send(f"**{mlabel}** — vai ter **{clabel}**?", view=view)

        elif fase == "confirmacao":
            view = ConfirmacaoView(self.handler, self.channel_id)
            await channel.send(
                "Todas as dúvidas foram resolvidas? Se sim, já te entrego o briefing completo.",
                view=view,
            )

        elif fase == "complete":
            await self.handler._finalize_and_post(channel, pedido)

    @discord.ui.button(label="✅ Vai ter", style=discord.ButtonStyle.success)
    async def btn_sim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._apply(interaction, "aguardando_material")

    @discord.ui.button(label="❌ Não vai ter", style=discord.ButtonStyle.danger)
    async def btn_nao(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._apply(interaction, "nao_se_aplica")


class ConfirmacaoView(discord.ui.View):
    """Confirmação final — Sim finaliza; Não abre modal de observação (D7)."""

    def __init__(self, handler: "BriefingV2Handler", channel_id: int):
        super().__init__(timeout=43200)
        self.handler = handler
        self.channel_id = channel_id

    @discord.ui.button(label="✅ Sim — finalizar briefing", style=discord.ButtonStyle.success)
    async def btn_sim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        row = order_state.get_v2_raw(self.channel_id)
        if not row:
            await interaction.followup.send("❌ Nenhum briefing ativo.", ephemeral=True)
            return
        pedido = pedido_from_json(row[0])
        conversation = json.loads(row[1])
        order_state.save_v2(self.channel_id, pedido_to_json(pedido), json.dumps(conversation), "complete")
        channel = interaction.client.get_channel(self.channel_id) or interaction.channel
        await self.handler._finalize_and_post(channel, pedido)

    @discord.ui.button(
        label="📝 Não — observação para a Arte",
        style=discord.ButtonStyle.secondary,
    )
    async def btn_nao(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ObservacaoModal(self.handler, self.channel_id))


class ObservacaoModal(discord.ui.Modal, title="Observação para a Arte Finalista"):
    """Coleta observação/dúvida final; posta chamado no #geral e finaliza briefing (D7)."""

    observacao = discord.ui.TextInput(
        label="Observação ou dúvida para a Arte",
        placeholder="Descreva o que ainda não está resolvido para a Arte Finalista...",
        style=discord.TextStyle.long,
        max_length=1000,
        required=True,
    )

    def __init__(self, handler: "BriefingV2Handler", channel_id: int):
        super().__init__()
        self.handler = handler
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        row = order_state.get_v2_raw(self.channel_id)
        if not row:
            await interaction.followup.send("❌ Nenhum briefing ativo.", ephemeral=True)
            return
        pedido = pedido_from_json(row[0])
        conversation = json.loads(row[1])
        channel = interaction.client.get_channel(self.channel_id) or interaction.channel
        await self.handler._submit_observacao(
            channel, pedido, conversation,
            self.observacao.value, interaction.user.display_name,
        )


# ── Handler V2 ────────────────────────────────────────────────────────────────

class BriefingV2Handler:
    """Pipeline v2: extração estruturada → loop dirigido por dados → render por template."""

    def __init__(self, config: Config, v1_handler: BriefingHandler):
        self.config = config
        self.v1 = v1_handler
        self.ai = v1_handler.ai
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
            await self._avancar(interaction.channel, pedido, [], perguntas, prev_stage="")

        except Exception as exc:
            logger.error(f"[v2] Erro no /briefing: {exc}", exc_info=True)
            await interaction.channel.send(f"❌ Erro ao processar os materiais: `{exc}`")

    # ── on_message: resposta do atendimento ──────────────────────────────────

    async def handle_response(self, message: discord.Message) -> None:
        row = order_state.get_v2_raw(message.channel.id)
        if not row:
            return
        pedido_json, conv_json, stage = row

        # ── fase observacao: próxima mensagem de texto é a observação (D7) ──
        if stage == "observacao":
            pedido = pedido_from_json(pedido_json)
            conversation = json.loads(conv_json)
            await self._submit_observacao(
                message.channel, pedido, conversation,
                message.content, message.author.display_name,
            )
            return

        # ── fase confirmacao ──────────────────────────────────────────────────
        if stage == "confirmacao":
            text = message.content.strip().lower()
            pedido = pedido_from_json(pedido_json)
            conversation = json.loads(conv_json)

            if text in ("sim", "s", "ok"):
                order_state.save_v2(message.channel.id, pedido_to_json(pedido), conv_json, "complete")
                await self._finalize_and_post(message.channel, pedido)
                return

            if text in ("não", "nao", "n", "no"):
                order_state.save_v2(message.channel.id, pedido_json, conv_json, "observacao")
                await message.channel.send(
                    "Ok! Escreva agora sua observação ou dúvida para a Arte Finalista."
                )
                return

            # Texto livre em confirmacao → GPT → _avancar (D4)
            try:
                conversation.append({"role": "user", "content": message.content})
                async with message.channel.typing():
                    pedido, perguntas = await self._update_pedido(pedido, message.content, conversation)
                conversation.append({"role": "assistant", "content": perguntas or "(sem perguntas)"})
                await self._avancar(message.channel, pedido, conversation, perguntas, prev_stage=stage)
            except Exception as exc:
                logger.error(f"[v2] Erro na fase confirmacao: {exc}", exc_info=True)
                await message.channel.send(f"❌ Erro ao processar resposta: `{exc}`")
            return

        # ── fase checklist ────────────────────────────────────────────────────
        if stage == "checklist":
            pedido = pedido_from_json(pedido_json)
            conversation = json.loads(conv_json)
            text = message.content.strip().lower()
            items = itens_checklist(pedido)

            if items:
                idx, campo = items[0]
                if text in ("sim", "s", "ok", "v", "✓", "✅"):
                    cv = getattr(pedido.modelos[idx], campo, None)
                    if cv:
                        cv.estado = "aguardando_material"
                    clabel = _CAMPO_NOME_CHECKLIST.get(campo, campo)
                    await message.channel.send(
                        f"✅ {clabel}: vai ter — aguardando material do cliente."
                    )
                    await self._avancar(message.channel, pedido, conversation, prev_stage="checklist")
                    return

                if text in ("não", "nao", "n", "x", "✗", "❌"):
                    cv = getattr(pedido.modelos[idx], campo, None)
                    if cv:
                        cv.estado = "nao_se_aplica"
                    clabel = _CAMPO_NOME_CHECKLIST.get(campo, campo)
                    await message.channel.send(f"❌ {clabel}: não vai ter.")
                    await self._avancar(message.channel, pedido, conversation, prev_stage="checklist")
                    return

            # Texto livre em checklist → GPT (pode resolver vários itens de uma vez, D4)
            try:
                conversation.append({"role": "user", "content": message.content})
                async with message.channel.typing():
                    pedido, perguntas = await self._update_pedido(pedido, message.content, conversation)
                conversation.append({"role": "assistant", "content": perguntas or "(sem perguntas)"})
                await self._avancar(message.channel, pedido, conversation, perguntas, prev_stage="checklist")
            except Exception as exc:
                logger.error(f"[v2] Erro na fase checklist: {exc}", exc_info=True)
                await message.channel.send(f"❌ Erro ao processar resposta: `{exc}`")
            return

        # ── fase questionnaire ────────────────────────────────────────────────
        if stage != "questionnaire":
            return

        pedido = pedido_from_json(pedido_json)
        conversation = json.loads(conv_json)
        conversation.append({"role": "user", "content": message.content})

        try:
            async with message.channel.typing():
                pedido, perguntas = await self._update_pedido(pedido, message.content, conversation)
            conversation.append({"role": "assistant", "content": perguntas or "(sem perguntas)"})
            await self._avancar(message.channel, pedido, conversation, perguntas, prev_stage="questionnaire")

        except Exception as exc:
            logger.error(f"[v2] Erro ao processar resposta: {exc}", exc_info=True)
            await message.channel.send(f"❌ Erro ao processar resposta: `{exc}`")

    # ── on_message: arquivo durante questionário (resolve C-BUG4) ─────────────

    async def handle_attachment(self, message: discord.Message) -> None:
        """Arquivo chegou durante fluxo v2 — integra ao Pedido com análise visual (CY7.3)."""
        row = order_state.get_v2_raw(message.channel.id)
        if not row:
            return
        pedido_json, conv_json, stage = row

        if stage not in ("questionnaire", "checklist", "confirmacao", "observacao"):
            return

        pedido = pedido_from_json(pedido_json)
        conversation = json.loads(conv_json)

        # ── fase observacao: registra arquivo, aguarda texto (D7) ─────────────
        if stage == "observacao":
            novos = []
            for att in message.attachments:
                try:
                    parts = await self.file_processor.process_attachment(att)
                    classe = await classificar_arquivo(att.filename, parts, self.ai.client)
                    arq = Arquivo(nome=att.filename, url=att.url, classe=classe)
                    if not any(a.nome == att.filename for a in pedido.arquivos):
                        pedido.arquivos.append(arq)
                        novos.append(att.filename)
                except Exception as exc:
                    logger.warning(f"[v2] Falha ao processar anexo {att.filename}: {exc}")
            if novos:
                order_state.save_v2(message.channel.id, pedido_to_json(pedido), conv_json, "observacao")
                await message.channel.send(
                    "📎 Arquivo registrado. Aguardando sua observação por texto para acionar a Arte Finalista."
                )
            return

        # ── demais fases: integra arquivo ao Pedido com análise visual ────────
        novos = []
        all_visual_parts: list[dict] = []

        for att in message.attachments:
            try:
                parts = await self.file_processor.process_attachment(att)
                all_visual_parts.extend(parts)
                classe = await classificar_arquivo(att.filename, parts, self.ai.client)
                arq = Arquivo(nome=att.filename, url=att.url, classe=classe, status_tecnico="")
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
                pedido, perguntas = await self._update_pedido(
                    pedido, update_msg, conversation, image_parts=all_visual_parts
                )

            conversation.append({"role": "assistant", "content": perguntas or "(sem perguntas)"})
            await self._avancar(message.channel, pedido, conversation, perguntas, prev_stage=stage)

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

    # ── Máquina de fases (D1) ──────────────────────────────────────────────────

    async def _avancar(
        self,
        channel: discord.TextChannel,
        pedido: Pedido,
        conversation: list,
        perguntas: str = "",
        prev_stage: str = "",
    ) -> None:
        """Ponto único de transição de fase: salva estado e posta o que a fase pede (D1)."""
        fase = proxima_fase(pedido)
        order_state.save_v2(channel.id, pedido_to_json(pedido), json.dumps(conversation), fase)

        if fase == "questionnaire":
            await self._post_pedido_parcial(channel, pedido, perguntas)
            view = ArtistCallViewV2(self, channel.id)
            await channel.send("_Se precisar acionar a arte finalista:_", view=view)

        elif fase == "checklist":
            if prev_stage != "checklist":
                # Primeira entrada: briefing parcial + intro (D6)
                await self._post_pedido_parcial(channel, pedido)
                await channel.send(
                    "✅ Perguntas abertas resolvidas! Agora algumas confirmações rápidas sobre o pedido:"
                )
            items = itens_checklist(pedido)
            if items:
                idx, campo = items[0]
                modelo = pedido.modelos[idx]
                mlabel = f"Modelo {idx+1}" + (f" ({modelo.nome})" if modelo.nome else "")
                clabel = _CAMPO_NOME_CHECKLIST.get(campo, campo)
                view = ChecklistView(self, channel.id, idx, campo, clabel, mlabel)
                await channel.send(f"**{mlabel}** — vai ter **{clabel}**?", view=view)

        elif fase == "confirmacao":
            view = ConfirmacaoView(self, channel.id)
            await channel.send(
                "Todas as dúvidas foram resolvidas? Se sim, já te entrego o briefing completo.",
                view=view,
            )

        elif fase == "complete":
            await self._finalize_and_post(channel, pedido)

    # ── Submissão de observação final (D7) ────────────────────────────────────

    async def _submit_observacao(
        self,
        channel: discord.TextChannel,
        pedido: Pedido,
        conversation: list,
        obs_text: str,
        author_name: str = "atendimento",
    ) -> None:
        """Adiciona observação ao pedido, posta chamado no #geral e finaliza briefing."""
        pedido.observacoes_atendimento.append(obs_text)
        order_state.save_v2(channel.id, pedido_to_json(pedido), json.dumps(conversation), "complete")
        await self._post_to_geral_v2(channel, pedido, obs_text, author_name)
        await self._finalize_and_post(channel, pedido)

    # ── Post no #geral via Pedido object (D7) ─────────────────────────────────

    async def _post_to_geral_v2(
        self,
        channel: discord.TextChannel,
        pedido: Pedido,
        problem_text: str,
        author_name: str = "atendimento",
    ) -> None:
        if not self.config.GERAL_CHANNEL_ID:
            await channel.send("❌ Canal #geral não configurado. Chamado não enviado.")
            return

        geral = channel.guild.get_channel(int(self.config.GERAL_CHANNEL_ID))
        if not geral:
            await channel.send("❌ Canal #geral não encontrado. Chamado não enviado.")
            return

        entendimento = _entendimento_do_objeto(pedido)
        raiza_id = self.config.RAIZA_DISCORD_ID
        mention = f"<@{raiza_id}>" if raiza_id else "@arte_finalista"

        num = pedido.numero_omie or "?"
        cli = pedido.cliente or "?"
        texto = (
            f"🔔 **Chamado de arte** — Pedido **{num}** · {cli}\n\n"
            f"**Problema relatado pelo atendimento ({author_name}):**\n"
            f"{problem_text}\n\n"
            f"**Entendimento do Cyan:**\n"
            f"{entendimento}"
        )
        await geral.send(f"{mention}\n{texto}")

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
        """Atualiza o Pedido com a resposta do atendimento via GPT estruturado."""
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
                *conversation[:-1],
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
        briefing_text = render.briefing(pedido)
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


# ── Helpers puros ─────────────────────────────────────────────────────────────

def _entendimento_do_objeto(pedido: Pedido) -> str:
    """Gera resumo do pedido a partir do objeto (D7 — sem _extract_tag)."""
    linhas = [f"Pedido {pedido.numero_omie or '?'} — {pedido.cliente or '?'}"]
    if pedido.produto:
        linhas.append(f"Produto: {pedido.produto} · Qtd total: {pedido.quantidade_total or '?'}")
    tipo_render = {
        "arte_nova": "Arte Nova",
        "reimpressao": "Reimpressão",
        "reimpressao_com_alteracao": "Reimpressão com alteração",
        "pendente": "A definir",
    }
    for i, m in enumerate(pedido.modelos, 1):
        tipo = tipo_render.get(m.tipo_arte, m.tipo_arte)
        nome = f" ({m.nome})" if m.nome else ""
        qtd = f" · {m.quantidade}" if m.quantidade else ""
        linhas.append(f"• Modelo {i}{nome}{qtd} — {tipo}")
    return "\n".join(linhas)


# ── Aplicador de atualizações ─────────────────────────────────────────────────

def _apply_updates(pedido: Pedido, atualizacoes: dict) -> Pedido:
    """Aplica o JSON de atualizações ao Pedido in-place e retorna o pedido."""

    pedido_upd = atualizacoes.get("pedido") or {}

    # Campos escalares do Pedido
    for campo in ("numero_omie", "cliente", "produto", "quantidade_total"):
        val = pedido_upd.get(campo)
        if val:
            setattr(pedido, campo, str(val))

    # alertas_impressao (CY8 — dedupe, igual a inconsistencias)
    for alerta in pedido_upd.get("alertas_impressao", []):
        if alerta and alerta not in pedido.alertas_impressao:
            pedido.alertas_impressao.append(alerta)

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
