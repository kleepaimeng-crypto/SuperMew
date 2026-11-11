"""模型运行配置独立于回答模型和文档构建身份。"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class EmbeddingRuntimeSettings(BaseSettings):
    # 命令行编码不需要数据库、Redis 或回答模型密钥。
    model_config = SettingsConfigDict(env_prefix="MINIMEW_EMBEDDING_", frozen=True)

    cache_dir: Path = Path("data/models")
    batch_size: int = Field(default=4, ge=1, le=64)

    @property
    def resolved_cache_dir(self) -> Path:
        return (PROJECT_ROOT / self.cache_dir).resolve()
