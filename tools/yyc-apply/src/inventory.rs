//! The inventory ConfigMap (deploy-workflows 6.1): `data.entries` = one `<id> <version>` per line, annotations
//! `yyc-deploy/{commit,target,run,applied-at,phase,lease-until}`. `commit` is the last commit applied in full; while a
//! run is applying, `target` names its commit and the entries are old ∪ new, so a run that dies leaves a superset.
//! Every write after the first carries the resourceVersion it read (an optimistic lock), so two writes made from the same read cannot both land. On top of that
//! a running applier holds a lease: `lease-until` (unix seconds) with `run` naming the holder, renewed by a heartbeat
//! every quarter of `--lease-secs` while `apply` runs, and cleared when it ends. `plan`
//! refuses while another run's lease is live (`--force` overrides it for a holder known to be dead), so a second
//! applier (a session, a re-run) cannot start applying and pruning beside a run in progress. A run killed outright
//! (SIGKILL, a lost runner) leaves its lease to expire within `--lease-secs`.

use crate::kube::Kube;
use crate::obj::Id;
use anyhow::{Context, Result, anyhow, bail};
use serde_json::json;
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const MANAGER: &str = "yyc-apply-inventory";

#[derive(Clone, Debug)]
pub struct InvRef {
    pub ns: String,
    pub name: String,
}

