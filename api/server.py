"""FastAPI server serving bot state and historical data."""

import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

app = FastAPI(title="XMR1/USDC Market Maker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE_PATH = Path("state.json")
DASHBOARD_PATH = Path(__file__).parent.parent / "dashboard" / "index.html"


@app.get("/api/state")
async def get_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"status": "offline", "error": "No state file found"}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    if DASHBOARD_PATH.exists():
        return HTMLResponse(DASHBOARD_PATH.read_text())
    return HTMLResponse("<h1>Dashboard not found</h1>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
