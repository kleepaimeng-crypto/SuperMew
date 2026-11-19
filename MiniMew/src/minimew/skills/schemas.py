"""公开选择与私有不可变内容快照，包内容不会成为账户权限。"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
Version = Annotated[str, Field(pattern=r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$")]


class SkillModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class SkillSelection(SkillModel):
    id: Name
    version: Version | None = None


class SkillResource(SkillModel):
    id: Name
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=4096)


class SkillBundle(SkillModel):
    schema_version: Literal[1]
    id: Name
    version: Version
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=240)
    instructions: str = Field(max_length=2000)
    tools: tuple[Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")], ...] = Field(max_length=8)
    resources: tuple[SkillResource, ...] = Field(max_length=4)

    @model_validator(mode="after")
    def bounded(self):
        if len(set(self.tools)) != len(self.tools) or len({r.id for r in self.resources}) != len(
            self.resources
        ):
            raise ValueError("Unique Skill tools and resources required")
        encoded = canonical(self.model_dump(mode="json"))
        if len(encoded) > 24000 or "\\u0000" in encoded:
            raise ValueError("Invalid Skill content budget")
        if bool(self.resources) != ("read_skill_resource" in self.tools):
            raise ValueError("Resource tool and resource directory must agree")
        return self


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def content_digest(bundle):
    return hashlib.sha256(canonical(bundle).encode("utf-8")).hexdigest()


class SkillSnapshot(SkillBundle):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def integrity(self):
        if self.digest != content_digest(self.model_dump(mode="json", exclude={"digest"})):
            raise ValueError("Skill snapshot digest mismatch")
        return self


class SkillSummary(SkillModel):
    id: Name
    version: Version
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=240)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def summary(snapshot):
    return SkillSummary(**{key: getattr(snapshot, key) for key in SkillSummary.model_fields})


def parse_snapshot(value):
    # JSON是持久形状；strict tuple字段通过JSON校验，禁止宽松Python转换。
    return SkillSnapshot.model_validate_json(canonical(value))
