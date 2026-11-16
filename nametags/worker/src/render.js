// Server-side rendering of the public pages (src/pages/*.html, imported as text) and the QR codes as inline SVG.
// A page is a template: {{name}} is replaced by the HTML-escaped value, {{{name}}} by raw HTML made here, in one pass
// (so a value that contains braces is never expanded again); a placeholder with no value becomes empty.
//
// Placeholders:
//   form.html    {{event_name}} {{event_name_en}} {{event_date}} {{event_venue}} {{event_id}} {{turnstile_sitekey}}
//                {{v_name}} {{v_institution}} {{v_email}} {{v_phone}} (the values sent, re-filled after an error)
//                {{{errors}}} (a <ul class="errors"> of zh-HK / English lines, or empty)
//   status.html  {{event_name}} {{event_name_en}} {{guest_name}} {{state}} (pending | accepted | declined)
//                {{{qr_svg}}} (the guest's QR code; empty unless accepted)
//   closed.html  {{event_name}} {{event_name_en}} (both empty for an unknown event)
import qrcodegen from "./qrcodegen.js";

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

export function fill(template, values = {}, raw = {}) {
  return template.replace(/\{\{\{(\w+)\}\}\}|\{\{(\w+)\}\}/g, (_, r, e) => (r ? String(raw[r] ?? "") : esc(values[e])));
}

export const errorList = (errors) => (errors.length ? `<ul class="errors">${errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : "");

// QR code (ECC M, 4-module quiet zone) as one <svg> path; sized by its container
export function qrSvg(text, { label = "QR code" } = {}) {
  const qr = qrcodegen.QrCode.encodeText(text, qrcodegen.QrCode.Ecc.MEDIUM);
  const n = qr.size + 8;
  let d = "";
  for (let y = 0; y < qr.size; y++)
    for (let x = 0; x < qr.size; x++) if (qr.getModule(x, y)) d += `M${x + 4} ${y + 4}h1v1h-1z`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n} ${n}" shape-rendering="crispEdges" role="img" aria-label="${esc(label)}">`
    + `<rect width="${n}" height="${n}" fill="#fff"/><path d="${d}" fill="#000"/></svg>`;
}
