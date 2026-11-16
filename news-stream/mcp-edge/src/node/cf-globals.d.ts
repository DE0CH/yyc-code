// The Workers types the shared code names, as far as this Node server provides them.
declare module "cloudflare:workers" {
  export const env: Record<string, unknown>;
  export function waitUntil(p: Promise<unknown>): void;
}
interface ExecutionContext { waitUntil(p: Promise<unknown>): void; passThroughOnException(): void }
interface ExportedHandler<Env = unknown> { fetch?(req: Request, env: Env, ctx: ExecutionContext): Response | Promise<Response> }
declare namespace Cloudflare { interface Env { [k: string]: unknown } }
