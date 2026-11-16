// The build this code belongs to (vite.config.ts: the commit, else the build time). The same id is in the browser's
// bundle, in the Worker (/version, the LiveHub's hello) and on the page (<html data-build>), so a page can tell that the
// site has moved on (lib/update.ts).
declare const __BUILD_ID__: string;
export const BUILD_ID: string = typeof __BUILD_ID__ === "string" ? __BUILD_ID__ : "dev";
