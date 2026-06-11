from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Optional

EXPIRY_SECONDS = 28800  # 8 horas (um expediente) sem atividade → estado expira


@dataclass
class PendingCall:
    user_id: int
    user_display_name: str
    problem_text: str


@dataclass
class FileRecord:
    filename: str
    url: str
    file_type: str = "undefined"    # production | reference | undefined
    status: str = "pending"         # pending | approved | warning | rejected | proceeding
    analysis: str = ""
    analysis_msg_id: Optional[int] = None


@dataclass
class OrderState:
    order_number: str = "?"
    client: str = "?"
    briefing_channel_id: int = 0
    stage: str = "questionnaire"    # questionnaire | complete
    conversation: list[dict] = field(default_factory=list)
    files: dict[str, FileRecord] = field(default_factory=dict)
    # Quando o atendimento clica "Enviar novo arquivo": (filename_original, analysis_msg_id)
    pending_replacement: Optional[tuple[str, int]] = None
    final_briefing: str = ""
    final_resumo: str = ""
    audio_transcripts: list[tuple[str, str]] = field(default_factory=list)  # (filename, transcript)
    pending_call: Optional[PendingCall] = None
    last_activity: float = field(default_factory=time.time)


# ── store indexado por briefing channel_id ───────────────────────────────────
_store: dict[int, OrderState] = {}


def get(channel_id: int) -> Optional[OrderState]:
    state = _store.get(channel_id)
    if state and (time.time() - state.last_activity) > EXPIRY_SECONDS:
        _store.pop(channel_id, None)
        return None
    return state


def save(channel_id: int, state: OrderState) -> None:
    state.last_activity = time.time()
    _store[channel_id] = state


def remove(channel_id: int) -> None:
    _store.pop(channel_id, None)


def find_by_order(order_number: str) -> Optional[tuple[int, OrderState]]:
    """Encontra estado pelo número do pedido Omie."""
    for ch_id, s in _store.items():
        if s.order_number == order_number:
            return ch_id, s
    return None
