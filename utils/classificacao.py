"""
Módulo CY6.2 — Classificação canônica de arquivo (DESLIGADO em produção até CY6.4).
Consolida as regras de classify_file (openai_client.py) e _classify_folder
(zip_generator.py). Não importado pelos handlers da v1.

Divergências resolvidas (registradas aqui conforme PLANO):
- .tif/.tiff: _classify_folder → "Producao", classify_file não tratava → producao
- imagem ambígua sem pista no nome: CY4 retornava "undefined" estático;
  aqui usa LLM mini-prompt (gpt-4o-mini) quando cliente disponível
"""
from __future__ import annotations
from typing import Optional

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_VECTOR_EXTS = {".ai", ".eps", ".cdr", ".svg", ".fh", ".ps"}
_RASTER_PROD_EXTS = {".psd", ".psb", ".tif", ".tiff"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".webm", ".mp4"}
_TEXT_EXTS = {".txt", ".csv", ".rtf", ".md"}

_CLASSIFY_MINI_SYSTEM = (
    "Você é o Cyan, assistente de arte final da Copack.\n"
    "Classifique o arquivo em uma categoria:\n"
    "  PRODUCAO   → logo, QR Code, código de barras, tabela nutricional, arte final\n"
    "  REFERENCIA → foto de produto, embalagem, mockup, arte de IA, inspiração\n"
    "  INDEFINIDO → não é possível determinar\n"
    "Responda SOMENTE com a palavra: PRODUCAO, REFERENCIA ou INDEFINIDO"
)


def _ext(filename: str) -> str:
    low = filename.lower()
    return ("." + low.rsplit(".", 1)[-1]) if "." in low else ""


async def classificar_arquivo(
    filename: str,
    parts: Optional[list] = None,
    openai_client=None,  # AsyncOpenAI — opcional para testes determinísticos
) -> str:
    """
    Retorna 'producao', 'referencia' ou 'indefinido'.
    Regras determinísticas por extensão primeiro; LLM mini-prompt só para
    imagem raster sem pista clara no nome (gpt-4o-mini).
    """
    low = filename.lower()
    ext = _ext(filename)

    # Vetoriais → sempre produção
    if ext in _VECTOR_EXTS:
        return "producao"

    # Photoshop e TIFF → sempre produção
    if ext in _RASTER_PROD_EXTS:
        return "producao"

    # Áudio e texto → sempre indefinido
    if ext in _AUDIO_EXTS or ext in _TEXT_EXTS:
        return "indefinido"

    # PDF → nome decide; falta de pista → produção (regra conservadora)
    if ext == ".pdf":
        if any(kw in low for kw in ("aprovacao", "aprovação", "modelo")):
            return "referencia"
        return "producao"

    # Imagem raster → pista no nome, depois LLM
    if ext in _IMAGE_EXTS:
        if any(kw in low for kw in ("logo", "logotipo", "marca", "qr", "ean", "barras")):
            return "producao"
        if any(kw in low for kw in ("ref", "referencia", "insp", "mock", "foto")):
            return "referencia"

        # Sem pista determinística → LLM mini-prompt (gpt-4o-mini, ~20 tokens saída)
        if parts and openai_client is not None:
            try:
                prompt = f'O arquivo se chama "{filename}". Classifique: PRODUCAO | REFERENCIA | INDEFINIDO'
                resp = await openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": _CLASSIFY_MINI_SYSTEM},
                        {"role": "user", "content": [{"type": "text", "text": prompt}, *parts]},
                    ],
                    max_tokens=10,
                )
                word = (resp.choices[0].message.content or "").strip().upper()
                if word == "PRODUCAO":
                    return "producao"
                if word == "REFERENCIA":
                    return "referencia"
            except Exception:
                pass

        return "indefinido"

    return "indefinido"
