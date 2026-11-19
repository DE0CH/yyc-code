# yyc-apply

How a push to DE0CH/yyc-code reaches the cluster: the applier behind `.github/workflows/yyc-ai.yml` (design:
`DE0CH/aviary yyc/refactor/deploy-workflows.md` 5-6 and 12, spec-platform 13, DESIGN 15.3). It targets the new Talos cluster
`yyc-talos`, which started empty: there is no hand-over from Flux, so the inventory starts empty too. Rust, one binary, used by the workflow and from a
session. It drives `kubectl` (server-side apply, server-side dry runs, `rollout status`) and `kustomize build`
(5.8.1), and owns what kubectl lacks: the inventory, the deletion gate, the apply order and the waits. It knows
nothing about YYC AI beyond its flags, so other folders can move to it later. The old cluster keeps its own Flux;
nothing here reaches it.

## Commands

| Command | What it does |
|---|---|
| `check --folder F --sealed S --access FILE… [--secrets infra/secrets.txt] [--commits A..B] [--exclude ID…] [--existing ID…]` | job `check`'s items 9-12 (spec-platform 13.3), from the repo only: every rendered object inside the deployer's rights as the RBAC files grant them (get, patch and delete by name; create, which RBAC cannot limit by name, unless `--existing` names it as an object the cluster already holds; the fence likewise for SealedSecrets), and no rendered Role or binding granting more than the deployer holds); `sealed/` only SealedSecrets, each in `infra/secrets.txt`, each in `--namespaces` and where the fence may write ; a map name may carry `@talos` (`NS/NAME@talos`, the same name on both clusters) and the line must write into the sealed folder, so a name only the old cluster's line carries does not pass; `Deploy-*` trailers well-formed and `Deploy-Replace` not on serving kinds; no Namespace, no RBAC outside `--namespaces` |
| `plan --folder F --sealed S --inventory NS/NAME [--run ID] [--force] [--out plan.md] [--trailers]` | refused while another run holds the inventory's lease (below; `--force` only for a holder known to be dead); render; live objects; a server-side dry run of each (the sealed folder as `--sealed-as`); creates, updates (changed fields), replaces, unchanged; the prune set = inventory − render, each member named by a trailer in `<inventory commit>..HEAD` (HEAD's own trailers only while the inventory has no commit) or refused; sealed objects (and SealedSecrets to prune) outside `--namespaces` refused. A malformed or refused trailer in range is a warning (check item 11 failed its push; it is on main for good and must not block every later run); a refused `Deploy-Replace` never replaces. Writes `plan.md` (summary) and `plan.md.json` (what `apply` runs); exits 1 when refused or erroring. `--trailers` prints the `Deploy-Prune:` and `Deploy-Replace:` lines the plan needs, the errors on stderr, and exits 1 while the plan is blocked |
| `apply --plan plan.md --run ID [--lease-secs 120]` | the plan, in order: inventory old ∪ new (phase `applying`, the lease taken); cluster-scoped and cross-namespace objects; SealedSecrets as the fence, then a wait (≤ 2 min) until each changed one is `Synced` at its generation; everything else; workloads; `rollout status` of every Deployment, DaemonSet and StatefulSet (15 min); prune: workloads, then Services, PVCs, PDBs and the like, then (once the Pods the pruned workloads owned and the older-revision Pods of the render's updated workloads are gone, up to their longest grace period + `--drain-margin-secs`) NetworkPolicies, ConfigMaps, SealedSecrets, ServiceAccounts, Roles and RoleBindings, then cross-namespace and cluster-scoped; inventory = render (phase `done`, lease cleared). A failing step stops red; nothing is rolled back or pruned. |

Flags shared by `check` and `plan`: `--namespaces yyc-ai,yyc-ai-fabric` (the folder's own namespaces; objects
elsewhere, such as the bridge placeholders in `yyc`, apply with the cluster-scoped ones),
`--sealed-as system:serviceaccount:yyc-deploy:sealed-secrets-applier`, `--exclude ID` (left out of render, plan and
apply: an object another owner still applies), `--repo .`. Global:
`--kubectl`, `--kustomize`. kubectl talks to `$KUBECONFIG`.

## Inventory and trailers

ConfigMap `yyc-deploy/inventory-yyc-ai`: `data.entries` = one `<namespace>_<name>_<group>_<Kind> <version>` per
line (Flux's id format), annotations `yyc-deploy/commit` (last commit applied in full), `yyc-deploy/target` (the
commit being applied), `yyc-deploy/run`, `yyc-deploy/applied-at`, `yyc-deploy/phase` (`applying`/`done`), `yyc-deploy/lease-until`.

Two locks. Every inventory write carries the resourceVersion read before it, so two writes made from the same read
cannot both land. And a running `apply` holds a lease: `lease-until` (unix seconds) with
`run` naming the holder, renewed every quarter of `--lease-secs` and cleared when the run ends, on success or failure.
`plan` refuses while another run's lease is live, so a second applier (a session, a re-run with another id) cannot
start applying and pruning beside a run in progress; a renewal that finds the inventory moved stops the run before
its next apply or prune. A run killed outright (SIGKILL, a lost runner) leaves its lease to expire within
`--lease-secs`; a re-run of the same workflow run (same id) is its own holder. A run that dies leaves the superset and the old `commit`, so the next run still sees the trailers of the dead run's commits. A run
whose commit is older than `commit` or `target` is a green no-op ("main already at …"); rolling back is a revert
commit.

| Trailer | Effect |
|---|---|
| `Deploy-Prune: <id>[, <id>…]` | delete these objects after the rollout |
| `Deploy-Prune-Namespace: <ns>` | every prune-set member in `<ns>` |
| `Deploy-Release: <id>` | drop from the inventory, keep the object (it moves to another owner) |
| `Deploy-Replace: <id>` | delete then create for an immutable-field change; refused for Deployments, DaemonSets, StatefulSets, PDBs, Services in `yyc-ai` and for PVs/PVCs |

Superseded kustomize generator names (a ConfigMap or Secret `<name>-<10-char hash>` in kustomize's hash alphabet
`bcdfghkmt2456789`, when the generator renders a newer one) are pruned without a trailer.

A bad trailer that reached main (a typo, an unknown kind, a refused `Deploy-Replace`) turns that push's check red and
is then ignored, with a warning in every plan until the inventory's commit passes it: it cannot block later runs.

## From a session

Through the new cluster's operator path (DESIGN 15.2 N2: `news-stream/deploy/clusters/talos/README.md` "Operator
access", the admin kubeconfig on https://127.0.0.1:6443): `KUBECONFIG=~/talos/kubeconfig yyc-apply plan … --trailers`
before a push that deletes or replaces anything (claude-env CLAUDE.md "GitOps").

## In the workflow

`connect.sh` starts `cloudflared access tcp` to `k8s-deploy.yyc-internal.com` (Access service token from the job's
env) and writes a kubeconfig whose credential is `gha-token.sh` (a fresh GitHub OIDC token, audience
`yyc-cluster`) and whose CA is `infra/yyc-talos-ca.pem`. The control nodes' authenticator
(`news-stream/deploy/clusters/talos/patches/controlplane.yaml`) maps that token to `gha:yyc-ai`, whose rights are
`news-stream/deploy/clusters/talos/yyc-ai-access.yaml` and whose fence is `…/talos/sealed-secrets.yaml`.

The `deploy` job applies on every push to main, every 3 h and on dispatch (`DEPLOY_APPLY: "yes"`; the workflow's
header).

The applier's tests gate on its content: an Actions-cache marker keyed by the hash of `tools/yyc-apply`,
`.github/actions` and the workflow is saved only after the unit and kind tests pass at that content, and job `deploy`
does not start without it (a push touching only `DE0CH/aviary yyc/deploy` after a red applier push re-runs the tests). A run
holds the deploy group for at most the apply's own waits; a plan-only dispatch has its own concurrency group, so it
never cancels a pending apply.

## Tests

`cargo test` (unit tests and `tests/check.rs`: items 9-12 on planted violations; needs `kustomize`), and against a
kind cluster (`kubectl` on PATH pointed at it): `cargo test --test kind -- --ignored`: an unnamed removal fails before
any write; the inventory is a superset after a killed run, which the next run finishes; an old run is a green no-op;
no workload applies before its SealedSecret is `Synced`, and a never-synced one turns the run red; a never-ready Pod
turns the run red with the old Pod serving and nothing pruned; a bad trailer on main does not block the next runs; a pruned workload's draining Pod (15 s grace) outlives its
NetworkPolicy and ConfigMap; an updated workload's old-revision Pod, draining after `rollout status` returned, outlives
its renamed NetworkPolicy and ServiceAccount; a second applier planning while a run holds the inventory (past the first
lease period, so renewed) is refused, and goes ahead once that run ends; a killed run's lease refuses the next plan
until it expires; and `clusters/talos/yyc-ai-access.yaml` with `sealed-secrets.yaml` applied (namespaces renamed
`t-r-*`), plan, apply and prune run as `gha:yyc-ai` (a client certificate from the cluster's signer): the SealedSecret
through the fence's impersonation, a Role and its binding, a bridge placeholder Service and EndpointSlice in `yyc`, a
PV, and the Role the render creates (`hermit-read` with EndpointSlices), which the escalation check allows; the API
server refuses a Role escalating to Secrets, and the plan refuses a SealedSecret for `db`. That test reads the repo's access files, so it runs
from a full checkout.
Job `check` runs both when the applier changes.
