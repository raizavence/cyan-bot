import logging
import sys
from datetime import timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from config import Config
from handlers.briefing_handler import BriefingHandler
from handlers.briefing_v2 import BriefingV2Handler
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

# ── handlers ──────────────────────────────────────────────────────────────────
briefing_handler = BriefingHandler(config)
analysis_handler = AnalysisHandler(config)
briefing_v2_handler = BriefingV2Handler(config, briefing_handler)

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

    # Só posta se o bot não enviou mensagem recente no canal (últimas 50)
    try:
        async for m in channel.history(limit=50):
            if m.author.id == bot.user.id:
                return
    except Exception as exc:
        logger.warning(f"Não foi possível verificar histórico do Geral: {exc}")
        return

    texto = (
        "Olá! Sou o **Cyan 🤓** — assistente Pré-Arte da Copack, treinado no processo de arte final.\n\n"
        "Estou aqui para garantir que o setor de Arte receba tudo que precisa, sem informação faltando e sem retrabalho.\n\n"
        "─────────────────────────────────\n"
        "⚠️ **Estamos em fase de testes**\n"
        "Por enquanto, use apenas o canal **#briefing-teste** para experimentar o fluxo. "
        "O canal de produção será liberado em breve.\n"
        "─────────────────────────────────\n\n"
        "**Como usar o canal de briefing:**\n"
        "1. Vá para **#briefing-teste**\n"
        "2. Envie todos os materiais do pedido — prints do WhatsApp, imagens, PDFs, arquivos do cliente\n"
        "3. Use `/briefing` — eu analiso tudo e faço as perguntas que precisam ser respondidas antes de ir para a Arte\n"
        "4. Responda minhas perguntas no chat\n"
        "5. Use `/finalizar` — o briefing completo é postado no canal\n"
        "6. Use `/limpar` para zerar o canal e receber o próximo pedido\n\n"
        "**Regras do canal:**\n"
        "• Um pedido por vez\n"
        "• Sem `/briefing` ativo, mensagens são ignoradas\n"
        "• Sessão expira automaticamente após 2 horas sem atividade\n\n"
        "Dúvidas sobre arquivos ou fluxo? Use `/ajuda` em qualquer canal."
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


async def _post_teste_pin_if_needed() -> None:
    if not config.TEST_BRIEFING_CHANNEL_ID:
        return
    channel = bot.get_channel(int(config.TEST_BRIEFING_CHANNEL_ID))
    if not channel:
        return
    try:
        async for m in channel.pins():
            if m.author.id == bot.user.id:
                return  # já tem pin
    except Exception as exc:
        logger.warning(f"Não foi possível verificar pins no canal de teste: {exc}")
        return

    texto = (
        "**Canal de teste do Cyan 🧪**\n\n"
        "Por enquanto, todos os briefings devem ser feitos aqui.\n\n"
        "**Fluxo:**\n"
        "1. Envie os materiais do pedido (prints, imagens, PDFs, arquivos)\n"
        "2. Use `/briefing` — eu analiso tudo e faço as perguntas necessárias\n"
        "3. Responda as perguntas no chat\n"
        "4. Use `/finalizar` — briefing completo postado no canal\n"
        "5. Use `/limpar` — canal zerado para o próximo pedido"
    )
    try:
        msg = await channel.send(texto)
        logger.info("Mensagem de orientação postada no #briefing-teste")
    except Exception as exc:
        logger.error(f"Erro ao postar mensagem no canal de teste: {exc}")
        return
    try:
        await msg.pin()
        logger.info("Mensagem de orientação fixada no #briefing-teste")
    except discord.Forbidden:
        logger.warning("Sem permissão para fixar no canal de teste — pin manual necessário")


async def _avisar_se_briefing_em_andamento() -> None:
    """Pós-restart: avisa no canal de briefing sobre briefing persistido ou recente."""
    if not config.BRIEFING_CHANNEL_ID:
        return
    channel = bot.get_channel(int(config.BRIEFING_CHANNEL_ID))
    if not channel:
        return

    if config.CYAN_FLOW == "v2":
        row = order_state.get_v2_raw(int(config.BRIEFING_CHANNEL_ID))
        if row:
            from utils.briefing_schema import pedido_from_json
            pedido = pedido_from_json(row[0])
            # Nota: Views/botões do discord.py não sobrevivem a restart; estado sim.
            await channel.send(
                f"♻️ Reiniciei — o briefing do pedido **{pedido.numero_omie or '?'}** "
                "continua ativo, pode seguir respondendo."
            )
        return

    # v1
    state = order_state.get(int(config.BRIEFING_CHANNEL_ID))
    if state:
        # Nota: Views/botões do discord.py não sobrevivem a restart; estado sim.
        await channel.send(
            f"♻️ Reiniciei — o briefing do pedido **{state.order_number}** continua ativo, "
            "pode seguir respondendo."
        )
        return

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


async def _anunciar_v2_se_necessario() -> None:
    """Posta no #geral uma única vez quando CYAN_FLOW=v2 é ativado."""
    if config.CYAN_FLOW != "v2":
        return
    marker = Path("/root/cyan-bot/.v2-announced")
    if marker.exists():
        return
    if not config.GERAL_CHANNEL_ID:
        return
    channel = bot.get_channel(int(config.GERAL_CHANNEL_ID))
    if not channel:
        return
    try:
        await channel.send(
            "🔄 **Atualização do Cyan:** o canal **#briefing-do-pedido** agora usa "
            "o pipeline v2. O fluxo de uso continua exatamente igual — a diferença: "
            "arquivos enviados **durante o questionário** agora entram automaticamente "
            "na análise, sem precisar de **/briefing** de novo."
        )
        marker.touch()
        logger.info("Pipeline v2 anunciado no #geral")
    except Exception as exc:
        logger.warning(f"Não foi possível anunciar v2 no #geral: {exc}")


async def _avisar_v2_se_briefing_em_andamento() -> None:
    """Pós-restart: avisa no canal de teste se havia briefing v2 persistido."""
    if not config.TEST_BRIEFING_CHANNEL_ID:
        return
    channel = bot.get_channel(int(config.TEST_BRIEFING_CHANNEL_ID))
    if not channel:
        return
    row = order_state.get_v2_raw(int(config.TEST_BRIEFING_CHANNEL_ID))
    if not row:
        return
    from utils.briefing_schema import pedido_from_json
    pedido = pedido_from_json(row[0])
    try:
        await channel.send(
            f"♻️ [v2] Reiniciei — briefing do pedido **{pedido.numero_omie or '?'}** "
            "continua ativo no canal de teste, pode seguir respondendo."
        )
    except Exception as exc:
        logger.warning(f"Não foi possível avisar canal de teste pós-restart: {exc}")


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
        guild = discord.Object(id=1521587717933236305)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"Sincronizados {len(synced)} comando(s) slash no servidor")
    except Exception as exc:
        logger.error(f"Erro ao sincronizar comandos: {exc}")
    await _post_presentation_if_needed()
    await _post_teste_pin_if_needed()
    await _avisar_se_briefing_em_andamento()
    await _avisar_v2_se_briefing_em_andamento()
    await _anunciar_v2_se_necessario()


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    ch_id = str(message.channel.id)

    # Canal de análise: pausado temporariamente
    if ch_id == config.ANALYSIS_CHANNEL_ID:
        if message.attachments:
            await message.channel.send(
                "⏸️ Este canal está temporariamente pausado. Use **#briefing-teste** por enquanto.",
                delete_after=10,
            )
        return

    # ── Canal de teste (#briefing-teste) — pipeline v2 ────────────────────────
    if config.TEST_BRIEFING_CHANNEL_ID and ch_id == config.TEST_BRIEFING_CHANNEL_ID:
        row = order_state.get_v2_raw(message.channel.id)
        _FASES_ATIVAS = ("questionnaire", "checklist", "confirmacao", "observacao")
        if row and row[2] in _FASES_ATIVAS:
            if message.attachments:
                await briefing_v2_handler.handle_attachment(message)
            else:
                await briefing_v2_handler.handle_response(message)
            return
        if not row:
            await message.channel.send(
                "Não há briefing v2 ativo. Use **/briefing** para iniciar.",
                delete_after=8,
            )
        return  # sem bot.process_commands — canal de teste só aceita slash commands

    # ── Canal de briefing de produção — pausado temporariamente ──────────────
    if ch_id == config.BRIEFING_CHANNEL_ID:
        await message.channel.send(
            "⏸️ Este canal está temporariamente pausado. Use **#briefing-teste** por enquanto.",
            delete_after=10,
        )
        return

    await bot.process_commands(message)


