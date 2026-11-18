"""从正式路由导出 OpenAPI；不创建应用、不读取 Secret、不连接数据库。"""

import json
from pathlib import Path

from fastapi import FastAPI

from minimew.api.auth import router as auth_router
from minimew.api.documents import router as documents_router
from minimew.api.evaluations import router as evaluations_router
from minimew.api.runs import router as runs_router
from minimew.api.threads import router as threads_router
from minimew.projects import router as projects_router

app = FastAPI(title="MiniMew API", version="1.0.0")
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(threads_router)
app.include_router(runs_router)
app.include_router(documents_router)
app.include_router(evaluations_router)
target = Path(__file__).resolve().parents[1] / "frontend" / "openapi.json"
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("Exported auth, projects, Threads, Runs, documents and evaluations from canonical routes")
