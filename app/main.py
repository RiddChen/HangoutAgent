import os
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.agents.hangout.orchestrator import hangout_orchestrator
from app.api.v1 import hangout


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化主 Agent
    await hangout_orchestrator.init()
    yield
    # 关闭时清理资源
    await hangout_orchestrator.close()


app = FastAPI(title="HangoutAgent 出行企划助手", lifespan=lifespan)
app.include_router(hangout.router, prefix="/api/v1")

# 静态文件（前端）
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8002, reload=True)
