#!/usr/bin/env bash
# catalogue.sh: YYC's access catalogue, {"users": [...], "admins": [...]} ("@domain" = everyone there; an admin is also
# a user). It is people's mail addresses, so it lives in the store `yyc` as YYC_CATALOGUE_JSON and never as a file in
# git. Its readers: hermit and tools (Secret yyc-ai/catalogue), yyc-mcp (Secret yyc/yyc-catalogue), both sealed from
# the store (secrets.txt), and tofu's Access policy "YYC users" and yyc-ai.yml's catalogue check (Actions secret
# YYC_CATALOGUE_JSON). Prints no address.
#
#   infra/catalogue.sh get FILE   write the store's catalogue to FILE (mode 600) to edit it
#   infra/catalogue.sh put FILE   FILE becomes the catalogue: the store key, both sealed files and the Actions secret.
#                                 Then commit and push the two sealed files (yyc-ai.yml and yyc-apps.yml apply them;
#                                 the pods re-read the file within about 2 min, no restart) and run
#                                 `gh workflow run cloudflare.yml` (or wait for its next half hour)
# Needs a session holding the store `yyc` (YYC_CATALOGUE_JSON, YYC_CI_SECRETS_TOKEN), claude-env's scripts/store-put.py
# (CLAUDE_ENV, default /workspace), kubeseal, jq and gh. Delete FILE afterwards.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=${YYC_REPO:-$(git -C "$here" remote get-url origin | sed -E 's#^.*github\.com[^:/]*[:/]##; s#\.git$##')}
file=${2:?FILE}
case "${1:-}" in
  get)
    (umask 077; session-secret --keys YYC_CATALOGUE_JSON --exec bash -c 'printf "%s" "$YYC_CATALOGUE_JSON" | jq .' > "$file")
    jq -r '"\(.users | length) users, \(.admins | length) admins"' "$file"
    ;;
  put)
    # the shape hermit's catalogue-check asks for (CI runs the real one on the Actions secret)
    jq -e '(.users | type == "array") and (.admins | type == "array") and (keys - ["admins", "users"] == [])
      and ([.users[], .admins[]] | all(type == "string" and test("^[^@\\s]*@[^@\\s]+$")))
      and (.users as $u | .admins | all(. as $a | ($u | index($a)) or ($u | index("@" + ($a | split("@")[1])))))' "$file" >/dev/null \
      || { echo "$file is not a catalogue: {\"users\": [...], \"admins\": [...]}, every entry an address or @domain, every admin a user" >&2; exit 1; }
    # one form everywhere (jq's, one entry a line), whatever the editor left
    norm=$(umask 077; mktemp); trap 'rm -f "$norm"' EXIT
    jq . "$file" > "$norm"; file=$norm
    "${CLAUDE_ENV:-/workspace}/scripts/store-put.py" yyc "YYC_CATALOGUE_JSON=file:$file" --explain \
      "YYC_CATALOGUE_JSON=YYC's access catalogue (who may use every YYC app, and the admins): JSON users/admins. Sealed into yyc-ai/catalogue and yyc/yyc-catalogue, and the Actions secret of the same name (DE0CH/YYC infra/catalogue.sh)"
    python3 "$here/seal.py" yyc-ai/catalogue --stdin catalogue.json < "$file"
    python3 "$here/seal.py" yyc/yyc-catalogue@talos --stdin config.json < "$file"
    # on ONE line: the runner masks every line of a secret by itself, and a line "{" or "]," would turn those into ***
    # in every log of the repo
    session-secret --keys YYC_CI_SECRETS_TOKEN --exec bash -c 'jq -c . "$1" | GH_TOKEN=$YYC_CI_SECRETS_TOKEN gh secret set YYC_CATALOGUE_JSON -R "$0"' "$repo" "$file"
    jq -r '"\(.users | length) users, \(.admins | length) admins: stored, sealed, Actions secret set on '"$repo"'. Now commit and push the sealed files"' "$file"
    ;;
  *) sed -n '2,15p' "$0" >&2; exit 2 ;;
esac
