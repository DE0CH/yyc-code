//! `plan`: render, compare with the live state through server-side dry runs, and gate deletions on commit trailers
//! (deploy-workflows 6.2, 6.3). Writes a markdown summary and, beside it, the machine plan `apply` executes.

use crate::git::{self, Trailers, replace_refused};
use crate::inventory::{self, InvRef};
use crate::kube::{Kube, kustomize_build};
use crate::obj::{Id, Phase, diff_paths, generator_base, normalized};
use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Action {
    Create,
    Update,
    Unchanged,
    /// delete, then create (`Deploy-Replace`, for an immutable-field change)
    Replace,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlanObj {
    pub id: String,
    pub v: String,
    pub phase: Phase,
    pub action: Action,
    pub paths: Vec<String>,
    pub manifest: Value,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Prune {
    pub id: String,
    pub why: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Plan {
    pub version: u32,
    pub head: String,
    /// set when a newer commit is already applied (or being applied): `apply` exits green, doing nothing
    pub stale: Option<String>,
    pub inventory: String,
    pub inventory_rv: String,
    pub inventory_commit: Option<String>,
    pub inventory_phase: Option<String>,
    pub inventory_entries: BTreeMap<String, String>,
    pub home: Vec<String>,
    pub sealed_as: String,
    pub objects: Vec<PlanObj>,
    pub prune: Vec<Prune>,
    pub release: Vec<String>,
    /// prune-set members no trailer names: the run stops before any write
    pub refused: Vec<String>,
    /// dry-run failures (an immutable field without `Deploy-Replace`, a forbidden kind, …)
    pub errors: Vec<String>,
    /// `Deploy-Replace:` lines that would let an immutable-field change through
    pub replace_needed: Vec<String>,
    pub warnings: Vec<String>,
}

impl Plan {
    pub fn blocked(&self) -> bool {
        self.stale.is_none() && (!self.refused.is_empty() || !self.errors.is_empty())
    }
    pub fn count(&self, a: &Action) -> usize {
        self.objects.iter().filter(|o| o.action == *a).count()
    }
    /// The trailer lines that would let the prune set and the immutable-field changes through.
    pub fn trailer_lines(&self) -> Vec<String> {
        self.refused
            .iter()
            .map(|id| format!("Deploy-Prune: {id}"))
            .chain(self.replace_needed.iter().map(|id| format!("Deploy-Replace: {id}")))
            .collect()
    }
}

#[derive(Clone, Debug)]
pub struct Opts {
    pub repo: PathBuf,
    pub folder: PathBuf,
    pub sealed: Option<PathBuf>,
    pub inventory: InvRef,
    pub exclude: Vec<Id>,
    pub home: Vec<String>,
    pub sealed_as: String,
    pub kustomize: String,
    /// this run's id: a live lease of another run refuses the plan
    pub run: String,
    /// plan although another run's lease is live (its holder is known to be dead)
    pub force: bool,
}

/// The render: (manifest, from the sealed folder), excluded ids dropped, duplicates refused.
pub fn render(kustomize: &str, folder: &Path, sealed: Option<&Path>, exclude: &[Id]) -> Result<Vec<(Value, bool)>> {
    let mut out = Vec::new();
    let mut seen = BTreeSet::new();
    let mut parts = vec![(folder, false)];
    if let Some(s) = sealed {
        parts.push((s, true));
    }
    for (dir, is_sealed) in parts {
        for o in kustomize_build(kustomize, dir)? {
            let (id, _) = Id::of(&o)?;
            if exclude.contains(&id) {
                continue;
            }
            if !seen.insert(id.clone()) {
                bail!("{id} is rendered twice ({})", dir.display());
            }
            out.push((o, is_sealed));
        }
    }
    Ok(out)
}

fn stale_reason(repo: &Path, head: &str, inv: &inventory::Inventory, target: Option<&str>) -> Result<Option<String>> {
    for (what, c) in [("applied", inv.commit.as_deref()), ("being applied", target)] {
        let Some(c) = c else { continue };
        if c == head {
            continue;
        }
        if !git::has_commit(repo, c)? {
            bail!("the inventory's commit {c} ({what}) is not in this checkout (fetch-depth 0?)");
        }
        if git::is_ancestor(repo, head, c)? {
            return Ok(Some(format!("main already at {c} ({what}): nothing to do for {head}")));
        }
        if what == "applied" && !git::is_ancestor(repo, c, head)? {
            bail!("the inventory's commit {c} and {head} have unrelated histories");
        }
    }
    Ok(None)
}

fn min_ref(o: &Value) -> Value {
    let mut m = json!({"apiVersion": o["apiVersion"], "kind": o["kind"], "metadata": {"name": o["metadata"]["name"]}});
    if let Some(ns) = o["metadata"]["namespace"].as_str() {
        m["metadata"]["namespace"] = json!(ns);
    }
    m
}

fn is_immutable(msg: &str) -> bool {
    let m = msg.to_lowercase();
    m.contains("immutable") || m.contains("updates to") && m.contains("forbidden")
}

/// A RoleBinding to a Role of its own namespace that does not exist yet (it is in this render).
fn binds_new_role(m: &Value, all: &BTreeSet<Id>, live: &BTreeMap<Id, Value>) -> bool {
    if m["kind"] != "RoleBinding" || m["roleRef"]["kind"] != "Role" {
        return false;
    }
    let role = Id {
        ns: m["metadata"]["namespace"].as_str().unwrap_or("").into(),
        name: m["roleRef"]["name"].as_str().unwrap_or("").into(),
        group: "rbac.authorization.k8s.io".into(),
        kind: "Role".into(),
    };
    all.contains(&role) && !live.contains_key(&role)
}

pub fn plan(kube: &Kube, o: &Opts) -> Result<Plan> {
    let head = git::head(&o.repo)?;
    let cm = kube.get(&o.inventory.id(), None)?;
    let target = cm
        .as_ref()
        .and_then(|c| {
            c["metadata"]["annotations"]["yyc-deploy/target"]
                .as_str()
                .map(str::to_string)
        })
        .filter(|s| !s.is_empty());
    let inv = inventory::read(kube, &o.inventory)?;
    if let Some((run, until)) = inv.held_by_other(&o.run) {
        let left = until.saturating_sub(inventory::now_secs());
        if o.force {
            eprintln!("yyc-apply: --force: ignoring the live lease of run {run} ({left} s left)");
        } else {
            bail!(
                "the inventory is held by run {run} (phase {}, target {}) for {left} s more: another applier is at work. \
                 Plan again after it ends; pass --force only if that run is known to be dead",
                inv.phase.as_deref().unwrap_or("none"),
                inv.target.as_deref().unwrap_or("none")
            );
        }
    }
    let mut plan = Plan {
        version: 1,
        head: head.clone(),
        stale: stale_reason(&o.repo, &head, &inv, target.as_deref())?,
        inventory: format!("{}/{}", o.inventory.ns, o.inventory.name),
        inventory_rv: inv.resource_version.clone(),
        inventory_commit: inv.commit.clone(),
        inventory_phase: inv.phase.clone(),
        inventory_entries: inv.entries.iter().map(|(k, v)| (k.to_string(), v.clone())).collect(),
        home: o.home.clone(),
        sealed_as: o.sealed_as.clone(),
        objects: vec![],
        prune: vec![],
        release: vec![],
        refused: vec![],
        errors: vec![],
        replace_needed: vec![],
        warnings: vec![],
    };
    if plan.stale.is_some() {
        return Ok(plan);
    }

    let mut objs = render(&o.kustomize, &o.folder, o.sealed.as_deref(), &o.exclude)?;
    // the fence may hold rights elsewhere (db, yyc…); this folder writes SealedSecrets only in its own namespaces.
    // Refused before any read or dry run (the deployer may not even read them there).
    objs.retain(|(m, sealed)| {
        let ns = m["metadata"]["namespace"].as_str().unwrap_or("");
        if *sealed && !o.home.iter().any(|h| h == ns) {
            let id = Id::of(m).map_or_else(|_| format!("{m}"), |x| x.0.to_string());
            plan.errors.push(format!(
                "{id}: a sealed object outside {} is refused",
                o.home.join(", ")
            ));
            false
        } else {
            true
        }
    });
    // the commits since the last one applied in full; with none (a fresh inventory) HEAD's own trailers
    // only, never the whole history's
    let raw = match &inv.commit {
        Some(c) => git::trailers(&o.repo, &format!("{c}..HEAD"))?,
        None => git::trailers_of(&o.repo, &["-1", "HEAD"])?,
    };
    let serving_ns = o.home.first().cloned().unwrap_or_default();
    let tr = Trailers::parse(&raw, &serving_ns);
    // a bad trailer is already on main for good (no force-push): check item 11 turned its push red; here it is
    // ignored, so it cannot block every later run
    plan.warnings.extend(
        tr.errors
            .iter()
            .map(|e| format!("trailer ignored: {e} (check item 11 failed that push)")),
    );
    // live state (one `get -f`), then server-side dry runs, the sealed folder as the fenced identity
    let refs: Vec<Value> = objs.iter().map(|(m, _)| min_ref(m)).collect();
    let mut args: Vec<String> = ["get", "-f", "-", "-o", "json", "--ignore-not-found"]
        .iter()
        .map(|s| (*s).to_string())
        .collect();
    if refs.is_empty() {
        args.clear();
    }
    let mut live: BTreeMap<Id, Value> = BTreeMap::new();
    if !args.is_empty() {
        for l in crate::kube::json_stream(&kube.ok(&args, Some(&crate::kube::list(&refs)))?)? {
            live.insert(Id::of(&l)?.0, l);
        }
    }
    let mut dry: BTreeMap<Id, Result<Value, String>> = BTreeMap::new();
    for sealed in [false, true] {
        let group: Vec<&Value> = objs.iter().filter(|(_, s)| *s == sealed).map(|(m, _)| m).collect();
        if group.is_empty() {
            continue;
        }
        let as_ = sealed.then_some(o.sealed_as.as_str());
        let batch: Vec<Value> = group.iter().map(|v| (*v).clone()).collect();
        match kube.apply(&batch, true, as_) {
            Ok(res) => {
                for r in res {
                    dry.insert(Id::of(&r)?.0, Ok(r));
                }
            }
            Err(_) => {
                for m in group {
                    let id = Id::of(m)?.0;
                    let r = kube
                        .apply(std::slice::from_ref(m), true, as_)
                        .map_err(|e| format!("{e:#}"));
                    dry.insert(
                        id,
                        r.and_then(|mut v| v.pop().ok_or_else(|| "no object returned".to_string())),
                    );
                }
            }
        }
    }

    let all_ids: BTreeSet<Id> = objs.iter().filter_map(|(m, _)| Id::of(m).ok().map(|x| x.0)).collect();
    let mut rendered: BTreeSet<Id> = BTreeSet::new();
    for (m, sealed) in &objs {
        let (id, v) = Id::of(m)?;
        rendered.insert(id.clone());
        let phase = Phase::of(&id, *sealed, &o.home);
        let (action, paths) = match (dry.get(&id), live.get(&id)) {
            (Some(Ok(_)), None) => (Action::Create, vec![]),
            (Some(Ok(d)), Some(l)) => {
                let p = diff_paths(&normalized(l), &normalized(d), 20);
                if p.is_empty() {
                    (Action::Unchanged, p)
                } else {
                    (Action::Update, p)
                }
            }
            // the API server's escalation check looks the Role up, which a dry run has not created: the binding is
            // checked for real when the apply creates it, right after its Role (same phase, render order)
            (Some(Err(e)), None) if e.contains("NotFound") && binds_new_role(m, &all_ids, &live) => {
                plan.warnings.push(format!(
                    "{id}: binds a Role this plan creates; the dry run cannot check it (the apply does)"
                ));
                (Action::Create, vec![])
            }
            (Some(Err(e)), _) if tr.replace.contains(&id) && is_immutable(e) => (
                Action::Replace,
                vec![format!("immutable: {}", e.lines().last().unwrap_or(""))],
            ),
            (Some(Err(e)), _) => {
                if is_immutable(e) {
                    match replace_refused(&id, &serving_ns) {
                        Some(why) => plan.errors.push(format!(
                            "{id}: an immutable field changed and Deploy-Replace is refused for it ({why}): {e}"
                        )),
                        None => {
                            plan.replace_needed.push(id.to_string());
                            plan.errors.push(format!(
                                "{id}: an immutable field changed (add `Deploy-Replace: {id}` to delete and recreate it): {e}"
                            ));
                        }
                    }
                } else {
                    plan.errors.push(format!("{id}: {e}"));
                }
                continue;
            }
            (None, _) => {
                plan.errors.push(format!("{id}: the dry run returned no object"));
                continue;
            }
        };
        if tr.replace.contains(&id) && action != Action::Replace {
            plan.warnings.push(format!(
                "Deploy-Replace: {id}: no immutable change, applied as an update"
            ));
        }
        plan.objects.push(PlanObj {
            id: id.to_string(),
            v,
            phase,
            action,
            paths,
            manifest: m.clone(),
        });
    }

    // the prune set = inventory − render; each member named by a trailer (or a superseded generator name)
    let gen_bases: BTreeSet<(String, String, String, String)> = rendered
        .iter()
        .filter_map(|i| generator_base(i).map(|b| (i.ns.clone(), i.group.clone(), i.kind.clone(), b.to_string())))
        .collect();
    for id in inv.entries.keys() {
        if rendered.contains(id) || o.exclude.contains(id) {
            continue;
        }
        if id.is_sealed_secret() && !o.home.contains(&id.ns) {
            plan.errors.push(format!(
                "{id}: pruning a SealedSecret outside {} is refused (remove it from the inventory by hand)",
                o.home.join(", ")
            ));
        } else if tr.release.contains(id) {
            plan.release.push(id.to_string());
        } else if generator_base(id)
            .is_some_and(|b| gen_bases.contains(&(id.ns.clone(), id.group.clone(), id.kind.clone(), b.to_string())))
        {
            plan.prune.push(Prune {
                id: id.to_string(),
                why: "superseded generator hash".into(),
            });
        } else if tr.prune.contains(id) {
            plan.prune.push(Prune {
                id: id.to_string(),
                why: "Deploy-Prune".into(),
            });
        } else if tr.prune_ns.contains(&id.ns) {
            plan.prune.push(Prune {
                id: id.to_string(),
                why: format!("Deploy-Prune-Namespace: {}", id.ns),
            });
        } else {
            plan.refused.push(id.to_string());
        }
    }
    for id in tr.prune.iter().chain(&tr.release) {
        if !inv.entries.contains_key(id) || rendered.contains(id) {
            plan.warnings
                .push(format!("a trailer names {id}, which is not in the prune set"));
        }
    }
    Ok(plan)
}

pub fn markdown(p: &Plan, folder: &str) -> String {
    let mut s = String::new();
    let h = &p.head[..p.head.len().min(12)];
    let _ = writeln!(s, "## yyc-apply plan: `{folder}` at `{h}`\n");
    if let Some(r) = &p.stale {
        let _ = writeln!(s, "{r}");
        return s;
    }
    let _ = writeln!(
        s,
        "Inventory `{}`: {} objects, last applied commit `{}`, phase `{}`.\n",
        p.inventory,
        p.inventory_entries.len(),
        p.inventory_commit.as_deref().unwrap_or("none"),
        p.inventory_phase.as_deref().unwrap_or("none")
    );
    let _ = writeln!(
        s,
        "| creates | updates | replaces | unchanged | prunes | releases | refused | errors |\n|---|---|---|---|---|---|---|---|\n| {} | {} | {} | {} | {} | {} | {} | {} |\n",
        p.count(&Action::Create),
        p.count(&Action::Update),
        p.count(&Action::Replace),
        p.count(&Action::Unchanged),
        p.prune.len(),
        p.release.len(),
        p.refused.len(),
        p.errors.len()
    );
    if !p.refused.is_empty() {
        let _ = writeln!(
            s,
            "### Refused: removed from the render, named by no commit since `{}`\n\nNothing is applied. If the removal is meant, add the `Deploy-Prune` trailers below to a commit (or restore the objects).\n",
            p.inventory_commit.as_deref().unwrap_or("the start"),
        );
    }
    if !p.trailer_lines().is_empty() {
        let _ = writeln!(
            s,
            "### Trailers this plan needs\n\n```\n{}\n```\n",
            p.trailer_lines().join("\n")
        );
    }
    for (title, list) in [("Errors", &p.errors), ("Warnings", &p.warnings)] {
        if !list.is_empty() {
            let _ = writeln!(s, "### {title}\n");
            for e in list {
                let _ = writeln!(s, "- {e}");
            }
            s.push('\n');
        }
    }
    for (title, a) in [
        ("Creates", Action::Create),
        ("Updates", Action::Update),
        ("Replaces", Action::Replace),
    ] {
        let items: Vec<&PlanObj> = p.objects.iter().filter(|o| o.action == a).collect();
        if items.is_empty() {
            continue;
        }
        let _ = writeln!(s, "### {title}\n");
        for o in items {
            if o.paths.is_empty() {
                let _ = writeln!(s, "- `{}`", o.id);
            } else {
                let _ = writeln!(s, "- `{}`: {}", o.id, o.paths.join(", "));
            }
        }
        s.push('\n');
    }
    if !p.prune.is_empty() {
        let _ = writeln!(s, "### Prunes (after every rollout is ready)\n");
        for x in &p.prune {
            let _ = writeln!(s, "- `{}` ({})", x.id, x.why);
        }
        s.push('\n');
    }
    if !p.release.is_empty() {
        let _ = writeln!(s, "### Released (left in place, dropped from the inventory)\n");
        for x in &p.release {
            let _ = writeln!(s, "- `{x}`");
        }
    }
    s
}

pub fn write(p: &Plan, out: &Path, folder: &str) -> Result<()> {
    std::fs::write(out, markdown(p, folder)).with_context(|| format!("writing {}", out.display()))?;
    std::fs::write(json_path(out), serde_json::to_vec_pretty(p)?)?;
    Ok(())
}

pub fn json_path(out: &Path) -> PathBuf {
    let mut s = out.as_os_str().to_owned();
    s.push(".json");
    PathBuf::from(s)
}

pub fn load(out: &Path) -> Result<Plan> {
    let p = json_path(out);
    serde_json::from_slice(&std::fs::read(&p).with_context(|| format!("reading {}", p.display()))?)
        .with_context(|| format!("parsing {}", p.display()))
}
