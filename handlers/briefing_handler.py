from __future__ import annotations
import logging
import discord
from config import Config
from utils.openai_client import OpenAIClient
from utils.file_processor import FileProcessor
from utils import order_state
from utils.order_state import OrderState, FileRecord, PendingCall

logger = logging.getLogger("cyan.briefing")

DISCORD_MAX = 1900


# ── Views e Modal do chamado para arte finalista ──────────────────────────────

class ArtistCallView(discord.ui.View):
    """Botão discreto exibido após cada rodada de perguntas."""

    def __init__(self, handler: BriefingHandler, channel_id: int):
        super().__init__(timeout=43200)  # 12 h
        self.handler = handler
        self.channel_id = channel_id

    @discord.ui.button(label="🎨 Chamar Arte Finalista", style=discord.ButtonStyle.secondary, row=0)
    async def call_artist(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = order_state.get(self.channel_id)
        if not state:
            await interaction.response.send_message(
                "❌ Nenhum briefing ativo neste canal.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            ArtistCallModal(self.handler, self.channel_id)
        )

    @discord.ui.button(
        label="✓ Arte sem alterações — confirmado com o cliente",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def confirm_as_is(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = order_state.get(self.channel_id)
        if not state:
            await interaction.edit_original_response(content="Nenhum briefing ativo.", view=None)
            return

        await interaction.edit_original_response(
            content="✅ Confirmação registrada — finalizando briefing...", view=None
        )
        channel = interaction.client.get_channel(self.channel_id) or interaction.channel
        await channel.send(
            "✅ **Confirmado pelo atendimento:** a arte não terá alterações. "
            "Seguir apenas com o modelo disponibilizado. Confirmado com o cliente."
        )

        state.conversation.append({
            "role": "user",
            "content": (
                "Confirmado com o cliente: a arte não terá alterações. "
                "Seguir exatamente o modelo disponibilizado. "
                "Todas as perguntas estão respondidas — não há mais pendências. "
                "Finalize o briefing agora."
            ),
        })
        order_state.save(self.channel_id, state)

        try:
            final_result = await self.handler.ai.generate_final_briefing(state.conversation)
            if final_result.get("resumo"):
                state.final_resumo = final_result["resumo"]
            if final_result.get("análise"):
                state.final_briefing = final_result["análise"]
            state.conversation.append({"role": "assistant", "content": final_result["raw"]})
            order_state.save(self.channel_id, state)
        except Exception as exc:
            logger.error(f"Erro ao finalizar via botão confirmação: {exc}", exc_info=True)

        await self.handler._deliver_final(channel, state)


class ArtistCallModal(discord.ui.Modal, title="Chamado para Arte Finalista"):

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

    def __init__(self, handler: BriefingHandler, channel_id: int):
        super().__init__()
        self.handler = handler
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        state = order_state.get(self.channel_id)
        if not state:
            await interaction.response.send_message(
                "❌ Nenhum briefing ativo.", ephemeral=True
            )
            return

        state.pending_call = PendingCall(
            user_id=interaction.user.id,
            user_display_name=interaction.user.display_name,
            problem_text=self.problem.value,
        )
        order_state.save(self.channel_id, state)

        view = AwaitingImageView(self.handler, self.channel_id)
        await interaction.response.send_message(
            "✅ Problema registrado!\n\n"
            "Se quiser incluir uma imagem no chamado, **envie-a agora no canal**.\n"
            "Sem imagem? Clique no botão abaixo:",
            view=view,
            ephemeral=True,
        )


class AwaitingImageView(discord.ui.View):
    """Exibida após o modal, enquanto aguarda a imagem opcional."""

    def __init__(self, handler: BriefingHandler, channel_id: int):
        super().__init__(timeout=300)
        self.handler = handler
        self.channel_id = channel_id

    @discord.ui.button(label="Enviar sem imagem", style=discord.ButtonStyle.secondary)
    async def send_without_image(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        state = order_state.get(self.channel_id)
        if not state or not state.pending_call:
            await interaction.edit_original_response(
                content="O chamado já foi enviado.", view=None
            )
            return
        # Busca o canal pelo ID para garantir objeto TextChannel completo (com .guild)
        channel = interaction.client.get_channel(self.channel_id)
        if not channel:
            await interaction.edit_original_response(
                content="❌ Canal não encontrado.", view=None
            )
            return
        await self.handler.send_artist_call(channel, state, None)
        await interaction.edit_original_response(
            content="✅ Chamado enviado para o **#geral** sem imagem.", view=None
        )


class BriefingHandler:
    def __init__(self, config: Config):
        self.config = config
        self.ai = OpenAIClient(
            api_key=config.OPENAI_API_KEY,
            model=config.GPT_MODEL,
            max_tokens=config.GPT_MAX_TOKENS,
        )
        self.file_processor = FileProcessor(
            max_size_mb=config.MAX_FILE_SIZE_MB,
            pdf_max_pages=config.PDF_MAX_PAGES,
            pdf_dpi=config.PDF_DPI,
            openai_api_key=config.OPENAI_API_KEY,
        )
        # Referência ao analysis_handler injetada pelo main.py
        self.analysis_handler = None

    # ── /briefing ─────────────────────────────────────────────────────────────

    async def handle(self, interaction: discord.Interaction) -> None:
        """Etapas 1-3: analisa materiais, classifica arquivos, inicia questionário."""
        try:
            await interaction.response.defer()
        except discord.errors.HTTPException:
            # Interação já foi reconhecida (ex: reinício durante processamento anterior)
            return

        try:
            messages = await self._collect_messages(interaction.channel)
            if not messages:
                await interaction.followup.send(
                    "❌ Nenhum material encontrado no canal.\n"
                    "Envie as conversas e arquivos do pedido antes de usar **/briefing**."
                )
                return

            await interaction.followup.send(
                f"🔍 Coletei **{len(messages)}** mensagens. Analisando materiais — aguarde..."
            )

            # Monta payload multimodal
            content_parts, file_records, audio_transcripts = await self._build_content(messages)

            # Posta transcrições de áudio para o atendimento verificar
            for filename, transcript in audio_transcripts:
                await self._post_audio_preview(interaction.channel, filename, transcript)

            # Primeira mensagem da conversa
            conversation = [{"role": "user", "content": content_parts}]

            # Chama GPT para análise inicial + perguntas
            result = await self.ai.briefing_turn(conversation)
            conversation.append({"role": "assistant", "content": result["raw"]})

            # Extrai número do pedido e cliente da análise do GPT
            import re
            order_match = re.search(r'Pedido\s+Omie[:\s]+(\S+)', result["análise"] or "")
            client_match = re.search(r'Cliente[:\s]+(.+?)(?:\n|$)', result["análise"] or "")
            order_number_extracted = order_match.group(1).strip(".,)") if order_match else "?"
            client_extracted = client_match.group(1).strip() if client_match else "?"

            # Salva estado
            state = OrderState(
                briefing_channel_id=interaction.channel.id,
                stage="questionnaire" if result["status"] == "CONTINUAR" else "complete",
                conversation=conversation,
                files=file_records,
                order_number=order_number_extracted,
                client=client_extracted,
                final_resumo=result.get("resumo", ""),
                audio_transcripts=audio_transcripts,
            )
            order_state.save(interaction.channel.id, state)

            # Posta análise — se as tags não vieram, posta o raw para não silenciar
            if result["análise"]:
                await self._send_chunked(interaction.channel, result["análise"])
            elif result["raw"]:
                await self._send_chunked(interaction.channel, result["raw"])

            # Posta resumo para arte
            if result.get("resumo"):
                await interaction.channel.send("─" * 40 + "\n" + result["resumo"])

            # Posta perguntas
            has_questions = (
                result["perguntas"]
                and result["perguntas"] != "Nenhuma — briefing completo."
            )
            if has_questions:
                await interaction.channel.send(
                    "─" * 40 + "\n**❓ Perguntas para o atendimento:**\n\n"
                    + result["perguntas"]
                )

            # Encaminhamento automático para análise desativado — usar /analisar manualmente

            # Se já completou na primeira rodada
            if result["status"] == "FINALIZAR":
                await self._deliver_final(interaction.channel, state)
            elif has_questions:
                await self._send_call_button(interaction.channel)

        except Exception as exc:
            logger.error(f"Erro no /briefing: {exc}", exc_info=True)
            await interaction.channel.send(
                f"❌ Erro ao processar os materiais: `{exc}`\nTente novamente."
            )

    # ── on_message: resposta do atendimento ──────────────────────────────────

    async def handle_response(self, message: discord.Message) -> None:
        """Processa uma resposta do atendimento durante o questionário."""
        state = order_state.get(message.channel.id)
        if not state or state.stage != "questionnaire":
            return

        try:
            # Adiciona resposta à conversa
            state.conversation.append(
                {"role": "user", "content": message.content}
            )

            async with message.channel.typing():
                result = await self.ai.briefing_turn(state.conversation)

            state.conversation.append({"role": "assistant", "content": result["raw"]})

            if result["análise"]:
                await self._send_chunked(message.channel, result["análise"])
            elif result["raw"]:
                await self._send_chunked(message.channel, result["raw"])

            if result.get("resumo"):
                state.final_resumo = result["resumo"]
                await message.channel.send("─" * 40 + "\n" + result["resumo"])

            has_questions = (
                result["perguntas"]
                and result["perguntas"] != "Nenhuma — briefing completo."
            )
            if has_questions:
                await message.channel.send(
                    "─" * 40 + "\n**❓ Perguntas:**\n\n" + result["perguntas"]
                )

            if result["status"] == "FINALIZAR":
                await self._deliver_final(message.channel, state)
            elif has_questions:
                await self._send_call_button(message.channel)

        except Exception as exc:
            logger.error(f"Erro ao processar resposta: {exc}", exc_info=True)
            await message.channel.send(f"❌ Erro ao processar resposta: `{exc}`")

    # ── /finalizar ────────────────────────────────────────────────────────────

    async def finalize(self, interaction: discord.Interaction) -> None:
        """Etapa 4: gera briefing final + ZIP e encerra o pedido."""
        try:
            await interaction.response.defer()
        except discord.errors.HTTPException:
            return

        state = order_state.get(interaction.channel.id)
        if not state:
            await interaction.followup.send(
                "❌ Nenhum briefing ativo neste canal. Use **/briefing** primeiro."
            )
            return

        try:
            await interaction.followup.send("📦 Gerando pacote final — aguarde...")
            await self._deliver_final(interaction.channel, state)

        except Exception as exc:
            logger.error(f"Erro no /finalizar: {exc}", exc_info=True)
            await interaction.channel.send(f"❌ Erro ao finalizar: `{exc}`")

    # ── chamado para arte finalista ───────────────────────────────────────────

    async def _send_call_button(self, channel: discord.TextChannel) -> None:
        """Envia o botão discreto de chamado após cada rodada de perguntas."""
        view = ArtistCallView(self, channel.id)
        await channel.send(
            "_Se precisar acionar a arte finalista:_",
            view=view,
        )

    async def send_artist_call(
        self,
        briefing_channel: discord.TextChannel,
        state: OrderState,
        image_message,  # Optional[discord.Message]
    ) -> None:
        """Posta o chamado de arte no #geral e limpa o pending_call."""
        if not self.config.GERAL_CHANNEL_ID:
            await briefing_channel.send(
                "❌ Canal #geral não configurado (GERAL_CHANNEL_ID). Chamado não enviado."
            )
            return

        geral_channel = briefing_channel.guild.get_channel(int(self.config.GERAL_CHANNEL_ID))
        if not geral_channel:
            await briefing_channel.send("❌ Canal #geral não encontrado. Chamado não enviado.")
            return

        call = state.pending_call
        state.pending_call = None
        order_state.save(briefing_channel.id, state)

        # Entendimento do Cyan: resumo mais recente da conversa
        from utils.openai_client import _extract_tag
        entendimento = state.final_resumo
        if not entendimento:
            for msg in reversed(state.conversation):
                if msg["role"] == "assistant":
                    resumo = _extract_tag(msg["content"], "RESUMO")
                    if resumo:
                        entendimento = resumo
                        break
        if not entendimento:
            for msg in reversed(state.conversation):
                if msg["role"] == "assistant":
                    analise = _extract_tag(msg["content"], "ANÁLISE")
                    if analise:
                        entendimento = analise[:800]
                        break
        if not entendimento:
            entendimento = "Briefing em andamento — contexto ainda não consolidado."

        if len(entendimento) > 800:
            entendimento = entendimento[:800] + "\n_[resumo truncado]_"

        raiza_id = self.config.RAIZA_DISCORD_ID
        mention = f"<@{raiza_id}>" if raiza_id else "@raiza_93431"

        texto = (
            f"🔔 **Chamado de arte** — Pedido **{state.order_number}** · {state.client}\n\n"
            f"**Problema relatado pelo atendimento ({call.user_display_name}):**\n"
            f"{call.problem_text}\n\n"
            f"**Entendimento do Cyan:**\n"
            f"{entendimento}"
        )

        files = []
        if image_message and image_message.attachments:
            att = image_message.attachments[0]
            try:
                import aiohttp
                import io
                async with aiohttp.ClientSession() as session:
                    async with session.get(att.url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            files = [discord.File(io.BytesIO(data), filename=att.filename)]
            except Exception as exc:
                logger.warning(f"Não foi possível baixar imagem do chamado: {exc}")

        if files:
            await geral_channel.send(f"{mention}\n{texto}", files=files)
        else:
            await geral_channel.send(f"{mention}\n{texto}")

        await briefing_channel.send(
            "✅ Arte finalista acionada. O chamado foi enviado para o **#geral**."
        )

    # ── entrega final ─────────────────────────────────────────────────────────

    async def _deliver_final(
        self, channel: discord.TextChannel, state: OrderState
    ) -> None:
        """Posta o briefing final como texto. ZIP e pacote de arquivos pausados."""
        from utils.openai_client import _extract_tag

        # Usa a última análise completa como briefing final
        last_analysis = ""
        for msg in reversed(state.conversation):
            if msg["role"] == "assistant":
                last_analysis = _extract_tag(msg["content"], "ANÁLISE")
                if last_analysis:
                    break

        state.final_briefing = last_analysis or state.conversation[-1].get("content", "")

        # Garante que o resumo mais recente da conversa esteja salvo
        if not state.final_resumo:
            for msg in reversed(state.conversation):
                if msg["role"] == "assistant":
                    resumo = _extract_tag(msg["content"], "RESUMO")
                    if resumo:
                        state.final_resumo = resumo
                        break

        # Se ainda não há resumo, faz uma chamada final ao GPT para fechar o briefing
        if not state.final_resumo:
            try:
                await channel.send("🔄 Consolidando briefing final — aguarde...")
                final_result = await self.ai.generate_final_briefing(state.conversation)
                if final_result.get("resumo"):
                    state.final_resumo = final_result["resumo"]
                if final_result.get("análise"):
                    state.final_briefing = final_result["análise"]
                state.conversation.append({"role": "assistant", "content": final_result["raw"]})
            except Exception as exc:
                logger.error(f"Erro ao gerar briefing final via GPT: {exc}", exc_info=True)

        state.stage = "complete"
        order_state.save(channel.id, state)

        await channel.send(
            f"✅ **Briefing finalizado!**\n"
            f"Cliente: **{state.client}** | Pedido: **{state.order_number}**\n"
            f"{'─' * 40}"
        )

        if state.final_briefing:
            await self._send_chunked(channel, state.final_briefing)

        if state.final_resumo:
            await channel.send("─" * 40 + "\n" + state.final_resumo)

        await channel.send(
            "_Use **/limpar** para apagar as mensagens e liberar o canal para o próximo pedido._"
        )

    # ── coleta de mensagens ───────────────────────────────────────────────────

    async def _collect_messages(self, channel: discord.TextChannel) -> list[discord.Message]:
        messages = []
        async for msg in channel.history(
            limit=self.config.MAX_MESSAGES_TO_COLLECT, oldest_first=True
        ):
            if not msg.author.bot:
                messages.append(msg)
        return messages

    # ── monta payload multimodal ──────────────────────────────────────────────

    async def _build_content(
        self, messages: list[discord.Message]
    ) -> tuple[list[dict], dict[str, FileRecord], list[tuple[str, str]]]:
        """
        Retorna (content_parts, file_records, audio_transcripts).
        audio_transcripts: lista de (filename, transcript_text) para exibição no Discord.
        Estratégia: textos primeiro (definem intenção), arquivos depois (confirmam e complementam).
        """
        from utils.file_processor import SUPPORTED_AUDIO, FileProcessor as _FP

        records: dict[str, FileRecord] = {}
        audio_transcripts: list[tuple[str, str]] = []

        # ── Passo 1: coleta todos os textos em ordem cronológica ──────────────
        text_parts: list[dict] = [
            {"type": "text", "text": (
                "=== TEXTO DO ATENDIMENTO — leia e interprete ANTES de analisar os arquivos ===\n"
                "O texto define a intenção do pedido. Os arquivos confirmam e complementam.\n"
            )}
        ]
        file_parts: list[dict] = [
            {"type": "text", "text": "\n=== ARQUIVOS RECEBIDOS ===\n"}
        ]

        import re
        DRIVE_PATTERN = re.compile(r'https?://drive\.google\.com/\S+')

        for msg in messages:
            ts = msg.created_at.strftime("%d/%m/%Y %H:%M")

            if msg.content:
                text_parts.append({
                    "type": "text",
                    "text": f"[{msg.author.display_name} — {ts}]:\n{msg.content}\n",
                })

                # Detecta links do Google Drive no texto e processa como arquivo
                for drive_url in DRIVE_PATTERN.findall(msg.content):
                    file_parts.append({
                        "type": "text",
                        "text": f"[Link Google Drive enviado por {msg.author.display_name}]: {drive_url}",
                    })
                    try:
                        parts, filename = await self.file_processor.process_from_drive(drive_url)
                        file_parts.extend(parts)
                        file_type = await self.ai.classify_file(parts, filename)
                        records[filename] = FileRecord(
                            filename=filename,
                            url=drive_url,
                            file_type=file_type,
                        )
                    except Exception as exc:
                        logger.error(f"Erro ao processar link Drive {drive_url}: {exc}")
                        file_parts.append({"type": "text", "text": f"⚠️ Erro ao processar link do Drive: {exc}"})

            for att in msg.attachments:
                size_kb = att.size / 1024
                file_parts.append({
                    "type": "text",
                    "text": f"[Arquivo: {att.filename} ({size_kb:.0f} KB) — enviado por {msg.author.display_name}]",
                })

                parts = await self.file_processor.process_attachment(att)
                file_parts.extend(parts)

                # Transcrição de áudio vai ao GPT mas não ao Discord (prévia pausada)
                ext = _FP._extension(att.filename)
                if ext in SUPPORTED_AUDIO and parts:
                    part_text = parts[0].get("text", "")
                    transcript_text = part_text.split("\n\n", 1)[-1] if "\n\n" in part_text else part_text
                    audio_transcripts.append((att.filename, transcript_text))

                # Classificação pausada (ZIP desativado) — registra arquivo sem tipo
                records[att.filename] = FileRecord(
                    filename=att.filename,
                    url=att.url,
                    file_type="undefined",
                )

        # ── Passo 2: monta payload com texto primeiro, arquivos depois ─────────
        content = text_parts + file_parts
        return content, records, audio_transcripts

    # ── encaminha arquivos de produção ────────────────────────────────────────

    async def _forward_production_files(
        self, interaction: discord.Interaction, state: OrderState
    ) -> None:
        """Encaminha todos os arquivos do pedido para #análise-de-arquivos."""
        analysis_ch = interaction.guild.get_channel(int(self.config.ANALYSIS_CHANNEL_ID))
        if not analysis_ch:
            return

        all_files = list(state.files.values())
        if not all_files:
            return

        order_tag = f"{state.order_number} · {state.client}"

        await analysis_ch.send(
            f"📎 **Arquivos do pedido [{order_tag}]**\n"
            f"Canal de origem: {interaction.channel.mention}"
        )

        if self.analysis_handler:
            import asyncio
            for rec in all_files:
                await self.analysis_handler.analyze_url(
                    analysis_ch,
                    rec.url,
                    rec.filename,
                    order_tag,
                    state.order_number,
                )
                await asyncio.sleep(3)
        else:
            for rec in all_files:
                await analysis_ch.send(f"• **{rec.filename}**: {rec.url}")

    # ── exibição de transcrição de áudio ─────────────────────────────────────

    async def _post_audio_preview(
        self, channel: discord.TextChannel, filename: str, transcript: str
    ) -> None:
        """
        Posta a transcrição completa + pontos principais de um áudio no canal,
        para o atendimento verificar antes do briefing ser gerado.
        """
        try:
            # Pontos principais via GPT (chamada rápida, max 300 tokens)
            pontos = await self.ai.summarize_audio_transcript(transcript)

            # Transcrição pode ser longa — limita para caber numa mensagem Discord
            MAX_TRANSCRIPT = 1200
            transcript_display = transcript
            if len(transcript) > MAX_TRANSCRIPT:
                transcript_display = transcript[:MAX_TRANSCRIPT] + "\n_[... transcrição truncada — texto completo enviado ao GPT]_"

            separador = "─" * 40
            msg = (
                f"🎙️ **Áudio transcrito: `{filename}`**\n"
                f"{separador}\n"
                f"📝 **Transcrição:**\n{transcript_display}\n\n"
                f"💡 **Pontos principais:**\n{pontos}\n"
                f"{separador}\n"
                f"_Confira a transcrição acima. Se algo estiver errado, corrija antes de prosseguir._"
            )

            await self._send_chunked(channel, msg)

        except Exception as exc:
            logger.error(f"Erro ao postar prévia de áudio {filename}: {exc}", exc_info=True)
            await channel.send(
                f"🎙️ Áudio `{filename}` transcrito — não foi possível gerar o resumo: `{exc}`"
            )

    # ── envio em chunks ───────────────────────────────────────────────────────

    async def _send_chunked(self, channel: discord.TextChannel, text: str) -> None:
        if len(text) <= DISCORD_MAX:
            await channel.send(text)
            return

        chunks: list[str] = []
        current = ""
        for line in text.split("\n"):
            candidate = (current + "\n" + line) if current else line
            if len(candidate) > DISCORD_MAX and current:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)

        for chunk in chunks:
            await channel.send(chunk)
