"""从 MiniMew 自己的 JSON Schema 生成消费者；不访问数据库、不导入上级 backend。"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def field_type(spec, *, python):
    if "$ref" in spec:
        if set(spec) != {"$ref"} or not spec["$ref"].startswith("#/$defs/"):
            raise ValueError("Unsupported contract reference")
        return spec["$ref"].split("/")[-1]
    allowed = {
        "type",
        "const",
        "enum",
        "format",
        "minimum",
        "minLength",
        "maxLength",
        "pattern",
        "maximum",
        "items",
        "maxItems",
    }
    if set(spec) - allowed:
        raise ValueError("Unsupported field keywords; update generator and tests")
    if spec.get("type") == "array":
        item = field_type(spec["items"], python=python)
        if set(spec) != {"type", "items", "maxItems"}:
            raise ValueError("Bounded array required")
        return f"list[{item}]" if python else f"{item}[]"
    if "const" in spec or "enum" in spec:
        values = spec.get("enum", [spec.get("const")])
        literals = ", ".join(repr(v) for v in values)
        return f"Literal[{literals}]" if python else " | ".join(json.dumps(v) for v in values)
    if spec.get("format") == "uuid":
        return "UUID" if python else "string"
    if spec.get("format") == "date-time":
        return "AwareDatetime" if python else "string"
    if spec.get("format"):
        raise ValueError("Unsupported string format")
    types = (
        {"string": "str", "integer": "int"} if python else {"string": "string", "integer": "number"}
    )
    return types[spec["type"]]


def object_fields(spec, *, python):
    if set(spec) != {"type", "additionalProperties", "required", "properties"}:
        raise ValueError("Unsupported object shape; update generator and tests")
    if spec["type"] != "object" or spec["additionalProperties"] is not False:
        raise ValueError("Contract object must be closed")
    if set(spec["required"]) != set(spec["properties"]):
        raise ValueError("All current contract fields must be required")
    lines = []
    for name, prop in spec["properties"].items():
        kind = field_type(prop, python=python)
        bounds = {
            "minimum": "ge",
            "maximum": "le",
            "maxItems": "max_length",
            "minLength": "min_length",
            "maxLength": "max_length",
            "pattern": "pattern",
        }
        args = [f"{target}={prop[key]!r}" for key, target in bounds.items() if key in prop]
        suffix = f" = Field({', '.join(args)})" if python and args else ""
        lines.append(f"    {name}: {kind}{suffix}" if python else f"  {name}: {kind};")
    if python:
        integer_constants = [
            name
            for name, prop in spec["properties"].items()
            if prop.get("type") == "integer" and "const" in prop
        ]
        for name in integer_constants:
            lines.append(
                f'\n    @field_validator("{name}", mode="before")\n'
                "    @classmethod\n"
                "    def integer_constant(cls, value):\n"
                "        if type(value) is not int:\n"
                '            raise ValueError("Expected an integer constant")\n'
                "        return value"
            )
    return "\n".join(lines)


def ts_alias(name, values):
    union = " | ".join(values)
    line = f"export type {name} = {union};\n"
    if len(line.rstrip()) <= 80:
        return line
    if len(f"  {union};") <= 80:
        return f"export type {name} =\n  {union};\n"
    return f"export type {name} =\n" + "\n".join(f"  | {v}" for v in values) + ";\n"


def render(schema):
    expected = {
        "$schema",
        "$id",
        "title",
        "type",
        "additionalProperties",
        "required",
        "properties",
        "$defs",
        "oneOf",
    }
    if set(schema) != expected:
        raise ValueError("Unsupported root keywords; update generator and tests")
    if set(schema["required"]) != set(schema["properties"]):
        raise ValueError("All event envelope fields must be required")
    definitions = schema["$defs"]
    py = [
        "# Generated from contracts/run_event_v1.json. Do not edit.\n",
        "from typing import Annotated, Literal\nfrom uuid import UUID\n\n",
        "from pydantic import (\n"
        "    AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, field_validator\n"
        ")\n\n",
        "class ContractModel(BaseModel):\n"
        "    model_config = ConfigDict(\n"
        '        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True\n'
        "    )\n\n",
    ]
    ts = ["// Generated from contracts/run_event_v1.json. Do not edit.\n"]
    for name, spec in definitions.items():
        if spec["type"] == "object":
            py.append(f"class {name}(ContractModel):\n{object_fields(spec, python=True)}\n\n")
            ts.append(f"export interface {name} {{\n{object_fields(spec, python=False)}\n}}\n")
        else:
            py.append(f"{name} = {field_type(spec, python=True)}\n\n")
            values = spec.get("enum", [spec.get("const")])
            ts.append(ts_alias(name, [json.dumps(v) for v in values]))
    envelope = {
        key: schema[key] for key in ("type", "additionalProperties", "required", "properties")
    }
    # type/data 由 oneOf 的每个闭合分支定义，其余 envelope 字段只生成一次。
    envelope["properties"] = {
        k: v for k, v in schema["properties"].items() if k not in {"type", "data"}
    }
    envelope["required"] = [k for k in schema["required"] if k not in {"type", "data"}]
    py.append(f"class RunEventBase(ContractModel):\n{object_fields(envelope, python=True)}\n\n")
    ts.append(f"export interface RunEventBase {{\n{object_fields(envelope, python=False)}\n}}\n")
    variants, event_types = [], []
    for branch in schema["oneOf"]:
        if set(branch) != {"properties"} or set(branch["properties"]) != {"type", "data"}:
            raise ValueError("Unsupported event branch")
        props = branch["properties"]
        if set(props["type"]) != {"const"}:
            raise ValueError("Unsupported event discriminator")
        tag = props["type"]["const"]
        event_types.append(tag)
        name = "".join(part.title() for part in tag.split(".")) + "Event"
        data_type = field_type(props["data"], python=True)
        if data_type not in definitions:
            raise ValueError("Unknown event payload")
        variants.append(name)
        py.append(
            f"class {name}(RunEventBase):\n    type: Literal[{tag!r}]\n    data: {data_type}\n\n"
        )
        ts.append(
            f"export interface {name} extends RunEventBase {{\n"
            f'  type: "{tag}";\n  data: {data_type};\n}}\n'
        )
    if schema["properties"]["type"] != {"type": "string", "enum": event_types}:
        raise ValueError("Event enum and branches differ")
    if schema["properties"]["data"] != {"type": "object"}:
        raise ValueError("Unsupported envelope data")
    union = " | ".join(variants)
    py.append(
        f'RunEvent = Annotated[{union}, Field(discriminator="type")]\n'
        "EVENT_ADAPTER = TypeAdapter(RunEvent)\n"
    )
    ts.append(ts_alias("RunEvent", variants))
    # 使用已锁定的开发工具格式化生成输出，--check 也比较同一确定性结果。
    formatted = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--stdin-filename", "run_event_v1.py", "-"],
        input="".join(py),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return formatted, "".join(ts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    schema = json.loads((ROOT / "contracts/run_event_v1.json").read_text(encoding="utf-8"))
    outputs = render(schema)
    targets = [
        ROOT / "src/minimew/generated/run_event_v1.py",
        ROOT / "frontend/src/generated/run-event-v1.ts",
    ]
    for target, content in zip(targets, outputs, strict=True):
        if args.check:
            if not target.exists() or target.read_bytes() != content.encode("utf-8"):
                raise SystemExit(f"Generated event contract differs: {target.relative_to(ROOT)}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
    print("Event contract generation check passed" if args.check else "Generated event consumers")


if __name__ == "__main__":
    main()
