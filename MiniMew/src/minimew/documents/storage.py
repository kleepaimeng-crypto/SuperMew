"""源文件只按服务端 UUID 寻址；完整文件保留到 DB 确认后再决定是否清理。"""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class InvalidSource(Exception):
    pass


class SourceTooLarge(InvalidSource):
    pass


@dataclass(frozen=True)
class Source:
    filename: str
    format: str
    content: bytes
    sha256: str


def validate_source(filename: str, content: bytes, max_bytes: int) -> Source:
    if len(content) > max_bytes:
        raise SourceTooLarge
    if (
        not filename
        or len(filename) > 255
        or any(c in filename for c in '/\\<>:"|?*')
        or any(ord(c) < 32 for c in filename)
        or filename.endswith((" ", "."))
    ):
        raise InvalidSource("Invalid filename")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".md", ".txt"} or not 0 < len(content) <= max_bytes:
        raise InvalidSource("Expected a non-empty Markdown/TXT within the upload limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvalidSource("UTF-8 source required") from None
    if not text.strip() or "\0" in text:
        raise InvalidSource("Text source required")
    return Source(
        filename,
        "markdown" if suffix == ".md" else "text",
        content,
        hashlib.sha256(content).hexdigest(),
    )


class SourceStore:
    def __init__(self, root: Path):
        self.root = (root if root.is_absolute() else PROJECT_ROOT / root).resolve()

    def path(self, version_id: UUID) -> Path:
        return self.root / f"{UUID(str(version_id))}.source"

    def write(self, version_id: UUID, source: Source) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path(version_id)
        if target.exists():
            raise FileExistsError("Version source already exists")
        fd, name = tempfile.mkstemp(prefix=".upload-", dir=self.root)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(source.content)
                stream.flush()
                os.fsync(stream.fileno())
            # link is atomic and refuses to replace an existing immutable version.
            os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, version_id: UUID, expected_sha256: str) -> bytes:
        path = self.path(version_id)
        if path.is_symlink():
            raise OSError("Source links are not supported")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise OSError("Source integrity check failed")
        return content

    def copy_version(self, source_version_id: UUID, version_id: UUID, digest: str) -> None:
        content = self.read(source_version_id, digest)
        try:
            self.write(version_id, Source("", "", content, digest))
        except FileExistsError:
            self.read(version_id, digest)

    def remove_orphan(self, version_id: UUID) -> None:
        path = self.path(version_id)
        if path.is_symlink():
            raise OSError("Source links are not supported")
        path.unlink(missing_ok=True)
