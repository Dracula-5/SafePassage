"""Instance A REST endpoints (see contracts/openapi.yaml, tag `brain`).

Gate 0: paths are registered so the contract is visible end to end, but each returns
501 Not Implemented until its component lands. No endpoint returns invented data.
"""

from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["brain"])


def _not_implemented(operation: str) -> JSONResponse:
    return JSONResponse(
        {"title": "Not Implemented", "status": 501, "detail": f"{operation} lands after Gate 0"},
        status_code=501,
        media_type="application/problem+json",
    )


@router.post("/api/plans/trigger")
async def trigger_plan() -> JSONResponse:
    return _not_implemented("triggerPlan")


@router.get("/api/plans")
async def list_plans() -> JSONResponse:
    return _not_implemented("listPlans")


@router.get("/api/plans/{plan_id}")
async def get_plan(plan_id: UUID) -> JSONResponse:
    return _not_implemented("getPlan")


@router.post("/api/plans/{plan_id}/approve")
async def decide_plan(plan_id: UUID) -> JSONResponse:
    return _not_implemented("decidePlan")


@router.get("/api/orders")
async def list_orders() -> JSONResponse:
    return _not_implemented("listOrders")


@router.get("/api/vehicles/{vehicle_id}/assignment")
async def get_vehicle_assignment(vehicle_id: UUID) -> JSONResponse:
    return _not_implemented("getVehicleAssignment")


@router.get("/api/brain/health")
async def brain_health() -> JSONResponse:
    return _not_implemented("brainHealth")
