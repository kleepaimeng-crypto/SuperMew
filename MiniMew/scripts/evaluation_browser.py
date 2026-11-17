"""仅显式验收时启动自有API和浏览器；工作台调用真实评测接口。"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from check_documents_frontend import stop
from check_models import ROOT


async def run_browser(request, credentials, database, directory):
    folder = ROOT / ".artifacts" / ("evaluation-browser-" + uuid4().hex)
    folder.mkdir(parents=True)
    dataset = Path(directory) / "dataset.json"
    scenarios = Path(directory) / "scenarios.json"
    dataset.write_text(request.dataset.model_dump_json(), encoding="utf-8")
    scenarios.write_text(
        json.dumps([s.model_dump(mode="json") for s in request.scenarios], ensure_ascii=False),
        encoding="utf-8",
    )
    env = dict(
        os.environ,
        MINIMEW_INTEGRATION_DB=database,
        MINIMEW_CHAT_BROWSER_TESTS="1",
        MINIMEW_EVALUATION_BROWSER_TESTS="1",
        MINIMEW_EVAL_USERNAME=credentials["username"],
        MINIMEW_EVAL_PASSWORD=credentials["password"],
        MINIMEW_EVAL_CORPUS=str(request.corpus_id),
        MINIMEW_EVAL_DATASET=str(dataset),
        MINIMEW_EVAL_SCENARIOS=str(scenarios),
        MINIMEW_EVAL_PYTHON=sys.executable,
        MINIMEW_EVAL_ROOT=str(ROOT),
    )
    address = folder / "address.json"
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise RuntimeError("npm unavailable")
    log = (folder / "api.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/frontend_process.py"), str(address)],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        text=True,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(200):
                if process.poll() is not None:
                    raise RuntimeError("Owned API exited")
                if address.exists():
                    base = f"http://127.0.0.1:{json.loads(address.read_text())['port']}"
                    try:
                        if (await client.get(base + "/health/ready", timeout=1)).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Owned API readiness timed out")
        await asyncio.to_thread(
            subprocess.run,
            [npm, "run", "test:e2e", "--", "tests/evaluations-live.spec.ts"],
            cwd=ROOT / "frontend",
            env={**env, "MINIMEW_API_TARGET": base},
            check=True,
            timeout=300,
        )
    finally:
        try:
            await stop(process)
        finally:
            log.close()
    return str(folder.relative_to(ROOT))
