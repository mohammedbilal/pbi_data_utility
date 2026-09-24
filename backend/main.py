from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routers import (bonds, loans, interest, config_router, history_router,
                     csv_upload, tig_orders, compare_router, securitized,
                     munis)

app = FastAPI(title="PBI Test Utility", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(bonds.router,        prefix="/api/bonds",    tags=["bonds"])
app.include_router(loans.router,        prefix="/api/loans",    tags=["loans"])
app.include_router(interest.router,     prefix="/api/interest", tags=["interest"])
app.include_router(securitized.router,  prefix="/api/securitized", tags=["securitized"])
app.include_router(munis.router,        prefix="/api/munis",    tags=["munis"])
app.include_router(config_router.router, prefix="/api/config",  tags=["config"])
app.include_router(history_router.router, prefix="/api/history",    tags=["history"])
app.include_router(csv_upload.router,    prefix="/api/csv_upload",  tags=["csv_upload"])
app.include_router(tig_orders.router,    prefix="/api/tig_orders",  tags=["tig_orders"])
app.include_router(compare_router.router, prefix="/api/compare",    tags=["compare"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Single-port mode (Docker): if the frontend has been built, serve it from here
# instead of the Vite dev server, so there is no :5173 and no /api proxy. Mounted
# last so every /api route above still wins. On Windows dev this directory is
# usually absent and the mount is simply skipped.
_UI_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _UI_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_UI_DIST, html=True), name="ui")
