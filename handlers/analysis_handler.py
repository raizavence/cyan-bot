from __future__ import annotations
import logging
import discord
from config import Config
from utils.openai_client import OpenAIClient
from utils.file_processor import FileProcessor
from utils import order_state

logger = logging.getLogger("cyan.analysis")


# ─────────────────────────────────────────────────────────────────────────────
# View com botões interativos por arquivo
# ─────────────────────────────────────────────────────────────────────────────

class FileAnalysisView(discord.ui.View):
    def __init__(
        self,
        order_number: str,
        filename: str,
        briefing_channel_id: int,
        bot: discord.ext.commands.Bot,
    ):
        super().__init__(timeout=86400 * 7)  # 7 dias
        self.order_number = order_number
        self.filename = filename
        self.briefing_channel_id = briefing_channel_id
        self.bot = bot

    @discord.ui.button(label="📎 Enviar novo arquivo", style=discord.ButtonStyle.primary)
    async def btn_new_file(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Sinaliza que o atendimento vai enviar arquivo substituto."""
        result = order_state.find_by_order(self.order_number)
        if result:
            _, state = result
            state.pending_replacement = (self.filename, interaction.message.id)

        await interaction.response.send_message(
            f"📎 Envie o arquivo substituto para **`{self.filename}`** neste canal.\n"
            "Vou analisar automaticamente assim que receber.",
            ephemeral=True,
        )

    @discord.ui.button(label="⚠️ Seguir com o que tem", style=discord.ButtonStyle.secondary)
    async def btn_proceed(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Registra decisão de seguir com o arquivo atual e atualiza o briefing."""
        result = order_state.find_by_order(self.order_number)
        if result:
            ch_id, state = result
            if self.filename in state.files:
                state.files[self.filename].status = "proceeding"

            # Notifica o canal de briefing
            briefing_ch = self.bot.get_channel(ch_id)
            if briefing_ch:
                await briefing_ch.send(
                    f"📎 **{self.filename}** — arquivo único disponível.\n"
                    f"Decisão: ⚠️ seguir com o que tem. "
                    f"_(registrado por {interaction.user.display_name})_"
                )

        # Desabilita botões após decisão
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message(
            f"✅ Registrado. Seguindo com **`{self.filename}`**.", ephemeral=True
        )


# ─────────────────────────────────────────────────────────────────────────────
# Handler principal
# ─────────────────────────────────────────────────────────────────────────────

class AnalysisHandler:
    def __init__(self, config: Config):
        self.config = config
        self.ai = OpenAIClient(
            api_key=config.OPENAI_API_KEY,
            model=config.GPT_MODEL,
            max_tokens=1024,
        )
        self.file_processor = FileProcessor(
            max_size_mb=config.MAX_FILE_SIZE_MB,
            pdf_max_pages=config.PDF_MAX_PAGES,
            pdf_dpi=config.PDF_DPI,
            openai_api_key=config.OPENAI_API_KEY,
        )
        # Injetado pelo main.py após criação do bot
        self.bot: discord.ext.commands.Bot | None = None

    # ── análise automática (on_message) ──────────────────────────────────────

    async def handle_automatic(self, message: discord.Message) -> None:
        """Analisa arquivos enviados diretamente no canal de análise."""
        # Verifica se há substituição pendente para este canal
        state_data = self._find_pending_replacement(message.channel.id)

        for att in message.attachments:
            if state_data:
                ch_id, state, original_filename = state_data
                order_tag = f"{state.order_number} · {state.client}"
                await self.analyze_url(
                    message.channel, att.url, att.filename, order_tag, state.order_number
                )
                # Atualiza estado: substitui registro do arquivo original
                if original_filename in state.files:
                    old = state.files.pop(original_filename)
                    state.files[att.filename] = old.__class__(
                        filename=att.filename,
                        url=att.url,
                        file_type=old.file_type,
                        status="pending",
                    )
                state.pending_replacement = None

                # Notifica canal de briefing
                briefing_ch = self.bot.get_channel(ch_id) if self.bot else None
                if briefing_ch:
                    await briefing_ch.send(
                        f"📎 Arquivo substituto recebido para **{original_filename}** "
                        f"→ **{att.filename}**. Análise em andamento no canal de arquivos."
                    )
            else:
                await self._analyze(message.channel, att, order_tag="—", order_number=None)

    # ── /analisar ─────────────────────────────────────────────────────────────

    async def handle_command(self, interaction: discord.Interaction) -> None:
        """Analisa os arquivos mais recentes via /analisar."""
        await interaction.response.defer()

        try:
            attachments: list[tuple[discord.Message, discord.Attachment]] = []
            async for msg in interaction.channel.history(limit=20):
                if not msg.author.bot:
                    for att in msg.attachments:
                        attachments.append((msg, att))

            if not attachments:
                await interaction.followup.send(
                    "❌ Nenhum arquivo encontrado nas últimas 20 mensagens."
                )
                return

            await interaction.followup.send(
                f"🔍 Analisando **{len(attachments)}** arquivo(s)..."
            )

            import asyncio
            for _msg, att in attachments:
                await self._analyze(interaction.channel, att, "—", None)
                await asyncio.sleep(3)

        except Exception as exc:
            logger.error(f"Erro no /analisar: {exc}", exc_info=True)
            await interaction.channel.send(f"❌ Erro durante a análise: `{exc}`")

    # ── análise via URL (chamada pelo briefing_handler) ───────────────────────

    async def analyze_url(
        self,
        channel: discord.TextChannel,
        url: str,
        filename: str,
        order_tag: str,
        order_number: str | None,
    ) -> None:
        """Analisa um arquivo pela URL e posta resultado com botões."""
        try:
            view = None
            if order_number and self.bot:
                view = FileAnalysisView(
                    order_number=order_number,
                    filename=filename,
                    briefing_channel_id=int(self.config.BRIEFING_CHANNEL_ID),
                    bot=self.bot,
                )

            async with channel.typing():
                parts = await self.file_processor.process_from_url(url, filename)
                result = await self.ai.analyze_file(parts, filename, order_tag)
                await channel.send(result, view=view)

        except Exception as exc:
            logger.error(f"Erro ao analisar {filename}: {exc}", exc_info=True)
            await channel.send(f"❌ Erro ao analisar **{filename}**: `{exc}`")

    # ── análise de attachment direto ──────────────────────────────────────────

    async def _analyze(
        self,
        channel: discord.TextChannel,
        attachment: discord.Attachment,
        order_tag: str,
        order_number: str | None,
    ) -> None:
        try:
            view = None
            if order_number and self.bot:
                view = FileAnalysisView(
                    order_number=order_number,
                    filename=attachment.filename,
                    briefing_channel_id=int(self.config.BRIEFING_CHANNEL_ID),
                    bot=self.bot,
                )

            async with channel.typing():
                parts = await self.file_processor.process_attachment(attachment)

                if len(parts) == 1 and "excede" in parts[0].get("text", ""):
                    await channel.send(parts[0]["text"])
                    return

                result = await self.ai.analyze_file(parts, attachment.filename, order_tag)
                await channel.send(result, view=view)

        except Exception as exc:
            logger.error(f"Erro ao analisar {attachment.filename}: {exc}", exc_info=True)
            await channel.send(
                f"❌ Erro ao analisar **{attachment.filename}**: `{exc}`"
            )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _find_pending_replacement(
        self, analysis_channel_id: int
    ) -> tuple[int, object, str] | None:
        """Procura pedido com substituição de arquivo pendente."""
        from utils.order_state import _store
        for ch_id, state in _store.items():
            if state.pending_replacement is not None:
                original_filename, _ = state.pending_replacement
                return ch_id, state, original_filename
        return None
