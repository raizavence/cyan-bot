import logging
import sys
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from config import Config
from handlers.briefing_handler import BriefingHandler
from handlers.analysis_handler import AnalysisHandler
from utils import order_state

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("cyan.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("cyan")

# ── configuração ──────────────────────────────────────────────────────────────
config = Config()
try:
    config.validate()
except ValueError as exc:
    logger.error(str(exc))
    sys.exit(1)

# ── bot ───────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── handlers (recebem referência ao bot) ──────────────────────────────────────
briefing_handler = BriefingHandler(config)
analysis_handler = AnalysisHandler(config)

# Injeção cruzada de referências
analysis_handler.bot = bot
briefing_handler.analysis_handler = analysis_handler


# ── eventos ───────────────────────────────────────────────────────────────────

async def _post_presentation_if_needed() -> None:
    if not config.GERAL_CHANNEL_ID:
        return
    channel = bot.get_channel(int(config.GERAL_CHANNEL_ID))
    if not channel:
        logger.warning("Canal Geral não encontrado — verifique GERAL_CHANNEL_ID")
        return
    try:
        already_pinned = False
        async for m in channel.pins():
            if m.author.id == bot.user.id:
                already_pinned = True
                break
        if already_pinned:
            return
    except Exception as exc:
        logger.warning(f"Não foi possível verificar pins no Geral: {exc}")
        return

    texto = (
        "**Olá, eu sou o Cyan 🤓**\n\n"
        "Sou o assistente Pré-Arte da Copack, treinado no processo de arte final. "
        "Estou aqui pra garantir que o setor de Arte receba "
        "tudo que precisa, sem informação faltando e sem retrabalho.\n\n"
        "**O que faço:**\n"
        "• Leio os materiais enviados pelo atendimento (prints, imagens, PDFs, arquivos)\n"
        "• Monto o briefing completo do pedido com tudo que o setor de Arte precisa\n"
        "• Analiso tecnicamente os arquivos: resolução, modo de cor, adequação para offset\n"
        "• Identifico o que está faltando e faço as perguntas certas antes que vire retrabalho\n\n"
        "**Como usar:**\n"
        "O canal **#briefing-do-pedido** funciona um pedido por vez. O fluxo é:\n"
        "1. Envie os materiais do pedido (prints, imagens, PDFs, arquivos)\n"
        "2. Use `/briefing` — eu analiso tudo e faço as perguntas necessárias\n"
        "3. Responda as perguntas normalmente no chat\n"
        "4. Use `/finalizar` — eu gero o briefing completo e o pacote de arquivos\n"
        "5. Use `/limpar` — o canal é zerado e fica pronto para o próximo pedido\n\n"
        "**Comandos disponíveis:**\n"
        "`/briefing` — inicia a análise dos materiais e o questionário\n"
        "`/finalizar` — encerra o questionário e gera o pacote final\n"
        "`/limpar` — apaga as mensagens do canal e reseta para novo pedido\n"
        "`/analisar` — analisa arquivos manualmente no **#análise-de-arquivos**\n\n"
        "**Este canal — #geral** — é onde vocês relatam erros ou comportamentos inesperados meus. "
        "Cada relato me ajuda a melhorar."
    )
    try:
        msg = await channel.send(texto)
        logger.info("Mensagem de apresentação postada no canal Geral")
    except Exception as exc:
        logger.error(f"Erro ao postar apresentação no Geral: {exc}")
        return
    try:
        await msg.pin()
        logger.info("Mensagem de apresentação fixada no canal Geral")
    except discord.Forbidden:
        logger.warning("Sem permissão para fixar mensagem no Geral — pin manual necessário")


async def _avisar_se_briefing_em_andamento() -> None:
    """Pós-restart: avisa no canal de briefing se havia conversa recente sem estado em memória."""
    if not config.BRIEFING_CHANNEL_ID:
        return
    channel = bot.get_channel(int(config.BRIEFING_CHANNEL_ID))
    if not channel:
        return
    if order_state.get(int(config.BRIEFING_CHANNEL_ID)):
        return  # estado ainda em memória, não precisa avisar
    cutoff = discord.utils.utcnow() - timedelta(hours=8)
    try:
        async for msg in channel.history(limit=20, after=cutoff):
            if not msg.author.bot:
                await channel.send(
                    "♻️ Reiniciei. Se havia um briefing em andamento, use **/briefing** novamente "
                    "— eu recoletarei tudo que está no canal."
                )
                break
    except Exception as exc:
        logger.warning(f"Não foi possível verificar histórico do canal de briefing: {exc}")


@bot.event
async def on_ready() -> None:
    logger.info(f"✅ Cyan online como {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="os briefings da Copack",
        )
    )
    try:
        guild = discord.Object(id=1508828656804561059)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"Sincronizados {len(synced)} comando(s) slash no servidor")
    except Exception as exc:
        logger.error(f"Erro ao sincronizar comandos: {exc}")
    await _post_presentation_if_needed()
    await _avisar_se_briefing_em_andamento()


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    ch_id = str(message.channel.id)

    # Canal de análise: análise automática de arquivos
    if ch_id == config.ANALYSIS_CHANNEL_ID and message.attachments:
        await analysis_handler.handle_automatic(message)
        return  # não processa como resposta de briefing

    # Canal de briefing: captura imagem de chamado pendente ou resposta ao questionário
    if ch_id == config.BRIEFING_CHANNEL_ID:
        state = order_state.get(message.channel.id)
        if state and state.pending_call:
            if (
                message.author.id == state.pending_call.user_id
                and message.attachments
            ):
                await briefing_handler.send_artist_call(message.channel, state, message)
                return
        if state and state.stage == "questionnaire":
            await briefing_handler.handle_response(message)
            return
        if not state and not message.author.bot:
            await message.channel.send(
                "Não há briefing ativo. Use **/briefing** para iniciar a análise.",
                delete_after=8,
            )
            return

    await bot.process_commands(message)


