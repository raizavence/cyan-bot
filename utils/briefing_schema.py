"""
Módulo CY6.2 — Schema estruturado do pedido (atualizado CY7.2, CY8.1).
Não importado pelos handlers da v1.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import json

TIPOS_ARTE = {"arte_nova", "reimpressao", "reimpressao_com_alteracao", "pendente"}

ESTADOS_CAMPO = {
    "pendente",
    "preenchido",
    "resolvido_pela_referencia",
    "identificado_na_referencia_aguardando_arquivo",
    "nao_se_aplica",
    "aguardando_material",   # CY8: atendimento confirmou que vai ter; material ainda não recebido
}

CLASSES_ARQUIVO = {"producao", "referencia", "indefinido"}

# CY8 — Checklist de presença binária para modelos de arte nova
# logo fica FORA (D2): é crítico de arte_nova, já tratado em pendencias_criticas
CAMPOS_CHECKLIST = ("redes_sociais", "qr_code", "ean", "tabela_nutricional", "selos", "box")
TIPOS_COM_CHECKLIST = {"arte_nova"}


@dataclass
class CampoVisual:
    estado: str = "pendente"
    valor: Optional[str] = None


@dataclass
class Modelo:
    nome: str = ""
    quantidade: Optional[str] = None
    tipo_arte: str = "pendente"          # arte_nova | reimpressao | reimpressao_com_alteracao | pendente
    arquivo_referencia: Optional[str] = None
    logo: CampoVisual = field(default_factory=CampoVisual)
    fundo: CampoVisual = field(default_factory=CampoVisual)
    cor: CampoVisual = field(default_factory=CampoVisual)
    redes_sociais: CampoVisual = field(default_factory=CampoVisual)
    qr_code: CampoVisual = field(default_factory=CampoVisual)
    ean: CampoVisual = field(default_factory=CampoVisual)
    tabela_nutricional: CampoVisual = field(default_factory=CampoVisual)
    selos: CampoVisual = field(default_factory=CampoVisual)
    box: CampoVisual = field(default_factory=CampoVisual)
    acoes_para_arte: list[str] = field(default_factory=list)


@dataclass
class Arquivo:
    nome: str = ""
    url: str = ""
    classe: str = "indefinido"           # producao | referencia | indefinido
    status_tecnico: str = ""
    flag: str = ""                        # ok | atencao | recusar | ""
    recomendacao: str = ""               # ação recomendada ao atendimento


@dataclass
class Pedido:
    numero_omie: Optional[str] = None
    cliente: Optional[str] = None
    produto: Optional[str] = None
    quantidade_total: Optional[str] = None
    modelos: list[Modelo] = field(default_factory=list)
    arquivos: list[Arquivo] = field(default_factory=list)
    inconsistencias: list[str] = field(default_factory=list)
    alertas_impressao: list[str] = field(default_factory=list)       # CY8: limitações Offset CMYK
    observacoes_atendimento: list[str] = field(default_factory=list) # CY8: obs da confirmação final


# ── Tabela de requisitos por tipo de arte ─────────────────────────────────────
# Regra de negócio — validada por Raíza na CY6.3.

CAMPOS_VISUAIS = ("logo", "fundo", "cor", "redes_sociais", "qr_code", "ean",
                  "tabela_nutricional", "selos", "box")

REQUISITOS_POR_TIPO: dict[str, dict[str, list[str]]] = {
    "reimpressao": {
        "criticos": ["arquivo_referencia", "produto"],
        "complementares": ["quantidade"],
    },
    "reimpressao_com_alteracao": {
        "criticos": ["arquivo_referencia", "produto"],
        "complementares": ["quantidade"] + list(CAMPOS_VISUAIS),
    },
    "arte_nova": {
        "criticos": ["quantidade", "logo"],
        "complementares": ["fundo", "cor", "redes_sociais", "qr_code", "ean",
                           "tabela_nutricional", "selos", "box"],
    },
}


# ── Funções de pendências ─────────────────────────────────────────────────────

def pendencias_criticas(pedido: Pedido) -> list[str]:
    result = []

    # Arquivos com flag "recusar" bloqueiam o início da arte (CY7.4)
    for arq in pedido.arquivos:
        if arq.flag == "recusar":
            detalhe = arq.recomendacao or arq.status_tecnico or "inadequado"
            result.append(f"Arquivo {arq.nome}: {detalhe}")

    for i, modelo in enumerate(pedido.modelos, 1):
        label = f"Modelo {i} ({modelo.nome})" if modelo.nome else f"Modelo {i}"
        if modelo.tipo_arte == "pendente":
            result.append(f"{label}: tipo de arte não definido")
            continue
        reqs = REQUISITOS_POR_TIPO.get(modelo.tipo_arte, {})
        criticos = reqs.get("criticos", [])

        if "produto" in criticos and pedido.produto is None:
            result.append(f"{label}: tipo de produto e volumetria não informados")

        if "arquivo_referencia" in criticos and modelo.arquivo_referencia is None:
            result.append(f"{label}: arquivo de referência não recebido")

        if "quantidade" in criticos and modelo.quantidade is None:
            result.append(f"{label}: quantidade não informada")

        _nao_campo = {"produto", "arquivo_referencia", "quantidade"}
        for campo in criticos:
            if campo in _nao_campo:
                continue
            cv: Optional[CampoVisual] = getattr(modelo, campo, None)
            if cv and cv.estado == "pendente":
                result.append(f"{label}: {campo} pendente")
    return result


def pendencias_complementares(pedido: Pedido) -> list[str]:
    result = []

    # Arquivos com flag "atencao" e recomendação → complementar (CY7.4)
    for arq in pedido.arquivos:
        if arq.flag == "atencao" and arq.recomendacao:
            result.append(f"Arquivo {arq.nome}: {arq.recomendacao}")

    for i, modelo in enumerate(pedido.modelos, 1):
        label = f"Modelo {i} ({modelo.nome})" if modelo.nome else f"Modelo {i}"
        if modelo.tipo_arte == "pendente":
            continue
        reqs = REQUISITOS_POR_TIPO.get(modelo.tipo_arte, {})
        for campo in reqs.get("complementares", []):
            if campo == "quantidade":
                if modelo.quantidade is None:
                    result.append(f"{label}: quantidade não informada")
                continue
            cv: Optional[CampoVisual] = getattr(modelo, campo, None)
            if cv and cv.estado == "pendente":
                result.append(f"{label}: {campo} pendente")
            elif cv and cv.estado == "aguardando_material":
                # CY8: confirmado que vai ter; material ainda não recebido
                result.append(f"{label}: {campo} confirmado — aguardando material do cliente")
    return result


# ── Helpers CY8 ───────────────────────────────────────────────────────────────

def itens_checklist(pedido: Pedido) -> list[tuple[int, str]]:
    """Retorna (índice do modelo, nome do campo) com estado pendente no checklist (D2/D4)."""
    result = []
    for i, modelo in enumerate(pedido.modelos):
        if modelo.tipo_arte not in TIPOS_COM_CHECKLIST:
            continue
        for campo in CAMPOS_CHECKLIST:
            cv: Optional[CampoVisual] = getattr(modelo, campo, None)
            if cv and cv.estado == "pendente":
                result.append((i, campo))
    return result


def proxima_fase(pedido: Pedido) -> str:
    """Determina a próxima fase do pipeline v2 a partir do estado do objeto (D1).

    Fases: questionnaire → checklist → confirmacao → complete
    A transição é sempre derivada do objeto — nunca armazenada em dois lugares.
    """
    if pendencias_criticas(pedido):
        return "questionnaire"
    if itens_checklist(pedido):
        return "checklist"
    if any(m.tipo_arte == "arte_nova" for m in pedido.modelos):
        return "confirmacao"
    return "complete"


# ── Serialização JSON ida-e-volta ─────────────────────────────────────────────

def pedido_to_json(pedido: Pedido) -> str:
    return json.dumps(asdict(pedido), ensure_ascii=False, indent=2)


def pedido_from_json(data: "str | dict") -> Pedido:
    if isinstance(data, str):
        data = json.loads(data)

    raw_modelos = data.get("modelos", [])
    modelos = []
    for m in raw_modelos:
        m = dict(m)
        campos = {}
        for campo in CAMPOS_VISUAIS:
            raw = m.pop(campo, None)
            campos[campo] = CampoVisual(**raw) if raw else CampoVisual()
        apa = m.pop("acoes_para_arte", [])
        modelos.append(Modelo(**m, acoes_para_arte=apa, **campos))

    arquivos = [Arquivo(**a) for a in data.get("arquivos", [])]

    return Pedido(
        numero_omie=data.get("numero_omie"),
        cliente=data.get("cliente"),
        produto=data.get("produto"),
        quantidade_total=data.get("quantidade_total"),
        modelos=modelos,
        arquivos=arquivos,
        inconsistencias=data.get("inconsistencias", []),
        alertas_impressao=data.get("alertas_impressao", []),       # retro-compatível
        observacoes_atendimento=data.get("observacoes_atendimento", []),  # retro-compatível
    )
