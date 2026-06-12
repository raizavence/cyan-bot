from __future__ import annotations
import json
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

EXPIRY_SECONDS = 28800  # 8 horas (um expediente) sem atividade → estado expira

DB_PATH = Path(__file__).parent.parent / "cyan.db"


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


# ── SQLite ────────────────────────────────────────────────────────────────────

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS briefings (
                channel_id INTEGER PRIMARY KEY,
                state_json TEXT NOT NULL,
                last_activity REAL NOT NULL
            )
        """)
        conn.commit()


_init_db()


def _state_to_json(state: OrderState) -> str:
    return json.dumps(asdict(state))


def _json_to_state(json_str: str) -> OrderState:
    data = json.loads(json_str)

    files_raw = data.pop("files", {})
    files = {k: FileRecord(**v) for k, v in files_raw.items()}

    pc = data.pop("pending_call", None)
    pending_call = PendingCall(**pc) if pc else None

    pr = data.pop("pending_replacement", None)
    pending_replacement = tuple(pr) if pr is not None else None

    at = data.pop("audio_transcripts", [])
    audio_transcripts = [tuple(t) for t in at]

    return OrderState(
        files=files,
        pending_call=pending_call,
        pending_replacement=pending_replacement,
        audio_transcripts=audio_transcripts,
        **data,
    )


# ── API pública (idêntica à versão em memória) ────────────────────────────────

def get(channel_id: int) -> Optional[OrderState]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT state_json, last_activity FROM briefings WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
    if not row:
        return None
    state_json, last_activity = row
    if (time.time() - last_activity) > EXPIRY_SECONDS:
        remove(channel_id)
        return None
    state = _json_to_state(state_json)
    state.last_activity = last_activity
    return state


def save(channel_id: int, state: OrderState) -> None:
    state.last_activity = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO briefings (channel_id, state_json, last_activity)
            VALUES (?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                state_json = excluded.state_json,
                last_activity = excluded.last_activity
            """,
            (channel_id, _state_to_json(state), state.last_activity),
        )
        conn.commit()


def remove(channel_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM briefings WHERE channel_id = ?", (channel_id,))
        conn.commit()


def find_by_order(order_number: str) -> Optional[tuple[int, OrderState]]:
    """Encontra estado pelo número do pedido Omie."""
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT channel_id, state_json, last_activity FROM briefings"
        ).fetchall()
    for channel_id, state_json, last_activity in rows:
        if (now - last_activity) > EXPIRY_SECONDS:
            continue
        state = _json_to_state(state_json)
        if state.order_number == order_number:
            return channel_id, state
    return None


# ── V2 state (briefings_v2) ────────────────────────────────────────────────────

def _init_v2_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS briefings_v2 (
                channel_id INTEGER PRIMARY KEY,
                pedido_json TEXT NOT NULL,
                conversation_json TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'questionnaire',
                last_activity REAL NOT NULL
            )
        """)
        conn.commit()


_init_v2_db()


def get_v2_raw(channel_id: int) -> Optional[tuple[str, str, str]]:
    """Retorna (pedido_json, conversation_json, stage) ou None se expirado/inexistente."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT pedido_json, conversation_json, stage, last_activity FROM briefings_v2 WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
    if not row:
        return None
    pedido_json, conv_json, stage, last_activity = row
    if (time.time() - last_activity) > EXPIRY_SECONDS:
        remove_v2(channel_id)
        return None
    return pedido_json, conv_json, stage


def save_v2(channel_id: int, pedido_json: str, conversation_json: str, stage: str) -> None:
    now = time.time()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO briefings_v2 (channel_id, pedido_json, conversation_json, stage, last_activity)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                pedido_json = excluded.pedido_json,
                conversation_json = excluded.conversation_json,
                stage = excluded.stage,
                last_activity = excluded.last_activity
            """,
            (channel_id, pedido_json, conversation_json, stage, now),
        )
        conn.commit()


def remove_v2(channel_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM briefings_v2 WHERE channel_id = ?", (channel_id,))
        conn.commit()
