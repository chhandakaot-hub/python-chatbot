import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.database import init_db
from app.routers import (
    assignments,
    auth,
    catalog,
    chat,
    completion,
    conversations,
    enrollments,
    feedback,
    results,
    staff,
    student_extras,
    students,
    users,
)


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    auth.router,
    users.router,
    conversations.router,
    chat.router,
    students.router,
    enrollments.router,
    assignments.router,
    results.router,
    completion.router,
    feedback.router,
    catalog.router,
    staff.router,
    student_extras.router,
):
    app.include_router(router, prefix=settings.API_V1_PREFIX)


@app.exception_handler(OperationalError)
def portal_query_too_slow(request: Request, exc: OperationalError) -> JSONResponse:
    """A portal query that hit PORTAL_STATEMENT_TIMEOUT_SECONDS.

    MariaDB interrupts the statement (error 1969) rather than letting it run on.
    Without this the caller would get an unhandled 500 and no idea why; with it
    they get a 504 that says to narrow the question, which is the actual fix.
    """
    if getattr(exc.orig, "args", [None])[0] != 1969:
        raise exc
    logger.warning("portal query exceeded the statement timeout: %s", request.url.path)
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content={
            "detail": "The portal query took too long and was stopped. Narrow it "
            "with a course, student or enrollment filter, or a smaller date range."
        },
    )


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


# Mounted last so it never shadows the API routes above.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
