"""Privacy endpoints for customer data requests (erasure and status)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from dataflow_ai.api.routes.auth import UserSession, get_current_user
from dataflow_ai.warehouse.repository import WarehouseRepository

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


def _repository(request: Request) -> WarehouseRepository:
    return WarehouseRepository(getattr(request.state, "workspace_settings", request.app.state.settings))


@router.get("/customers/{customer_id}/status")
def customer_privacy_status(
    request: Request,
    customer_id: str,
    current_user: UserSession = Depends(get_current_user),
) -> dict:
    if current_user.user_id != customer_id and current_user.role not in ("admin", "dpo"):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: You do not have permission to view privacy data for another customer.",
        )

    repository = _repository(request)
    status = repository.authorized_customer_action(customer_id, current_user.user_id, current_user.role)
    if not status:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found.")
    return status


@router.post("/customers/{customer_id}/delete")
def delete_customer_personal_data(
    request: Request,
    customer_id: str,
    current_user: UserSession = Depends(get_current_user),
) -> dict:
    if current_user.user_id != customer_id and current_user.role not in ("admin", "dpo"):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: You do not have permission to delete personal data for another customer.",
        )

    repository = _repository(request)
    try:
        receipt = repository.authorized_customer_action(customer_id, current_user.user_id, current_user.role, erase=True)
        return receipt
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail="Failed to anonymize customer data.") from error

