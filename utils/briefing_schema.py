"""
Módulo CY6.2 — Schema estruturado do pedido (DESLIGADO em produção até CY6.4).
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
}

CLASSES_ARQUIVO = {"producao", "referencia", "indefinido"}


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


@dataclass
class Pedido:
    numero_omie: Optional[str] = None
    cliente: Optional[str] = None
    produto: Optional[str] = None
    quantidade_total: Optional[str] = None
    modelos: list[Modelo] = field(default_factory=list)
    arquivos: list[Arquivo] = field(default_factory=list)
    inconsistencias: list[str] = field(default_factory=list)


# ── Tabela de requisitos por tipo de arte ─────────────────────────────────────
# Regra de negócio — será validada por Raíza na CY6.3 antes de entrar em produção.

CAMPOS_VISUAIS = ("logo", "fundo", "cor", "redes_sociais", "qr_code", "ean",
                  "tabela_nutricional", "selos", "box")

REQUISITOS_POR_TIPO: dict[str, dict[str, list[str]]] = {
    "reimpressao": {
        # Todos os visuais estão no arquivo de referência — só quantidade e referência são críticos.
        # "FINALIZAR na 1ª rodada se quantidade clara" surge de graça: pendencias_criticas() == []
        "criticos": ["quantidade", "arquivo_referencia"],
        "complementares": [],
    },
    "reimpressao_com_alteracao": {
        "criticos": ["quantidade", "arquivo_referencia"],
        "complementares": list(CAMPOS_VISUAIS),
    },
    "arte_nova": {
        # cor: só crítica sem referência visual (checada em pendencias_criticas)
        "criticos": ["quantidade", "logo", "cor"],
        "complementares": ["fundo", "redes_sociais", "qr_code", "ean",
                           "tabela_nutricional", "selos", "box"],
    },
}


# ── Funções de pendências ─────────────────────────────────────────────────────

def pendencias_criticas(pedido: Pedido) -> list[str]:
    result = []
    for i, modelo in enumerate(pedido.modelos, 1):
        label = f"Modelo {i} ({modelo.nome})" if modelo.nome else f"Modelo {i}"
        if modelo.tipo_arte == "pendente":
            result.append(f"{label}: tipo de arte não definido")
            continue
        reqs = REQUISITOS_POR_TIPO.get(modelo.tipo_arte, {})
        criticos = reqs.get("criticos", [])
        if "quantidade" in criticos and modelo.quantidade is None:
            result.append(f"{label}: quantidade não informada")
        if "arquivo_referencia" in criticos and modelo.arquivo_referencia is None:
            result.append(f"{label}: arquivo de referência não recebido")
        for campo in criticos:
            if campo in ("quantidade", "arquivo_referencia"):
                continue
            cv: Optional[CampoVisual] = getattr(modelo, campo, None)
            if cv and cv.estado == "pendente":
                # cor não é crítica quando há referência visual
                if campo == "cor" and modelo.arquivo_referencia:
                    continue
                result.append(f"{label}: {campo} pendente")
    return result


def pendencias_complementares(pedido: Pedido) -> list[str]:
    result = []
    for i, modelo in enumerate(pedido.modelos, 1):
        label = f"Modelo {i} ({modelo.nome})" if modelo.nome else f"Modelo {i}"
        if modelo.tipo_arte == "pendente":
            continue
        reqs = REQUISITOS_POR_TIPO.get(modelo.tipo_arte, {})
        for campo in reqs.get("complementares", []):
            cv: Optional[CampoVisual] = getattr(modelo, campo, None)
            if cv and cv.estado == "pendente":
                result.append(f"{label}: {campo} pendente")
    return result


# ── Serialização JSON ida-e-volta ─────────────────────────────────────────────

def pedido_to_json(pedido: Pedido) -> str:
    return json.dumps(asdict(pedido), ensure_ascii=False, indent=2)


def pedido_from_json(data: "str | dict") -> Pedido:
    if isinstance(data, str):
        data = json.loads(data)

    raw_modelos = data.get("modelos", [])
    modelos = []
    for m in raw_modelos:
        m = dict(m)  # cópia para não modificar o original
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
    )
