// The OpenAPI 3.1 description of /api/v2 (src/api.js), served at /api/v2/openapi.json.
const err = { description: "refused: {error, message}", content: { "application/json": { schema: { $ref: "#/components/schemas/Error" } } } };
const json = (schema, description = "OK") => ({ description, content: { "application/json": { schema } } });
const id = { name: "id", in: "path", required: true, schema: { type: "string", pattern: "^[a-z0-9][a-z0-9-]{0,39}$" } };
const pid = { name: "id", in: "path", required: true, schema: { type: "string" } };

export const OPENAPI = {
  openapi: "3.1.0",
  info: { title: "YYC labels", version: "2", description: "Labels for YYC's NIIMBOT M3, sent as finished bitmaps: 848 × 590 dots, 1 bit per dot (1 = black), rows of 106 bytes top to bottom, the leftmost dot in the highest bit; 62,540 bytes a label, no header. Templates hold design.json, design.md, four example labels and their own files; changing one needs its lock." },
  paths: {
    "/api/v2/prints": {
      post: {
        summary: "Send labels to print: they appear on a print page, where staff connect the printer and print them",
        parameters: [
          { name: "title", in: "query", schema: { type: "string", maxLength: 120 }, description: "shown on the print page" },
          { name: "template", in: "query", schema: { type: "string" }, description: "the template they were drawn from" },
          { name: "density", in: "query", schema: { type: "integer", minimum: 1, maximum: 5, default: 1 }, description: "print darkness" },
        ],
        requestBody: { required: true, content: { "application/octet-stream": { schema: { type: "string", format: "binary" }, description: "1-500 labels, one after another" } } },
        responses: { 201: json({ $ref: "#/components/schemas/Print" }, "the print request, with its page"), 400: err, 413: err },
      },
      get: { summary: "The latest print requests", responses: { 200: json({ type: "array", items: { $ref: "#/components/schemas/Print" } }) } },
    },
    "/api/v2/prints/{id}": { get: { summary: "One print request: waiting, printing, printed or cancelled", parameters: [pid], responses: { 200: json({ $ref: "#/components/schemas/Print" }), 404: err } } },
    "/api/v2/prints/{id}/labels/{n}.bin": {
      get: { summary: "One label of a print request", parameters: [pid, { name: "n", in: "path", required: true, schema: { type: "integer", minimum: 1 } }],
        responses: { 200: { description: "the label", content: { "application/octet-stream": {} } }, 404: err } },
    },
    "/api/v2/prints/{id}/status": {
      post: { summary: "Report progress or cancel (the print page)", parameters: [pid],
        requestBody: { content: { "application/json": { schema: { type: "object", properties: { status: { const: "cancelled" }, printed: { type: "integer" } } } } } },
        responses: { 200: json({ $ref: "#/components/schemas/Print" }), 404: err, 409: err } },
    },
    "/api/v2/templates": { get: { summary: "Every template", responses: { 200: json({ type: "array", items: { $ref: "#/components/schemas/Template" } }) } } },
    "/api/v2/templates/{id}": {
      get: { summary: "One template: name, version, lock, files", parameters: [id], responses: { 200: json({ $ref: "#/components/schemas/Template" }), 404: err } },
      put: {
        summary: "Create or replace the whole template (needs its lock)", parameters: [id],
        requestBody: { required: true, content: { "multipart/form-data": { schema: { type: "object", required: ["name", "design.json", "design.md", "examples/1.bin", "examples/2.bin", "examples/3.bin", "examples/4.bin"],
          properties: { name: { type: "string" }, "design.json": { type: "string", format: "binary" }, "design.md": { type: "string", format: "binary" },
            "examples/1.bin": { type: "string", format: "binary" }, "examples/2.bin": { type: "string", format: "binary" }, "examples/3.bin": { type: "string", format: "binary" }, "examples/4.bin": { type: "string", format: "binary" } },
          additionalProperties: { type: "string", format: "binary", description: "files/<name>: anything else the template needs" } } } } },
        responses: { 200: json({ $ref: "#/components/schemas/Template" }), 400: err, 409: err },
      },
      delete: { summary: "Delete the template (needs its lock)", parameters: [id], responses: { 200: json({ type: "object" }), 409: err } },
    },
    "/api/v2/templates/{id}/files/{path}": { get: { summary: "One of the template's files", parameters: [id, { name: "path", in: "path", required: true, schema: { type: "string" } }], responses: { 200: { description: "the file" }, 404: err } } },
    "/api/v2/templates/{id}/lock": {
      post: { summary: "Take the lock (409 locked: who holds it and since when)", parameters: [id], responses: { 200: json({ type: "object" }), 409: err } },
      delete: { summary: "Release the lock", parameters: [id], responses: { 200: json({ type: "object" }), 409: err } },
    },
    "/api/v2/templates/{id}/lock/override": { post: { summary: "Take the lock over from whoever holds it", parameters: [id], responses: { 200: json({ type: "object" }) } } },
    "/api/v2/fonts": { get: { summary: "The fonts: family, the name staff see, weights, files (GET /api/v2/fonts/<file>)", responses: { 200: json({ type: "object" }) } } },
    "/api/v2/fonts/{file}": { get: { summary: "One font file (Content-Encoding: br; curl --compressed)", parameters: [{ name: "file", in: "path", required: true, schema: { type: "string" } }], responses: { 200: { description: "TrueType" }, 404: err } } },
  },
  components: {
    schemas: {
      Error: { type: "object", properties: { error: { type: "string" }, message: { type: "string" }, holder: { type: "string" }, since: { type: "string" }, takenBy: { type: "string" }, at: { type: "string" } } },
      Print: { type: "object", properties: { id: { type: "string" }, title: { type: ["string", "null"] }, template: { type: ["string", "null"] }, density: { type: "integer" }, labels: { type: "integer" },
        status: { enum: ["waiting", "printing", "printed", "cancelled"] }, printed: { type: "integer" }, createdAt: { type: "string" }, createdBy: { type: "string" }, page: { type: "string" } } },
      Template: { type: "object", properties: { id: { type: "string" }, name: { type: "string" }, version: { type: "integer" }, updatedAt: { type: "string" }, updatedBy: { type: "string" },
        lock: { type: ["object", "null"], properties: { holder: { type: "string" }, since: { type: "string" } } }, files: { type: "array", items: { type: "object", properties: { path: { type: "string" }, size: { type: "integer" } } } } } },
    },
  },
};
