//! `apply`: executes a plan in the order of deploy-workflows 6.4. A failing step stops the run red; nothing is
//! rolled back (git is the source) and nothing is pruned before every rollout is ready.

use crate::inventory::{self, InvRef, Lease, Record};
use crate::kube::Kube;
use crate::obj::{Id, Phase, prune_rank};
use crate::plan::{Action, Plan};
use anyhow::{Result, bail};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::time::{Duration, Instant};

#[derive(Clone, Debug)]
pub struct Opts {
    pub run: String,
    pub rollout_timeout: String,
    pub sealed_timeout: Duration,
    pub prune_timeout: String,
    /// added to the largest terminationGracePeriodSeconds when waiting for draining Pods to be gone
    pub drain_margin: Duration,
    /// the inventory lease (renewed every quarter of it while this run lives)
    pub lease_secs: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Outcome {
    /// a newer commit is applied (or being applied): nothing done
    Stale,
    /// applied in full, inventory `done`
    Done,
}

fn say(msg: &str) {
    eprintln!("yyc-apply: {msg}");
}

fn synced(o: &Value) -> bool {
    let gen_ = o["metadata"]["generation"].as_i64();
    let seen = o["status"]["observedGeneration"].as_i64();
    let ok = o["status"]["conditions"]
        .as_array()
        .is_some_and(|c| c.iter().any(|c| c["type"] == "Synced" && c["status"] == "True"));
    gen_.is_some() && gen_ == seen && ok
}

fn sealed_message(o: &Value) -> String {
    o["status"]["conditions"]
        .as_array()
        .and_then(|c| c.iter().find(|c| c["type"] == "Synced"))
        .map_or_else(
            || "no Synced condition yet".into(),
            |c| format!("Synced={} {}", c["status"], c["message"]),
        )
}

struct Sets {
    new: BTreeMap<Id, String>,
    union: BTreeMap<Id, String>,
    released: Vec<Id>,
}

fn sets(plan: &Plan) -> Result<Sets> {
    let mut old: BTreeMap<Id, String> = BTreeMap::new();
    for (k, v) in &plan.inventory_entries {
        old.insert(Id::parse(k)?, v.clone());
    }
    let mut new: BTreeMap<Id, String> = BTreeMap::new();
    for ob in &plan.objects {
        new.insert(Id::parse(&ob.id)?, ob.v.clone());
    }
    let released: Vec<Id> = plan.release.iter().map(|s| Id::parse(s)).collect::<Result<_>>()?;
    let mut union = old;
    union.extend(new.clone());
    for r in &released {
        union.remove(r);
        new.remove(r);
    }
    Ok(Sets { new, union, released })
}

fn rollouts(kube: &Kube, ids: Vec<Id>, timeout: &str) -> Result<()> {
    let mut handles = Vec::new();
    for id in ids {
        let (k, t) = (kube.clone(), timeout.to_string());
        handles.push((id.clone(), std::thread::spawn(move || k.rollout_status(&id, &t))));
    }
    let mut failed = Vec::new();
    for (id, h) in handles {
        match h.join() {
            Ok(Ok(())) => {}
            Ok(Err(e)) => failed.push(format!("{id}: {e:#}")),
            Err(_) => failed.push(format!("{id}: the wait panicked")),
        }
    }
    if !failed.is_empty() {
        bail!(
            "rollout not ready (old Pods keep serving; nothing pruned):\n  {}",
            failed.join("\n  ")
        );
    }
    Ok(())
}

pub fn apply(kube: &Kube, plan: &Plan, o: &Opts) -> Result<Outcome> {
    if let Some(r) = &plan.stale {
        say(r);
        return Ok(Outcome::Stale);
    }
    if plan.blocked() {
        bail!(
            "the plan is blocked ({} refused, {} errors): see the plan summary",
            plan.refused.len(),
            plan.errors.len()
        );
    }
    let inv_ref = InvRef::parse(&plan.inventory)?;
    let now = inventory::read(kube, &inv_ref)?;
    if now.resource_version != plan.inventory_rv {
        bail!("the inventory changed since the plan was made: plan again");
    }
    let s = sets(plan)?;

    // 0. inventory = old ∪ new, phase applying: a run that dies from here on leaves a superset. The write is locked
    // to the resourceVersion the plan read and takes the lease, so a second applier racing this one fails here,
    // before any change, and one planning later is refused while this run lives (inventory.rs).
    let applying = Record {
        entries: s.union.clone(),
        commit: plan.inventory_commit.clone(),
        target: Some(plan.head.clone()),
        run: o.run.clone(),
        phase: "applying".into(),
    };
    let lease = Lease::take(
        kube,
        &inv_ref,
        applying.clone(),
        now.exists.then_some(plan.inventory_rv.as_str()),
        o.lease_secs,
    )?;

    for phase in [Phase::Cluster, Phase::Sealed, Phase::Config, Phase::Workload] {
        let objs: Vec<&crate::plan::PlanObj> = plan.objects.iter().filter(|x| x.phase == phase).collect();
        if objs.is_empty() {
            continue;
        }
        let as_ = (phase == Phase::Sealed).then_some(plan.sealed_as.as_str());
        for r in objs.iter().filter(|x| x.action == Action::Replace) {
            say(&format!("replace {}: delete, then create", r.id));
            kube.delete(&Id::parse(&r.id)?, as_, &o.prune_timeout)?;
        }
        let before: BTreeMap<String, Option<i64>> = if phase == Phase::Sealed {
            objs.iter()
                .map(|x| {
                    let g = kube
                        .get(&Id::parse(&x.id)?, None)?
                        .and_then(|l| l["metadata"]["generation"].as_i64());
                    Ok((x.id.clone(), g))
                })
                .collect::<Result<_>>()?
        } else {
            BTreeMap::new()
        };
        let manifests: Vec<Value> = objs.iter().map(|x| x.manifest.clone()).collect();
        say(&format!("apply {phase:?}: {} objects", manifests.len()));
        lease.check()?;
        let applied = kube.apply(&manifests, false, as_)?;
        if phase == Phase::Sealed {
            // 6.4 step 2: no workload rolls before the controller has decrypted every changed SealedSecret
            let mut waiting: Vec<Id> = Vec::new();
            for a in &applied {
                let (id, _) = Id::of(a)?;
                let g = a["metadata"]["generation"].as_i64();
                if before.get(&id.to_string()).copied().flatten() != g || !synced(a) {
                    waiting.push(id);
                }
            }
            let start = Instant::now();
            while !waiting.is_empty() {
                let mut still = Vec::new();
                let mut why = String::new();
                for id in waiting {
                    let l = kube.get(&id, None)?;
                    if !l.as_ref().is_some_and(synced) {
                        why = format!("{id}: {}", l.as_ref().map_or_else(|| "gone".into(), sealed_message));
                        still.push(id);
                    }
                }
                waiting = still;
                if waiting.is_empty() {
                    break;
                }
                if start.elapsed() > o.sealed_timeout {
                    bail!(
                        "{} SealedSecret(s) not Synced after {:?} ({why}): no workload applied",
                        waiting.len(),
                        o.sealed_timeout
                    );
                }
                std::thread::sleep(Duration::from_secs(2));
            }
        }
    }

    // 5. every rollout in the render, in parallel
    let mut wait = Vec::new();
    for ob in &plan.objects {
        let id = Id::parse(&ob.id)?;
        if id.has_rollout() {
            wait.push(id);
        }
    }
    rollouts(kube, wait, &o.rollout_timeout)?;

    prune_and_finish(kube, plan, o, lease, &s)?;
    Ok(Outcome::Done)
}

/// Which Pods in one namespace are draining: every Pod owned by `all` (a pruned workload, its ReplicaSets or Jobs; an
/// updated Deployment's older ReplicaSets), and Pods owned by a key of `rev` (an updated DaemonSet or StatefulSet)
/// that are terminating or carry another `controller-revision-hash` than the value.
#[derive(Clone, Debug, Default)]
struct Drain {
    all: BTreeSet<String>,
    rev: BTreeMap<String, String>,
    terminating: BTreeSet<String>,
}

fn template_grace(o: &Value) -> Option<i64> {
    o["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]
        .as_i64()
        .or_else(|| o["spec"]["jobTemplate"]["spec"]["template"]["spec"]["terminationGracePeriodSeconds"].as_i64())
}

fn owned_by(o: &Value, uid: &str) -> bool {
    o["metadata"]["ownerReferences"]
        .as_array()
        .is_some_and(|r| r.iter().any(|r| r["uid"] == uid))
}

/// A pruned workload: everything it owns drains after its (background-cascade) deletion.
fn pruned_owners(kube: &Kube, id: &Id, live: &Value, d: &mut Drain) -> Result<i64> {
    let uid = live["metadata"]["uid"].as_str().unwrap_or("").to_string();
    let mut grace = template_grace(live).unwrap_or(30);
    d.all.insert(uid.clone());
    let child = match (id.group.as_str(), id.kind.as_str()) {
        ("apps", "Deployment") => Some("replicasets.apps"),
        ("batch", "CronJob") => Some("jobs.batch"),
        _ => None,
    };
    if let Some(res) = child {
        for c in kube.list(res, &id.ns)? {
            if owned_by(&c, &uid) {
                if let Some(u) = c["metadata"]["uid"].as_str() {
                    d.all.insert(u.to_string());
                }
                grace = grace.max(template_grace(&c).unwrap_or(0));
            }
        }
    }
    Ok(grace)
}

/// An updated workload of the render (6.4 step 6: "a draining old Pod keeps them until it is gone from the
/// rollout"): its Pods of older revisions, still terminating after `rollout status` returned.
fn updated_owners(kube: &Kube, id: &Id, d: &mut Drain) -> Result<i64> {
    let Some(live) = kube.get(id, None)? else { return Ok(0) };
    let uid = live["metadata"]["uid"].as_str().unwrap_or("").to_string();
    let grace = template_grace(&live).unwrap_or(30);
    match id.kind.as_str() {
        "Deployment" => {
            let cur = live["metadata"]["annotations"]["deployment.kubernetes.io/revision"].as_str();
            for rs in kube.list("replicasets.apps", &id.ns)? {
                if !owned_by(&rs, &uid) {
                    continue;
                }
                let r = rs["metadata"]["annotations"]["deployment.kubernetes.io/revision"].as_str();
                if let Some(u) = rs["metadata"]["uid"].as_str() {
                    if r != cur {
                        d.all.insert(u.to_string());
                    } else {
                        d.terminating.insert(u.to_string());
                    }
                }
            }
        }
        "StatefulSet" => {
            if let Some(r) = live["status"]["updateRevision"].as_str() {
                d.rev.insert(uid, r.to_string());
            }
        }
        "DaemonSet" => {
            let mut best: Option<(i64, String)> = None;
            for cr in kube.list("controllerrevisions.apps", &id.ns)? {
                if !owned_by(&cr, &uid) {
                    continue;
                }
                let n = cr["revision"].as_i64().unwrap_or(0);
                let h = cr["metadata"]["labels"]["controller-revision-hash"]
                    .as_str()
                    .unwrap_or("");
                if best.as_ref().is_none_or(|(b, _)| n > *b) {
                    best = Some((n, h.to_string()));
                }
            }
            if let Some((_, h)) = best {
                d.rev.insert(uid, h);
            }
        }
        _ => {}
    }
    Ok(grace)
}

fn draining_pods(kube: &Kube, ns: &str, d: &Drain) -> Result<Vec<(String, i64)>> {
    let mut out = Vec::new();
    for p in kube.list("pods", ns)? {
        let owners: Vec<&str> = p["metadata"]["ownerReferences"]
            .as_array()
            .map(|r| r.iter().filter_map(|r| r["uid"].as_str()).collect())
            .unwrap_or_default();
        let deleting = !p["metadata"]["deletionTimestamp"].is_null();
        let hash = p["metadata"]["labels"]["controller-revision-hash"].as_str();
        let drains = owners.iter().any(|u| {
            d.all.contains(*u)
                || (deleting && (d.terminating.contains(*u) || d.rev.contains_key(*u)))
                || d.rev.get(*u).is_some_and(|cur| hash != Some(cur.as_str()))
        });
        if drains {
            out.push((
                p["metadata"]["name"].as_str().unwrap_or("").to_string(),
                p["spec"]["terminationGracePeriodSeconds"].as_i64().unwrap_or(30),
            ));
        }
    }
    Ok(out)
}

/// Step 6, prune: workloads, then Services, PVCs, PDBs and the like, then (once the Pods of the pruned workloads and
/// the old Pods of the render's updated workloads are gone) NetworkPolicies, ConfigMaps, SealedSecrets,
/// ServiceAccounts, Roles and RoleBindings, then cross-namespace and cluster-scoped objects. Step 7: inventory = the
/// render, phase `done`.
fn prune_and_finish(kube: &Kube, plan: &Plan, o: &Opts, lease: Lease, s: &Sets) -> Result<String> {
    let home = &plan.home;
    let mut prune: Vec<Id> = plan.prune.iter().map(|p| Id::parse(&p.id)).collect::<Result<_>>()?;
    prune.sort_by_key(|i| prune_rank(i, home));
    let mut draining: BTreeMap<String, Drain> = BTreeMap::new();
    let mut grace = 0i64;
    let mut waited = false;
    for id in &prune {
        let rank = prune_rank(id, home);
        if rank >= 2 && !waited {
            waited = true;
            // the old Pods of updated workloads: rollout status has returned, but they may still be in their grace
            // period (egress: 625 s) and use a renamed policy or a superseded generator ConfigMap
            for ob in plan
                .objects
                .iter()
                .filter(|x| matches!(x.action, Action::Update | Action::Replace))
            {
                let wid = Id::parse(&ob.id)?;
                if wid.has_rollout() && home.contains(&wid.ns) {
                    let g = updated_owners(kube, &wid, draining.entry(wid.ns.clone()).or_default())?;
                    grace = grace.max(g);
                }
            }
            wait_drained(kube, &draining, grace, o.drain_margin)?;
        }
        if rank == 0
            && let Some(live) = kube.get(id, None)?
        {
            let g = pruned_owners(kube, id, &live, draining.entry(id.ns.clone()).or_default())?;
            grace = grace.max(g);
        }
        lease.check()?;
        say(&format!("prune {id}"));
        let as_ = id.is_sealed_secret().then_some(plan.sealed_as.as_str());
        kube.delete(id, as_, &o.prune_timeout)?;
    }
    if !waited {
        // nothing a draining Pod uses was pruned; still let the pruned workloads' Pods go before `done`
        wait_drained(kube, &draining, grace, o.drain_margin)?;
    }

    let rv = lease.finish(Record {
        entries: s.new.clone(),
        commit: Some(plan.head.clone()),
        target: None,
        run: o.run.clone(),
        phase: "done".into(),
    })?;
    say(&format!(
        "applied {} ({} objects, {} pruned, {} released)",
        plan.head,
        s.new.len(),
        prune.len(),
        s.released.len()
    ));
    Ok(rv)
}

fn wait_drained(kube: &Kube, draining: &BTreeMap<String, Drain>, grace: i64, margin: Duration) -> Result<()> {
    if draining.is_empty() {
        return Ok(());
    }
    let mut grace = grace;
    let start = Instant::now();
    loop {
        let mut left = Vec::new();
        for (ns, d) in draining {
            for (name, g) in draining_pods(kube, ns, d)? {
                grace = grace.max(g);
                left.push(format!("{ns}/{name}"));
            }
        }
        if left.is_empty() {
            return Ok(());
        }
        let limit = Duration::from_secs(u64::try_from(grace).unwrap_or(30)) + margin;
        if start.elapsed() > limit {
            bail!(
                "draining Pods still there after {limit:?}: {} (the NetworkPolicies, ConfigMaps, SealedSecrets and RBAC they may use are kept)",
                left.join(", ")
            );
        }
        if start.elapsed().as_secs() % 30 < 2 {
            say(&format!(
                "waiting for {} draining Pod(s): {}",
                left.len(),
                left.join(", ")
            ));
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}
