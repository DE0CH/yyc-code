// The server's settings (process.env, read once by node/server.ts: deploy/apps/yyc-mcp.yaml and Secret mcp-env) and
// what it builds from them: the two Postgres pools, the OAuth store and the registry.
import type { Sql } from "../../web-app/src/server/db";
import type { Registry } from "./registry";

export interface Env {
  DB: Sql;                       // database app as yyc_readonly (DATABASE_URL): the tools' data
  REGISTRY: Registry;            // tokens, rate, calls in database mcp
  BASE_URL: string;              // the public origin, https://mcp.yyc-internal.com
  CATALOGUE: string;             // path of YYC's access catalogue (the store's YYC_CATALOGUE_JSON, Secret yyc-catalogue):
                                 // its `users` may sign in and call, its `admins` read the call log (catalogue.ts)
  RATE_PER_MIN: string;          // tool calls per account per minute
  OIDC_ISSUER: string;           // the sign-in step: Cloudflare Access app "YYC MCP sign-in" (OIDC, tofu access.tf),
                                 // https://yyc-hk.cloudflareaccess.com/cdn-cgi/access/sso/oidc/<client id>
  OIDC_CLIENT_ID: string;
  OIDC_CLIENT_SECRET: string;
  CONSENT_SECRET: string;        // signs remembered consent and the web session cookie (32+ chars)
  SERVICE_TOKENS?: string;       // {"<sha256>": "<email>"}: YYC AI's YYC_JARVIS_MCP_TOKEN, Deyao's sessions' YYC_MCP_TOKEN
  WISERS_API?: string;           // the WiseSearch relay's API in the cluster, http://wisers.yyc.svc.cluster.local:8899/app/wisers/api/
  WISERS_INTERNAL_TOKEN?: string; // the relay's caller token for yyc-mcp (relay.py WISERS_INTERNAL_TOKENS)
}

/** what an access token or a personal token carries: the account it was issued to (`service`: a SERVICE_TOKENS
 *  token, a machine identity outside the catalogue) */
export interface Props { email: string; name?: string; service?: boolean }
