#!/bin/bash
# gha-token.sh: kubectl's exec credential plugin in yyc-ai.yml's deploy job. Asks GitHub for a fresh OIDC token of
# this run (audience yyc-cluster; the job has `id-token: write`) and prints it as an ExecCredential with the token's own
# expiry, so kubectl asks again when it runs out and a long rollout wait never uses a stale token. The token goes only
# to kubectl's pipe (GitHub masks it in logs too); the request token is passed on stdin, never in argv.
set -euo pipefail
: "${ACTIONS_ID_TOKEN_REQUEST_URL:?no OIDC request URL: the job needs permissions id-token: write}"
: "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:?}"
printf 'Authorization: bearer %s\n' "$ACTIONS_ID_TOKEN_REQUEST_TOKEN" |
  curl -fsS --retry 3 -H @- "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=yyc-cluster" |
  python3 -c '
import base64, datetime, json, sys
tok = json.load(sys.stdin)["value"]
p = tok.split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
exp = datetime.datetime.fromtimestamp(claims["exp"] - 30, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print(json.dumps({"apiVersion": "client.authentication.k8s.io/v1", "kind": "ExecCredential",
                  "status": {"token": tok, "expirationTimestamp": exp}}))'
