# yyc-code

The code, deploy configuration and CI of YYC's internal tools. YYC is a Hong Kong company; the tools are for its
staff. The repository is public so that its CI runs on GitHub's free runners; the rest of YYC's work (research,
reports, decks, expenses) is in the private DE0CH/YYC.

| Folder | What it is |
|---|---|
| `yyc-ai/` | YYC AI, the staff AI chat (`ai.yyc-internal.com`): deploy configuration, managed skills, tests, design (`refactor/`) |
| `news-stream/` | The news stream (`newswatch.yyc-internal.com`): code, tests, and `deploy/` with the cluster `yyc-talos` and the apps on it |
| `ai-watch/` | AI watch (`aiwatch.yyc-internal.com`): what consumer AI apps say about a subject |
| `labels/`, `nametags/` | Labels and name tags for the NIIMBOT M3: Workers, templates, the iOS app |
| `feishu-index/` | A searchable index of a Feishu folder, with an HTTP API |
| `socks/` | `yyc-socks`, the cluster's way out to the internet |
| `tools/yyc-apply/` | The applier behind the deploy workflows |
| `infra/` | Sealing secrets (`seal.py`), the access catalogue (`catalogue.sh`), cluster certificates |

## Everything here is public

- **No secret in git.** A secret a workload needs is a SealedSecret (`infra/seal.py`: only the cluster's controller
  can open it); a secret a workflow needs is an Actions secret; the values live in the store `yyc`.
- **No staff address in git.** Who may use what is the access catalogue, the store key `YYC_CATALOGUE_JSON`
  (`infra/catalogue.sh` changes it); tests use sample addresses.
- **Workflow logs and artifacts are public too:** a workflow never prints the catalogue, a secret or a staff
  member's data.
- A pull request from a fork runs the tests only: the deploy jobs run on `main`, in their environments, and the
  cluster accepts a workflow's token only from this repository's `main`
  (`news-stream/deploy/clusters/talos/patches/controlplane.yaml`).

## History

The code moved here from DE0CH/YYC on 2026-11-16 with a fresh history. The commits before that date named
`pin history: …` replay the image pins of `DE0CH/aviary yyc/deploy` with their original dates (DE0CH/claudecodeui reads them to
know which app builds were served); every other earlier change is in DE0CH/YYC's history.
