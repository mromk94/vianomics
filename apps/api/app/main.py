from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import auth, command_center, health, mandate, universe

settings = get_settings()

app = FastAPI(title=settings.app_name, version=settings.version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["system"])
app.include_router(auth.router, prefix="/api/v1")
app.include_router(command_center.router, prefix="/api/v1", tags=["command-center"])
app.include_router(mandate.router, prefix="/api/v1")
app.include_router(universe.router, prefix="/api/v1")
