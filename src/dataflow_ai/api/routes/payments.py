"""Payment webhook endpoints for Stripe and Razorpay."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from dataflow_ai.security import (
    verify_razorpay_webhook_signature,
    verify_stripe_webhook_signature,
)

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.post("/webhook/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict[str, Any]:
    """Process incoming Stripe webhook events."""
    settings = getattr(request.app.state, "settings", None)
    webhook_secret = getattr(settings, "stripe_webhook_secret", None)
    if not webhook_secret:
        raise HTTPException(
            status_code=500,
            detail="Stripe webhook is unconfigured: STRIPE_WEBHOOK_SECRET must be set in environment.",
        )

    body = await request.body()
    if not verify_stripe_webhook_signature(body, stripe_signature, webhook_secret):
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature.")

    try:
        event = json.loads(body.decode("utf-8"))
    except Exception as error:
        raise HTTPException(status_code=400, detail="Invalid JSON in webhook payload.") from error

    return {"status": "received", "event_type": event.get("type", "unknown")}


@router.post("/webhook/razorpay")
async def razorpay_webhook(
    request: Request,
    razorpay_signature: str | None = Header(default=None, alias="X-Razorpay-Signature"),
) -> dict[str, Any]:
    """Process incoming Razorpay webhook events."""
    settings = getattr(request.app.state, "settings", None)
    webhook_secret = getattr(settings, "razorpay_webhook_secret", None)
    if not webhook_secret:
        raise HTTPException(
            status_code=500,
            detail="Razorpay webhook is unconfigured: RAZORPAY_WEBHOOK_SECRET must be set in environment.",
        )

    body = await request.body()
    if not verify_razorpay_webhook_signature(body, razorpay_signature, webhook_secret):
        raise HTTPException(status_code=400, detail="Invalid Razorpay webhook signature.")

    try:
        event = json.loads(body.decode("utf-8"))
    except Exception as error:
        raise HTTPException(status_code=400, detail="Invalid JSON in webhook payload.") from error

    return {"status": "received", "event": event.get("event", "unknown")}
