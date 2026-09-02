from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.core.config import settings
from app.services.rag.errors import KnowledgeBaseError


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name)

    @app.exception_handler(KnowledgeBaseError)
    async def knowledge_base_error_handler(request: Request, error: KnowledgeBaseError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content={"detail": {"code": error.code, "message": error.message}})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
