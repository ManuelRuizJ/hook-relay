from fastapi import APIRouter

from app.api.v1.endpoints import events, webhook_endpoints

api_router = APIRouter()
api_router.include_router(webhook_endpoints.router)
api_router.include_router(events.router)