impl InvRef {
    pub fn parse(s: &str) -> Result<InvRef> {
        let (ns, name) = s
            .split_once('/')
            .ok_or_else(|| anyhow!("--inventory takes NAMESPACE/NAME, got `{s}`"))?;
        Ok(InvRef {
            ns: ns.into(),
            name: name.into(),
        })
    }
    pub fn id(&self) -> Id {
        Id {
            ns: self.ns.clone(),
            name: self.name.clone(),
            group: String::new(),
            kind: "ConfigMap".into(),
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct Inventory {
    pub exists: bool,
    pub resource_version: String,
    pub entries: BTreeMap<Id, String>,
    pub commit: Option<String>,
    pub target: Option<String>,
    pub phase: Option<String>,
    /// the run that last wrote it (the lease holder while `lease_until` is live)
    pub run: Option<String>,
    pub lease_until: Option<u64>,
}

impl Inventory {
    /// The run holding a live lease, other than `me`.
    pub fn held_by_other(&self, me: &str) -> Option<(String, u64)> {
        let until = self.lease_until?;
        let run = self.run.clone().unwrap_or_default();
        (until > now_secs() && run != me).then_some((run, until))
    }
}

pub fn parse_entries(text: &str) -> Result<BTreeMap<Id, String>> {
    let mut m = BTreeMap::new();
    for line in text.lines().map(str::trim).filter(|l| !l.is_empty()) {
        let (id, v) = line.split_once(' ').unwrap_or((line, ""));
        m.insert(
            Id::parse(id).with_context(|| format!("inventory line `{line}`"))?,
            v.trim().to_string(),
        );
    }
    Ok(m)
}

pub fn read(kube: &Kube, r: &InvRef) -> Result<Inventory> {
    let Some(cm) = kube.get(&r.id(), None)? else {
        return Ok(Inventory::default());
    };
    let ann = |k: &str| cm["metadata"]["annotations"][k].as_str().map(str::to_string);
    Ok(Inventory {
        exists: true,
        resource_version: cm["metadata"]["resourceVersion"].as_str().unwrap_or("").into(),
        entries: parse_entries(cm["data"]["entries"].as_str().unwrap_or(""))?,
        commit: ann("yyc-deploy/commit").filter(|s| !s.is_empty()),
        target: ann("yyc-deploy/target").filter(|s| !s.is_empty()),
        phase: ann("yyc-deploy/phase"),
        run: ann("yyc-deploy/run").filter(|s| !s.is_empty()),
        lease_until: ann("yyc-deploy/lease-until").and_then(|s| s.parse().ok()),
    })
}

pub fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

fn now() -> String {
    let o = std::process::Command::new("date")
        .args(["-u", "+%Y-%m-%dT%H:%M:%SZ"])
        .output();
    o.map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

/// What an inventory write stores.
#[derive(Clone, Debug)]
pub struct Record {
    pub entries: BTreeMap<Id, String>,
    pub commit: Option<String>,
    pub target: Option<String>,
    pub run: String,
    pub phase: String,
}

/// Writes the inventory and returns its new resourceVersion. Fails, writing nothing, when the inventory is not at
/// `expect_rv` (or exists although `expect_rv` is None): another applier wrote it in between.
pub fn write(kube: &Kube, r: &InvRef, w: &Record, lease_until: Option<u64>, expect_rv: Option<&str>) -> Result<String> {
    let text: String = w.entries.iter().map(|(id, v)| format!("{id} {v}\n")).collect();
    let mut cm = json!({
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {
            "name": r.name, "namespace": r.ns,
            "annotations": {
                "yyc-deploy/commit": w.commit.as_deref().unwrap_or(""),
                "yyc-deploy/target": w.target.as_deref().unwrap_or(""),
                "yyc-deploy/run": w.run,
                "yyc-deploy/applied-at": now(),
                "yyc-deploy/phase": w.phase,
                "yyc-deploy/lease-until": lease_until.map(|t| t.to_string()).unwrap_or_default(),
            }
        },
        "data": {"entries": text},
    });
    let args: Vec<String> = match expect_rv.filter(|v| !v.is_empty()) {
        // server-side apply with metadata.resourceVersion is a precondition: 409 Conflict when it moved
        Some(rv) => {
            cm["metadata"]["resourceVersion"] = json!(rv);
            ["apply", "--server-side", "--force-conflicts", "-f", "-", "-o", "json"]
                .iter()
                .map(|s| (*s).to_string())
                .chain([format!("--field-manager={MANAGER}")])
                .collect()
        }
        // the first write: create, which fails when the inventory appeared meanwhile
        None => ["create", "-f", "-", "-o", "json"]
            .iter()
            .map(|s| (*s).to_string())
            .chain([format!("--field-manager={MANAGER}")])
            .collect(),
    };
    let out = kube.ok(&args, Some(&serde_json::to_vec(&cm)?)).with_context(|| {
        format!(
            "writing the inventory {}/{} (changed by another applier? plan again)",
            r.ns, r.name
        )
    })?;
    let v: serde_json::Value = serde_json::from_slice(&out).context("the inventory write's answer")?;
    Ok(v["metadata"]["resourceVersion"].as_str().unwrap_or("").to_string())
}

struct LeaseState {
    rv: String,
    rec: Record,
    /// set when a renewal failed (another writer moved the inventory): every later write fails
    lost: Option<String>,
    released: bool,
}

/// A held lease on the inventory: a heartbeat thread renews `lease-until` while this lives; `finish` writes the last
/// record and clears it; dropping it unfinished (an error path) clears it best-effort, keeping the record as it was.
pub struct Lease {
    kube: Kube,
    inv: InvRef,
    st: Arc<Mutex<LeaseState>>,
    stop: Arc<AtomicBool>,
    beat: Option<JoinHandle<()>>,
}

fn lock(st: &Mutex<LeaseState>) -> std::sync::MutexGuard<'_, LeaseState> {
    st.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl Lease {
    /// Writes `rec` holding the lease (expecting `expect_rv`; None creates the inventory) and starts the heartbeat.
    pub fn take(kube: &Kube, inv: &InvRef, rec: Record, expect_rv: Option<&str>, secs: u64) -> Result<Lease> {
        let secs = secs.max(4);
        let rv = write(kube, inv, &rec, Some(now_secs() + secs), expect_rv)?;
        let st = Arc::new(Mutex::new(LeaseState {
            rv,
            rec,
            lost: None,
            released: false,
        }));
        let stop = Arc::new(AtomicBool::new(false));
        let (k, i, s, sp) = (kube.clone(), inv.clone(), st.clone(), stop.clone());
        let beat = std::thread::spawn(move || {
            let every = Duration::from_secs(secs / 4);
            let mut last = Instant::now();
            while !sp.load(Ordering::Relaxed) {
                std::thread::sleep(Duration::from_millis(200));
                if last.elapsed() < every {
                    continue;
                }
                last = Instant::now();
                let mut g = lock(&s);
                if g.lost.is_some() || g.released || sp.load(Ordering::Relaxed) {
                    continue;
                }
                match write(&k, &i, &g.rec, Some(now_secs() + secs), Some(&g.rv)) {
                    Ok(rv) => g.rv = rv,
                    Err(e) => g.lost = Some(format!("{e:#}")),
                }
            }
        });
        Ok(Lease {
            kube: kube.clone(),
            inv: inv.clone(),
            st,
            stop,
            beat: Some(beat),
        })
    }

    /// Fails when a renewal found the inventory moved by another writer.
    pub fn check(&self) -> Result<()> {
        if let Some(e) = &lock(&self.st).lost {
            bail!("the inventory lease was lost (another applier wrote it): {e}");
        }
        Ok(())
    }

    fn stop_beat(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(h) = self.beat.take() {
            let _ = h.join();
        }
    }

    /// Writes the final `rec` and clears the lease; returns the new resourceVersion.
    pub fn finish(mut self, rec: Record) -> Result<String> {
        self.stop_beat();
        let mut g = lock(&self.st);
        if let Some(e) = &g.lost {
            bail!("the inventory lease was lost (another applier wrote it): {e}");
        }
        let rv = write(&self.kube, &self.inv, &rec, None, Some(&g.rv))?;
        g.released = true;
        Ok(rv)
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        self.stop_beat();
        let mut g = lock(&self.st);
        if g.released || g.lost.is_some() {
            return;
        }
        g.released = true;
        // an error path: the record stays as it was (a superset while `applying`); only the lease goes
        if let Err(e) = write(&self.kube, &self.inv, &g.rec, None, Some(&g.rv)) {
            eprintln!("yyc-apply: clearing the inventory lease: {e:#} (it expires by itself)");
        }
    }
}
