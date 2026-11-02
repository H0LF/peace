"""Typed decisions (choice, score, yes/no with probabilities) from a Laya server, an optional separate service.

Laya needs PyTorch, so it runs in its own environment (scripts/install-laya.sh) on loopback and PEACE stays small.
No feature depends on it yet: answers are not trustworthy on PEACE's data until the model is fine-tuned.
"""

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from peace.auth import require_token

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])

LAYA_URL = os.environ.get("PEACE_LAYA_URL", "http://127.0.0.1:3920").rstrip("/")


class DecideIn(BaseModel):
    state: str = Field(min_length=1, max_length=20_000)
    questions: dict[str, dict[str, Any]] = Field(min_length=1, max_length=20)


@router.post("/decide")
def decide(body: DecideIn):
    try:
        r = httpx.post(LAYA_URL + "/v1/systemone", json={"state": {"text": body.state}, "questions": body.questions}, timeout=60)
    except httpx.HTTPError as e:
        raise HTTPException(503, "O Laya não está a correr neste servidor.") from e
    if r.status_code == 422:  # a badly formed question, not an outage
        raise HTTPException(422, r.json().get("detail", "Pergunta inválida"))
    if r.is_error:
        log.warning("Laya answered %s", r.status_code)
        raise HTTPException(503, "O Laya não conseguiu responder.")
    return r.json()
