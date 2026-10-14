"""Natural-language analytics endpoint that always returns its SQL trace."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from dataflow_ai.ai.assistant import AnalyticsAssistant
from dataflow_ai.api.rate_limiter import rate_limit_dependency
from dataflow_ai.api.routes.auth import UserSession, get_current_user
from dataflow_ai.api.schemas import AskRequest, AskResponse
from dataflow_ai.utils import sanitize_secret_string

LOGGER = logging.getLogger("dataflow_ai.api.ai")
router = APIRouter(prefix="/api", tags=["ai"])

AI_RATE_LIMIT = rate_limit_dependency("ai_ask", max_requests=10, window_seconds=60)


@router.post("/ask", response_model=AskResponse, dependencies=[Depends(AI_RATE_LIMIT)])
def ask(
    request: Request,
    payload: AskRequest,
    current_user: UserSession = Depends(get_current_user),
) -> dict:
    assistant: AnalyticsAssistant = request.app.state.assistant
    try:
        return assistant.ask(payload.question).to_dict()
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        clean_err = sanitize_secret_string(str(error))
        LOGGER.exception("AI analytics request failed: %s", clean_err)
        raise HTTPException(
            status_code=503,
            detail=f"AI analytics is temporarily unavailable: {clean_err}",
        ) from error