# ── slash commands ────────────────────────────────────────────────────────────

@bot.tree.command(
    name="briefing",
    description="Analisa os materiais do pedido e inicia o questionário de briefing",
)
async def cmd_briefing(interaction: discord.Interaction) -> None:
    if str(interaction.channel_id) != config.BRIEFING_CHANNEL_ID:
        await interaction.response.send_message(
            "❌ Use este comando no canal **#briefing-do-pedido**.", ephemeral=True
        )
        return
    await briefing_handler.handle(interaction)


@bot.tree.command(
    name="analisar",
    description="Analisa tecnicamente os arquivos mais recentes neste canal",
)
async def cmd_analisar(interaction: discord.Interaction) -> None:
    if str(interaction.channel_id) != config.ANALYSIS_CHANNEL_ID:
        await interaction.response.send_message(
            "❌ Use este comando no canal **#análise-de-arquivos**.", ephemeral=True
        )
        return
    await analysis_handler.handle_command(interaction)


@bot.tree.command(
    name="limpar",
    description="Apaga as mensagens do canal e reseta o briefing ativo",
)
async def cmd_limpar(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        total = 0
        while True:
            try:
                deleted = await interaction.channel.purge(limit=100)
            except discord.HTTPException:
                # bulk delete não aceita mensagens > 14 dias — apagar individualmente
                deleted_count = 0
                async for msg in interaction.channel.history(limit=100):
                    try:
                        await msg.delete()
                        deleted_count += 1
                    except Exception:
                        pass
                total += deleted_count
                break
            total += len(deleted)
            if len(deleted) < 100:
                break

        order_state.remove(interaction.channel.id)
        await interaction.followup.send(
            f"🧹 {total} mensagens apagadas. Memória zerada. Canal pronto para novo briefing.",
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Sem permissão para apagar mensagens. Dê ao bot a permissão **Gerenciar Mensagens**.",
            ephemeral=True,
        )


@bot.tree.command(
    name="ajuda",
    description="Tire dúvidas com o Cyan sobre arte final, arquivos e fluxo de briefing",
)
@app_commands.describe(pergunta="O que você quer saber?")
async def cmd_ajuda(interaction: discord.Interaction, pergunta: str) -> None:
    await interaction.response.defer(ephemeral=True)
    resposta = await briefing_handler.ai.answer_question(pergunta)
    await interaction.followup.send(f"🤓 **Cyan responde:**\n\n{resposta}", ephemeral=True)


@bot.tree.command(
    name="finalizar",
    description="Encerra o questionário e gera o pacote final (briefing + ZIP)",
)
async def cmd_finalizar(interaction: discord.Interaction) -> None:
    if str(interaction.channel_id) != config.BRIEFING_CHANNEL_ID:
        await interaction.response.send_message(
            "❌ Use este comando no canal **#briefing-do-pedido**.", ephemeral=True
        )
        return
    await briefing_handler.finalize(interaction)


# ── erros globais ─────────────────────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    logger.error(f"Erro em comando slash: {error}", exc_info=True)
    msg = f"❌ Erro inesperado: `{error}`"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# ── entrada ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Iniciando Cyan...")
    bot.run(config.DISCORD_TOKEN)
