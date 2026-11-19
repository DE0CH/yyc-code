Old-cluster pieces of the move to yyc-talos (plan: DE0CH/aviary yyc/refactor/talos-apps-move.md). Nothing here is applied:
Flux builds only the files a kustomization.yaml lists, and none lists these. Each step of the plan that needs one adds
it to `../kustomization.yaml` (Flux `yyc-apps`) or merges it into `../../../db/postgres.yaml` (Flux `yyc-db`) in its
own commit.

| File | Step | What |
|---|---|---|
| `wisers-nodeport.yaml` | T4b-prep | NodePorts 30899/30898 for the WiseSearch relay, so yyc-talos's Service `wisers` reaches it until the wisers step |
| `pg-shared-alias.yaml` | T4b-prep | `db/pg-shared` on the old cluster too (selector: the primary), so tunnel yyc-stream's `db.yyc-internal.com` route names one Service that exists on both clusters |
| `pg-topology.yaml` | T5-prep, T5 | the old Cluster `pg` in CloudNativePG's distributed topology (`pg-k3s`), then its demotion (`primary: pg-talos`) |
| `copy-volume.sh` | T4b-precopy, T4b-jobs | copies an old PVC's files into a yyc-talos PVC over network `yyc` |
