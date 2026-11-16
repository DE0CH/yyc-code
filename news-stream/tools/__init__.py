"""YYC's tools for AI (docs/agent-platform.md): one registry that the REST API (/app/api/v1, OpenAPI 3.1) and the MCP
server (/mcp) both call, so they can't disagree.

A tool = a function + its JSON Schema + a description written for a model (+ a price for paid tools). call() checks
the arguments (an error names what to fix), runs the tool, and caps the answer's size; every call gets a ctx
{"email": the Access account}.
"""
import json

REGISTRY = {}
MAX_CHARS = 60000  # a tool answer the model reads: well under 25k tokens


class Tool:
    def __init__(self, name, fn, description, schema, price=None):
        self.name, self.fn, self.description, self.schema, self.price = name, fn, description, schema, price

    @property
    def paid(self):
        return self.price is not None


def tool(name, description, properties=None, required=(), price=None):
    """register fn as a tool; properties: JSON Schema of each argument (type/description/enum/default/items)"""
    def wrap(fn):
        REGISTRY[name] = Tool(name, fn, description,
                              {"type": "object", "properties": properties or {}, "required": list(required),
                               "additionalProperties": False}, price)
        return fn
    return wrap


def check_args(schema, v, path="arguments"):
    """v against the JSON Schema subset the tools use (object/properties/required, string, integer, number, boolean,
    array/items, enum, minimum/maximum); raises ValueError naming the first problem, so the model can correct it"""
    t = schema.get("type")
    if t == "object":
        if not isinstance(v, dict):
            raise ValueError(f"{path} must be an object")
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if v.get(k) in (None, ""):
                raise ValueError(f"{path}.{k} is required")
        for k, x in v.items():
            if k not in props:
                raise ValueError(f"{path}.{k} is not a parameter (allowed: {', '.join(props) or 'none'})")
            if x is not None:
                check_args(props[k], x, f"{path}.{k}")
    elif t == "string" and not isinstance(v, str):
        raise ValueError(f"{path} must be a string")
    elif t == "integer" and (isinstance(v, bool) or not isinstance(v, int)):
        raise ValueError(f"{path} must be an integer")
    elif t == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
        raise ValueError(f"{path} must be a number")
    elif t == "boolean" and not isinstance(v, bool):
        raise ValueError(f"{path} must be true or false")
    elif t == "array":
        if not isinstance(v, list):
            raise ValueError(f"{path} must be an array")
        for i, x in enumerate(v):
            check_args(schema.get("items", {}), x, f"{path}[{i}]")
    if "enum" in schema and v not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in schema and v < schema["minimum"]:
            raise ValueError(f"{path} must be at least {schema['minimum']}")
        if "maximum" in schema and v > schema["maximum"]:
            raise ValueError(f"{path} must be at most {schema['maximum']}")


def coerce(schema, args):
    """query-string arguments (all strings, from GET /app/api/v1/<tool>?…) → the schema's types"""
    out = {}
    for k, v in args.items():
        t = schema["properties"].get(k, {}).get("type")
        try:
            if t == "integer":
                v = int(v)
            elif t == "number":
                v = float(v)
            elif t == "boolean":
                v = str(v).lower() in ("1", "true", "yes")
            elif t == "array" and isinstance(v, str):
                v = json.loads(v) if v.startswith("[") else [x for x in v.split(",") if x]
        except ValueError:
            pass  # check_args names it
        out[k] = v
    return out


def call(name, args, ctx=None):
    """run a tool for the model, the API or MCP; ValueError/LookupError → a message for the caller to act on"""
    from . import data, live, skills, spend  # noqa: F401  (registers the tools)
    ctx = ctx if ctx is not None else {}
    t = REGISTRY.get(name)
    if not t:
        raise ValueError(f"unknown tool {name} (tools: {', '.join(sorted(REGISTRY))})")
    args = {k: v for k, v in (args or {}).items() if v is not None}
    check_args(t.schema, args)
    if t.paid:
        return spend.charged(t, args, ctx)
    return fit(t.fn(args, ctx))


def fit(result):
    """keep an answer under MAX_CHARS: trim its longest list, saying how much was left out and how to get the rest"""
    s = json.dumps(result, ensure_ascii=False, default=str)
    if len(s) <= MAX_CHARS or not isinstance(result, dict):
        return result
    key = max((k for k, v in result.items() if isinstance(v, list)), key=lambda k: len(json.dumps(result[k], default=str)), default=None)
    if not key:
        return {"error": "answer too long; narrow the request (a shorter period, a profile, a filter)"}
    items, keep = result[key], len(result[key])
    while keep > 1 and len(json.dumps(dict(result, **{key: items[:keep]}), ensure_ascii=False, default=str)) > MAX_CHARS:
        keep = keep * 3 // 4
    return dict(result, **{key: items[:keep], "truncated": f"{len(items) - keep} more {key} left out to keep this short: "
                                                        "ask for fewer (limit), a later page (offset) or use a filter"})


def tools(paid=True):
    from . import data, live, skills, spend  # noqa: F401
    return [t for t in REGISTRY.values() if paid or not t.paid]


def openai_tools():
    """the registry as OpenAI/OpenRouter function-calling tools"""
    return [{"type": "function", "function": {"name": t.name, "description": describe(t), "parameters": t.schema}}
            for t in tools()]


def describe(t):
    return t.description + (f" PAID: {t.price['text']}." if t.paid else "")


def openapi(base="/app/api/v1"):
    """OpenAPI 3.1 for the REST API: GET (query string) or POST (JSON body) /app/api/v1/<tool>"""
    paths = {}
    for t in tools():
        q = [{"name": k, "in": "query", "required": k in t.schema["required"], "description": p.get("description", ""),
              "schema": {x: y for x, y in p.items() if x != "description"}} for k, p in t.schema["properties"].items()]
        resp = {"200": {"description": "the tool's answer", "content": {"application/json": {"schema": {"type": "object"}}}},
                "400": {"description": "bad arguments or the tool refused; `error` says what to change"}}
        paths[f"{base}/{t.name}"] = {
            "get": {"operationId": t.name, "summary": describe(t)[:120], "description": describe(t), "parameters": q, "responses": resp},
            "post": {"operationId": t.name + "_post", "summary": describe(t)[:120], "description": describe(t),
                     "requestBody": {"content": {"application/json": {"schema": t.schema}}}, "responses": resp}}
    return {"openapi": "3.1.0",
            "info": {"title": "YYC news monitoring data", "version": "1",
                     "description": "Read-only access to what YYC's news stream collects (posts, profiles, statistics, "
                                    "reports, WiseSearch and social-search results) and live paid searches. Behind "
                                    "Cloudflare Access; paid tools' cost is recorded per caller."},
            "servers": [{"url": "https://newswatch.yyc-internal.com"}], "paths": paths}
