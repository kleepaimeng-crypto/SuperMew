"""固定发布目录；显式加载、校验和内容锁定，不解析用户路径。"""

from importlib.resources import files

from minimew.execution.messages import json_object
from minimew.skills.schemas import SkillBundle, SkillSnapshot, canonical, content_digest

# 文件及内容摘要在包发布时一起更新，同一版本不得原地替换正文。
RELEASES = (
    (
        "default",
        "1.0.0",
        "default-v1.json",
        "ebe889f699d8886dfa24dd6e1e2a6b927429c88d783b7dec1589e16db356ebdc",
    ),
    (
        "document-qa",
        "1.0.0",
        "document-qa-v1.json",
        "9cf0a1d97cb9fb4d8e7e7c7ff7701f8155292e00ef5e5c913c5e375935edae8e",
    ),
)


class SkillUnavailable(Exception):
    pass


def load_skill(selection=None):
    identity = selection.id if selection else "default"
    version = selection.version if selection else None
    release = next(
        (
            r
            for r in reversed(RELEASES)
            if r[0] == identity and (version is None or r[1] == version)
        ),
        None,
    )
    if release is None:
        raise SkillUnavailable("Unknown Skill release")
    try:
        data = files("minimew.skills").joinpath("builtin", release[2]).read_text(encoding="utf-8")
        bundle = SkillBundle.model_validate_json(canonical(json_object(data, 24000)))
        payload = bundle.model_dump(mode="json")
        digest = content_digest(payload)
        if (bundle.id, bundle.version, digest) != (release[0], release[1], release[3]):
            raise ValueError("Skill release content changed")
        return SkillSnapshot.model_validate_json(canonical({**payload, "digest": digest}))
    except (ValueError, OSError, UnicodeError):
        raise SkillUnavailable("Invalid builtin Skill") from None


def catalog():
    from minimew.skills.schemas import SkillSelection, summary

    return tuple(summary(load_skill(SkillSelection(id=r[0], version=r[1]))) for r in RELEASES)
