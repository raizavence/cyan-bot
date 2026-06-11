"""
Módulo CY6.2 — Templates Python para o briefing final (DESLIGADO em produção até CY6.4).
Não importado pelos handlers da v1.
"""
from __future__ import annotations
from utils.briefing_schema import (
    Pedido, Modelo, CampoVisual,
    pendencias_criticas, pendencias_complementares,
)

_TIPO_RENDER = {
    "arte_nova": "Arte Nova",
    "reimpressao": "Reimpressão sem alteração",
    "reimpressao_com_alteracao": "Reimpressão com alteração",
    "pendente": "🟡 Pendente",
}

_CLASSE_RENDER = {
    "producao": "PRODUÇÃO",
    "referencia": "REFERÊNCIA",
    "indefinido": "INDEFINIDO",
}


def _render_campo(cv: CampoVisual, nome: str) -> str:
    if cv.estado == "preenchido":
        return f"• {nome}: {cv.valor or 'Informado'}"
    mapa = {
        "pendente": "🟡 Pendente",
        "resolvido_pela_referencia": "➖ Consta na referência",
        "identificado_na_referencia_aguardando_arquivo": "🟡 Identificado na referência — aguardando arquivo",
        "nao_se_aplica": "➖ Não se aplica",
    }
    return f"• {nome}: {mapa.get(cv.estado, cv.estado)}"


def _bloco_reimpressao(modelo: Modelo, numero: int) -> str:
    label = f"📦 MODELO {numero}" + (f" — {modelo.nome}" if modelo.nome else "")
    return "\n".join([
        label,
        f"• Quantidade: {modelo.quantidade or '🟡 Não informado'}",
        "• Tipo de arte: Reimpressão sem alteração",
        f"• Referência: {modelo.arquivo_referencia or '🟡 Não informado'}",
    ])


def _bloco_completo(modelo: Modelo, numero: int) -> str:
    label = f"📦 MODELO {numero}" + (f" — {modelo.nome}" if modelo.nome else "")
    linhas = [
        label,
        f"• Quantidade: {modelo.quantidade or '🟡 Não informado'}",
        f"• Tipo de arte: {_TIPO_RENDER.get(modelo.tipo_arte, modelo.tipo_arte)}",
        _render_campo(modelo.logo, "Logo"),
        _render_campo(modelo.fundo, "Fundo"),
        _render_campo(modelo.cor, "Cor(es)"),
        f"• Referência visual: {modelo.arquivo_referencia or 'Não fornecida'}",
        _render_campo(modelo.redes_sociais, "Redes sociais"),
        _render_campo(modelo.qr_code, "QR Code"),
        _render_campo(modelo.ean, "Código de barras (EAN)"),
        _render_campo(modelo.tabela_nutricional, "Tabela nutricional"),
        _render_campo(modelo.selos, "Selos"),
        _render_campo(modelo.box, "Box para escrita"),
    ]
    return "\n".join(linhas)


def briefing(pedido: Pedido) -> str:
    """Gera o briefing final completo em texto, espelhando o formato visual do SYSTEM_PROMPT."""
    partes = []

    # Identificação
    partes.append(
        "📋 IDENTIFICAÇÃO\n"
        f"• Pedido Omie: {pedido.numero_omie or '🟡 Não informado'}\n"
        f"• Cliente: {pedido.cliente or '🟡 Não informado'}\n"
        f"• Produto: {pedido.produto or '🟡 Não informado'}\n"
        f"• Quantidade total: {pedido.quantidade_total or '🟡 Não informado'}"
    )

    # Modelos
    for i, modelo in enumerate(pedido.modelos, 1):
        if modelo.tipo_arte == "reimpressao":
            partes.append(_bloco_reimpressao(modelo, i))
        else:
            partes.append(_bloco_completo(modelo, i))

    # Arquivos
    if pedido.arquivos:
        linhas = ["📎 ARQUIVOS RECEBIDOS"]
        for arq in pedido.arquivos:
            classe = _CLASSE_RENDER.get(arq.classe, arq.classe.upper())
            status = arq.status_tecnico or "—"
            linhas.append(f"• {arq.nome} — {classe} — {status}")
        partes.append("\n".join(linhas))

    # Inconsistências
    if pedido.inconsistencias:
        linhas = ["⚠️ INCONSISTÊNCIAS IDENTIFICADAS"]
        for j, inc in enumerate(pedido.inconsistencias, 1):
            linhas.append(f"{j}. {inc}")
        partes.append("\n".join(linhas))

    # Pendências
    criticas = pendencias_criticas(pedido)
    complementares = pendencias_complementares(pedido)

    bloco_criticas = "🔴 PENDÊNCIAS CRÍTICAS — bloqueiam início da arte\n"
    bloco_criticas += "\n".join(f"• {p}" for p in criticas) if criticas else "Nenhuma."
    partes.append(bloco_criticas)

    bloco_comp = "🟡 PENDÊNCIAS COMPLEMENTARES\n"
    bloco_comp += "\n".join(f"• {p}" for p in complementares) if complementares else "Nenhuma."
    partes.append(bloco_comp)

    # Totais
    sep = "─" * 37
    partes.append(
        f"{sep}\n"
        f"TOTAIS: {len(criticas)} pendências críticas | {len(complementares)} pendências complementares\n"
        f"{sep}"
    )

    return "\n\n".join(partes)


def resumo_para_arte(pedido: Pedido) -> str:
    """Seção Para Arte — ações concretas por modelo + pendências abertas."""
    linhas = ["📝 **Para Arte:**"]
    for i, modelo in enumerate(pedido.modelos, 1):
        if modelo.acoes_para_arte:
            if len(pedido.modelos) > 1:
                linhas.append(f"\n**Modelo {i}{' — ' + modelo.nome if modelo.nome else ''}:**")
            for acao in modelo.acoes_para_arte:
                linhas.append(f"• {acao}")

    criticas = pendencias_criticas(pedido)
    if criticas:
        linhas.append("\n⏳ **Aguardando confirmação:**")
        for p in criticas:
            linhas.append(f"• {p}")
    else:
        linhas.append("\n⏳ **Aguardando confirmação:** Nenhuma pendência.")

    return "\n".join(linhas)
