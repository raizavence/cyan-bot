from __future__ import annotations
import io
import zipfile
import aiohttp
from utils.order_state import OrderState


async def build(state: OrderState) -> io.BytesIO:
    """Gera o ZIP final do briefing em memória e retorna BytesIO."""
    buf = io.BytesIO()
    base = _safe_name(state.client)
    pkg = f"@arquivos_{state.order_number}"

    all_files = list(state.files.values())

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── briefing.txt ────────────────────────────────────────────────────
        audio_section = ""
        for filename, transcript in getattr(state, "audio_transcripts", []):
            audio_section += f"🎙️ ÁUDIO: {filename}\n{transcript}\n\n"
        if audio_section:
            audio_section += "─" * 40 + "\n\n"

        resumo_section = ""
        if state.final_resumo:
            resumo_section = "\n\n" + "─" * 40 + "\n" + state.final_resumo

        zf.writestr(
            f"{base}/{pkg}/briefing.txt",
            audio_section + (state.final_briefing or "(briefing não finalizado)") + resumo_section,
        )

        # ── arquivos separados por tipo ──────────────────────────────────────
        for f in all_files:
            folder = _classify_folder(f.filename, f.file_type)
            await _add_file(zf, f"{base}/{pkg}/{folder}/{f.filename}", f.url)

    buf.seek(0)
    return buf


def zip_filename(client: str) -> str:
    return f"{_safe_name(client)}_Briefing_Cyan.zip"


# ── helpers ───────────────────────────────────────────────────────────────────

def _classify_folder(filename: str, file_type: str) -> str:
    """Determina a subpasta de destino dentro do pacote do pedido."""
    low = filename.lower()
    ext = ("." + low.rsplit(".", 1)[-1]) if "." in low else ""

    # Modelo de aprovação — sempre vai para Referencia/Modelos de Aprovacao
    if "modelo_aprovacao" in low or "modelo aprovacao" in low or "aprovacao" in low:
        return "Referencia/Modelos de Aprovacao"

    # Referências visuais
    if file_type == "reference":
        return "Referencia"

    # Arquivos de produção classificados pelo GPT — separados por categoria
    if file_type == "production":
        if any(kw in low for kw in ["logo", "logotipo", "marca"]):
            return "Logo"
        if any(kw in low for kw in ["qr", "qrcode"]):
            return "QR Code"
        if any(kw in low for kw in ["ean", "barras", "barcode"]):
            return "Codigo de Barras"
        if any(kw in low for kw in ["nutri", "tabela"]):
            return "Tabela Nutricional"
        return "Producao"

    # Fallback por extensão para arquivos não classificados pelo GPT
    if ext in (".ai", ".eps", ".ps", ".cdr", ".svg", ".fh"):
        # Vetoriais quase sempre são logos/produção
        if any(kw in low for kw in ["logo", "logotipo", "marca"]):
            return "Logo"
        return "Producao"

    if ext in (".psd", ".psb", ".tif", ".tiff"):
        return "Producao"

    if ext == ".pdf":
        if "aprovacao" in low:
            return "Referencia/Modelos de Aprovacao"
        if any(kw in low for kw in ["logo", "logotipo", "marca"]):
            return "Logo"
        return "Producao"

    if ext in (".mp3", ".mp4", ".wav", ".ogg", ".m4a", ".webm"):
        return "Audios"

    return "Outros"


async def _add_file(zf: zipfile.ZipFile, path: str, url: str) -> None:
    import logging
    log = logging.getLogger("cyan.zip")
    try:
        data = await _download(url)
        zf.writestr(path, data)
        log.info(f"ZIP: adicionado {path} ({len(data)//1024}KB)")
    except Exception as exc:
        log.error(f"ZIP: falha ao baixar {path} — {exc}")
        zf.writestr(path + ".ERRO_download.txt", f"Erro ao baixar: {exc}\nURL: {url}")


async def _download(url: str) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CyanBot/1.0)"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, allow_redirects=True) as resp:
            resp.raise_for_status()
            return await resp.read()


def _safe_name(name: str) -> str:
    return (
        "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()
        or "Cliente"
    )
