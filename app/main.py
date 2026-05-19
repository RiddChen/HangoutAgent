import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.agents.email_agent import email_agent
from app.api.v1 import chat, travel

from app.api.v1 import travel


@asynccontextmanager
async def lifespan(app: FastAPI):
    email_agent_started = False
    if os.path.exists("credentials.json") or os.path.exists("token.json"):
        await email_agent.init()
        email_agent_started = True
    else:
        print("EmailAgent skipped: credentials.json/token.json not found.")

    yield

    if email_agent_started:
        await email_agent.close()

app = FastAPI(
    title="TripCrew 出行企划多智能体 API",
    lifespan=lifespan,
)
app.include_router(travel.router, prefix="/api/v1", tags=["出行企划 Agent"])
app.include_router(chat.router, prefix="/api/v1", tags=["邮件 Agent"])



@app.get("/health")
async def health():
    return {"status": "ok"}


static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


@app.get("/{path:path}", include_in_schema=False)
async def serve_frontend(path: str):
    if path.startswith("api/"):
        return JSONResponse({"error": "Not Found"}, status_code=404)

    file_path = os.path.join(static_dir, path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)

    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)

    return {"message": "TripCrew is running", "status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8002, reload=True)