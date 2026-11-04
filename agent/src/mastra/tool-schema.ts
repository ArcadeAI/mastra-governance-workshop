type Schema = Record<string, unknown>;
const object = (value: unknown): Schema => value && typeof value === 'object' && !Array.isArray(value) ? value as Schema : {};
const TYPES: Record<string, string> = { string: 'string', integer: 'integer', number: 'number', float: 'number', boolean: 'boolean', json: 'object', object: 'object', array: 'array' };

function nested(schema: Schema): Schema {
  const children = object(schema.inner_properties);
  if (!Object.keys(children).length) return {};
  const out: Schema = { properties: Object.fromEntries(Object.entries(children).map(([key, child]) => [key, valueSchema(object(child))])) };
  if (Array.isArray(schema.inner_required_keys) && schema.inner_required_keys.length) out.required = schema.inner_required_keys.filter((key) => typeof key === 'string');
  return out;
}

function valueSchema(schema: Schema): Schema {
  const type = TYPES[String(schema.val_type)] ?? 'string';
  const out: Schema = { type };
  if (typeof schema.description === 'string' && schema.description) out.description = schema.description.replace(/\s+/g, ' ').trim();
  if (type === 'array') {
    const inner = TYPES[String(schema.inner_val_type)] ?? 'string';
    out.items = { type: inner, ...(inner === 'object' ? nested(schema) : {}) };
  } else if (type === 'object') Object.assign(out, nested(schema));
  if (Array.isArray(schema.enum) && schema.enum.length) out.enum = schema.enum;
  return out;
}

/** Engine selections contain Arcade parameter descriptors; MCP selections contain JSON Schema already. */
export function inputSchema(raw: unknown): Schema {
  if (!Array.isArray(raw)) return raw && typeof raw === 'object' && Object.keys(raw).length ? raw as Schema : { type: 'object', properties: {} };
  const properties = new Map<string, Schema>();
  const required = new Set<string>();
  for (const entry of raw) {
    const parameter = object(entry);
    if (typeof parameter.name !== 'string' || !parameter.name) continue;
    const schema = valueSchema(object(parameter.value_schema));
    if (typeof parameter.description === 'string' && parameter.description) schema.description = parameter.description.replace(/\s+/g, ' ').trim();
    properties.set(parameter.name, schema);
    if (parameter.required === true) required.add(parameter.name);
  }
  return { type: 'object', properties: Object.fromEntries(properties), ...(required.size ? { required: [...required] } : {}) };
}