# ── slash commands ────────────────────────────────────────────────────────────

@bot.tree.command(
    name="briefing",
    description="Analisa os materiais do pedido e inicia o questionário de briefing",
)
async def cmd_briefing(interaction: discord.Interaction) -> None:
    ch_id = str(interaction.channel_id)
    if config.TEST_BRIEFING_CHANNEL_ID and ch_id == config.TEST_BRIEFING_CHANNEL_ID:
        await briefing_v2_handler.handle(interaction)
        return
    if ch_id == config.BRIEFING_CHANNEL_ID:
        await interaction.response.send_message(
            "⏸️ Este canal está temporariamente pausado. Use **#briefing-teste** por enquanto.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        "❌ Use este comando no canal **#briefing-teste**.", ephemeral=True
    )


@bot.tree.command(
    name="analisar",
    description="Analisa tecnicamente os arquivos mais recentes neste canal",
)
async def cmd_analisar(interaction: discord.Interaction) -> None:
    if str(interaction.channel_id) == config.ANALYSIS_CHANNEL_ID:
        await interaction.response.send_message(
            "⏸️ Este canal está temporariamente pausado. Use **#briefing-teste** por enquanto.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        "❌ Use este comando no canal **#análise-de-arquivos**.", ephemeral=True
    )


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
        order_state.remove_v2(interaction.channel.id)
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
    ch_id = str(interaction.channel_id)
    if config.TEST_BRIEFING_CHANNEL_ID and ch_id == config.TEST_BRIEFING_CHANNEL_ID:
        await briefing_v2_handler.finalize(interaction)
        return
    if ch_id == config.BRIEFING_CHANNEL_ID:
        await interaction.response.send_message(
            "⏸️ Este canal está temporariamente pausado. Use **#briefing-teste** por enquanto.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        "❌ Use este comando no canal **#briefing-teste**.", ephemeral=True
    )


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
