from fastapi import APIRouter

from app.api.v1.endpoints import dead_letters, events, webhook_endpoints

api_router = APIRouter()
api_router.include_router(webhook_endpoints.router)
api_router.include_router(events.router)
api_router.include_router(dead_letters.router)