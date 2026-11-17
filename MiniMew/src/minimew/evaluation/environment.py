"""执行身份只记录源码/已安装依赖摘要，不记录本机路径或凭据。"""

from importlib.metadata import distributions
from pathlib import Path

from minimew.evaluation.protocol import fingerprint, text_hash


def execution_environment():
    root = Path(__file__).resolve().parents[1]
    sources = [
        (
            str(path.relative_to(root)).replace("\\", "/"),
            text_hash(path.read_text(encoding="utf-8")),
        )
        for path in sorted(root.rglob("*.py"))
    ]
    packages = sorted(
        (dist.metadata["Name"].lower(), dist.version)
        for dist in distributions()
        if dist.metadata["Name"]
    )
    return {"implementation": fingerprint(sources), "dependencies": fingerprint(packages)}
