import source from "../../../contracts/run_event_v1.json?raw";
import type { RunEvent } from "../generated/run-event-v1";

type Schema = {
  $ref?: string;
  $defs?: Record<string, Schema>;
  type?: string;
  const?: unknown;
  enum?: unknown[];
  required?: string[];
  properties?: Record<string, Schema>;
  additionalProperties?: boolean;
  minimum?: number;
  minLength?: number;
  maxLength?: number;
  format?: string;
  oneOf?: Schema[];
};
const contract = JSON.parse(source) as Schema;
const supported = new Set([
  "$schema",
  "$id",
  "title",
  "$defs",
  "$ref",
  "type",
  "const",
  "enum",
  "required",
  "properties",
  "additionalProperties",
  "minimum",
  "minLength",
  "maxLength",
  "format",
  "oneOf",
]);

function checkSchema(schema: Schema): void {
  if (Object.keys(schema).some((key) => !supported.has(key)))
    throw new Error("Unsupported event schema keyword");
  if (schema.type && !["object", "string", "integer"].includes(schema.type))
    throw new Error("Unsupported event schema type");
  if (schema.format && !["uuid", "date-time"].includes(schema.format))
    throw new Error("Unsupported event schema format");
  if (schema.$ref && !contract.$defs?.[schema.$ref.replace(/^#\/\$defs\//, "")])
    throw new Error("Unsupported event schema reference");
  Object.values(schema.properties ?? {}).forEach(checkSchema);
  Object.values(schema.$defs ?? {}).forEach(checkSchema);
  schema.oneOf?.forEach(checkSchema);
}
checkSchema(contract);

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export function isUuid(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i.test(value)
  );
}
export function isDateTime(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/i.exec(
      value,
    );
  if (!match || !Number.isFinite(Date.parse(value))) return false;
  const [, year, month, day, hour, minute, second, zoneHour, zoneMinute] =
    match;
  return (
    Number(month) >= 1 &&
    Number(month) <= 12 &&
    Number(day) >= 1 &&
    Number(day) <=
      new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate() &&
    Number(hour) < 24 &&
    Number(minute) < 60 &&
    Number(second) < 60 &&
    Number(zoneHour ?? 0) < 24 &&
    Number(zoneMinute ?? 0) < 60
  );
}

function matches(value: unknown, schema: Schema): boolean {
  if (schema.$ref)
    return matches(
      value,
      contract.$defs![schema.$ref.slice("#/$defs/".length)]!,
    );
  if ("const" in schema && value !== schema.const) return false;
  if (schema.enum && !schema.enum.includes(value)) return false;
  if (
    schema.type === "integer" &&
    (!Number.isSafeInteger(value) ||
      (typeof value === "number" &&
        schema.minimum !== undefined &&
        value < schema.minimum))
  )
    return false;
  if (schema.type === "string") {
    if (typeof value !== "string") return false;
    const length = [...value].length;
    if (
      (schema.minLength !== undefined && length < schema.minLength) ||
      (schema.maxLength !== undefined && length > schema.maxLength)
    )
      return false;
    if (schema.format === "uuid" && !isUuid(value)) return false;
    if (schema.format === "date-time" && !isDateTime(value)) return false;
  }
  if (schema.type === "object" && !isRecord(value)) return false;
  if (schema.properties) {
    if (!isRecord(value)) return false;
    if (schema.required?.some((key) => !Object.hasOwn(value, key)))
      return false;
    if (
      schema.additionalProperties === false &&
      Object.keys(value).some((key) => !Object.hasOwn(schema.properties!, key))
    )
      return false;
    for (const [key, child] of Object.entries(schema.properties))
      if (Object.hasOwn(value, key) && !matches(value[key], child))
        return false;
  }
  return (
    !schema.oneOf ||
    schema.oneOf.filter((child) => matches(value, child)).length === 1
  );
}

export function isEventValue(
  value: unknown,
  definition: "RunStatus" | "MessageStatus" | "ErrorCode",
): boolean {
  return matches(value, contract.$defs![definition]!);
}
export class EventProtocolError extends Error {
  constructor() {
    super("Invalid event stream");
  }
}
export function parseRunEvent(value: unknown): RunEvent {
  if (!matches(value, contract)) throw new EventProtocolError();
  return value as RunEvent;
}
