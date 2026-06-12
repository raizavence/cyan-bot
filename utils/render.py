"""
Módulo CY6.2 — Templates Python para o briefing final (layout aprovado em CY7.6).
Não importado pelos handlers da v1.
"""
from __future__ import annotations
from utils.briefing_schema import (
    Pedido, Modelo, CampoVisual, Arquivo,
    pendencias_criticas, pendencias_complementares,
)

_SEP = "──────────────"

_TIPO_RENDER = {
    "arte_nova": "Arte Nova",
    "reimpressao": "Reimpressão sem alteração",
    "reimpressao_com_alteracao": "Reimpressão com alteração",
    "pendente": "A definir",
}

_CLASSE_RENDER = {
    "producao": "Produção",
    "referencia": "Referência",
    "indefinido": "A classificar",
}

_FLAG_EMOJI = {
    "ok": "✅",
    "atencao": "⚠️",
    "recusar": "🔴",
}

_CAMPO_NOME = {
    "logo": "Logo",
    "fundo": "Fundo",
    "cor": "Cor(es)",
    "redes_sociais": "Redes sociais",
    "qr_code": "QR Code",
    "ean": "EAN / Código de barras",
    "tabela_nutricional": "Tabela nutricional",
    "selos": "Selos",
    "box": "Box para escrita",
}


def _campos_preenchidos(modelo: Modelo) -> list[str]:
    """Retorna linhas compactas dos campos com estado=preenchido e valor."""
    linhas = []
    for campo, nome in _CAMPO_NOME.items():
        cv: CampoVisual = getattr(modelo, campo)
        if cv.estado == "preenchido" and cv.valor:
            linhas.append(f"{nome}: {cv.valor}")
    return linhas


def _referencia_e_leque(modelo: Modelo, arquivos: list[Arquivo]) -> bool:
    """True quando o arquivo_referencia do modelo é uma arte em leque."""
    if not modelo.arquivo_referencia:
        return False
    for arq in arquivos:
        if arq.nome == modelo.arquivo_referencia and "leque" in arq.status_tecnico.lower():
            return True
    return False


def briefing(pedido: Pedido, perguntas: str = "") -> str:
    """Gera o briefing no layout aprovado por Raíza em 2026-06-12 (CY7.6).

    flags só aparecem em ARQUIVOS e nas seções de pendência — nunca dentro dos blocos de modelo.
    """
    lines: list[str] = []

    # ── Cabeçalho do pedido ───────────────────────────────────────────────────
    num = pedido.numero_omie or "?"
    cli = pedido.cliente or "?"
    lines.append(f"📋 PEDIDO {num} — {cli}")
    prod = pedido.produto or "—"
    qtd = pedido.quantidade_total or "—"
    lines.append(f"Produto: {prod} · Quantidade total: {qtd}")

    # ── Modelos ───────────────────────────────────────────────────────────────
    for i, modelo in enumerate(pedido.modelos, 1):
        lines.append(_SEP)

        nome_label = f" — {modelo.nome}" if modelo.nome else ""
        qtd_label = f" · {modelo.quantidade} un" if modelo.quantidade else ""
        lines.append(f"🎨 MODELO {i}{nome_label}{qtd_label}")

        tipo = _TIPO_RENDER.get(modelo.tipo_arte, modelo.tipo_arte)
        lines.append(f"Tipo: {tipo}")

        if modelo.arquivo_referencia:
            nota = " (referência em leque)" if _referencia_e_leque(modelo, pedido.arquivos) else ""
            lines.append(f"Baseada em: {modelo.arquivo_referencia}{nota}")

        # Apenas campos com valor preenchido — pendentes vão para a seção de pendências
        for campo_linha in _campos_preenchidos(modelo):
            lines.append(campo_linha)

        if modelo.acoes_para_arte:
            lines.append("   Como montar:")
            for acao in modelo.acoes_para_arte:
                lines.append(f"   • {acao}")

    # ── Arquivos ──────────────────────────────────────────────────────────────
    if pedido.arquivos:
        lines.append(_SEP)
        lines.append("📎 ARQUIVOS")
        for arq in pedido.arquivos:
            emoji = _FLAG_EMOJI.get(arq.flag, "")
            prefix = emoji if emoji else "•"
            classe = _CLASSE_RENDER.get(arq.classe, arq.classe)
            lines.append(f"{prefix} {arq.nome} — {classe}")
            if arq.status_tecnico:
                lines.append(f"   {arq.status_tecnico}")
            if arq.recomendacao:
                lines.append(f"   ↳ {arq.recomendacao}")

    # ── Inconsistências ───────────────────────────────────────────────────────
    if pedido.inconsistencias:
        lines.append(_SEP)
        lines.append("⚠️ INCONSISTÊNCIAS")
        for j, inc in enumerate(pedido.inconsistencias, 1):
            lines.append(f"{j}. {inc}")

    # ── Pendências ────────────────────────────────────────────────────────────
    criticas = pendencias_criticas(pedido)
    complementares = pendencias_complementares(pedido)

    lines.append(_SEP)

    if not criticas and not complementares:
        lines.append("✅ Sem pendências — briefing completo.")
    else:
        if criticas:
            lines.append("🔴 IMPEDE A ARTE — resolver antes de iniciar")
            for p in criticas:
                lines.append(f"• {p}")
        if complementares:
            lines.append("🟡 NÃO IMPEDE — a arte pode seguir")
            for p in complementares:
                lines.append(f"• {p}")

    # ── Perguntas ─────────────────────────────────────────────────────────────
    if perguntas:
        lines.append(_SEP)
        lines.append("❓ PERGUNTAS")
        lines.append(perguntas)

    return "\n".join(lines)
