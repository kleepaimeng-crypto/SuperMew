"""资源读取复用原Tool入口；数据只来自当前Run快照。"""

from typing import Annotated

from pydantic import Field

from minimew.execution.tools import ToolFailure, ToolModel, ToolSpec

RESOURCE_CAPABILITY = "skill.resources.read"


class ResourceInput(ToolModel):
    resource_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]


class ResourceResult(ToolModel):
    skill_id: str
    skill_version: str
    resource_id: str
    name: str
    content: str


def resource_spec():
    def authorize(context, arguments):
        return context.skill is not None and any(
            r.id == arguments.resource_id for r in context.skill.resources
        )

    async def read(context, arguments):
        resource = next((r for r in context.skill.resources if r.id == arguments.resource_id), None)
        if resource is None:
            raise ToolFailure("TOOL_PERMISSION_DENIED")
        return ResourceResult(
            skill_id=context.skill.id,
            skill_version=context.skill.version,
            resource_id=resource.id,
            name=resource.name,
            content=resource.content,
        )

    return ToolSpec(
        "read_skill_resource",
        "读取当前任务包目录中的只读参考资源。只能传resource_id。",
        ResourceInput,
        ResourceResult,
        RESOURCE_CAPABILITY,
        authorize,
        read,
    )
