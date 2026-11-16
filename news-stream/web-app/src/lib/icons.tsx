// Line icons, one set (house style, claude-env deyao-web-design): viewBox 24, stroke currentColor 2, square caps, no
// fill; squares rather than circles. Sentiment marks are squares, coloured by styles/house.css (svg.i[data-i=<sentiment>]).
const P: Record<string, string> = {
  // sections
  stream: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  search: '<path d="M4 4h11v11H4z"/><path d="m15 15 5 5"/>',
  chart: '<path d="M5 20V10M11 20V4M17 20v-7M21 20H3"/>',
  chat: '<path d="M3 4h18v13H9l-6 4z"/><path d="M8 10.5h.01M12 10.5h.01M16 10.5h.01"/>',
  report: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/>',
  // page tools
  find: '<path d="M4 4h11v11H4z"/><path d="m15 15 5 5"/>',
  prev: '<path d="m15 5-7 7 7 7"/>',
  next: '<path d="m9 5 7 7-7 7"/>',
  calendar: '<path d="M3 5h18v16H3z"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  out: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
  link: '<path d="m10 14 4-4"/><path d="m8 11-3 3 5 5 3-3"/><path d="m16 13 3-3-5-5-3 3"/>',
  // sentiments: a square mark each
  positive: '<path d="M4 4h16v16H4z"/><path d="M12 8v8M8 12h8"/>',
  neutral: '<path d="M4 4h16v16H4z"/>',
  negative: '<path d="M4 4h16v16H4z"/><path d="M8 12h8"/>',
  mixed: '<path d="M4 4h16v16H4z"/><path d="M4 20 20 4"/>',
  irony: '<path d="M4 4h16v16H4z"/><path d="M7 13c1.7-2.4 3.3-2.4 5 0s3.3 2.4 5 0"/>',
  // labels
  crisis: '<path d="M6 18v-7h12v7M4 18h16v3H4zM12 3v3M4 6l2 2M20 6l-2 2"/>',
  rival: '<path d="M5 5l11 11M19 5 8 16M3 17l4 4M21 17l-4 4M14 18l4-4M10 18l-4-4"/>',
  review: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/>',
  praise: '<path d="M7 11v9H4v-9z"/><path d="m7 11 4-8h2v6h7v4l-2 7H7"/>',
  urgent: '<path d="M4 5h16v16H4z"/><path d="M12 9v4l3 2M4 2 2 4M20 2l2 2"/>',
  monitor: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><path d="M10 10h4v4h-4z"/>',
  issue: '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/>',
  trend: '<path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
  service: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-.5-.5-2.5 2.5-2.5z"/>',
  chance: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
  repute: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
  engaging: '<path d="M3 4h18v13H9l-6 4z"/><path d="M8 9h8M8 12.5h5"/>',
  kol: '<path d="M8 4h8v8H8z"/><path d="M4 21v-5h16v5"/>',
  ask: '<path d="M3 3h18v18H3z"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17h.01"/>',
  rumor: '<path d="M4 5h16v11H8l-4 4z"/><path d="M9 10.5h.01M12 10.5h.01M15 10.5h.01"/>',
  goods: '<path d="M5 8h14l-1 12H6z"/><path d="M9 8V4h6v4"/>',
  accuse: '<path d="M3 10v4h3l5 4V6l-5 4z"/><path d="M15 9a4 4 0 0 1 0 6M18 6a8 8 0 0 1 0 12"/>',
};

export function Icon({ name, className }: { name: string; className?: string }) {
  const d = P[name];
  if (!d) return null;
  return <svg className={"i" + (className ? " " + className : "")} data-i={name} viewBox="0 0 24 24" aria-hidden="true" dangerouslySetInnerHTML={{ __html: d }} />;
}
