from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import bonds, loans, interest, config_router, history_router, csv_upload, tig_orders

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
app.include_router(config_router.router, prefix="/api/config",  tags=["config"])
app.include_router(history_router.router, prefix="/api/history",    tags=["history"])
app.include_router(csv_upload.router,    prefix="/api/csv_upload",  tags=["csv_upload"])
app.include_router(tig_orders.router,    prefix="/api/tig_orders",  tags=["tig_orders"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}
