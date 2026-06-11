"""
Módulo CY6.2 — Wrapper fino para chamadas GPT estruturadas (DESLIGADO em produção até CY6.4).
Não importado pelos handlers da v1.
"""
from __future__ import annotations
import json
from openai import AsyncOpenAI


async def structured_call(
    client: AsyncOpenAI,
    system: str,
    user_parts: list[dict],
    json_schema: dict,
    model: str = "gpt-4o",
) -> dict:
    """
    Chamada GPT com saída JSON (response_format=json_object).
    Faz 1 retry em falha de parse.
    """
    for attempt in range(2):
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_parts},
            ],
            response_format={"type": "json_object"},
            max_tokens=4096,
        )
        raw = (response.choices[0].message.content or "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            if attempt == 0:
                continue
            raise ValueError(f"GPT devolveu JSON inválido após 2 tentativas: {raw[:300]}")
    raise RuntimeError("unreachable")
