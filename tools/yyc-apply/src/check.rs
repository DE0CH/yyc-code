//! `check`: the deploy-specific items of job `check` (spec-platform 13.3 items 9-12 = deploy-workflows 6.6). Reads
//! the repo only, no cluster. Items 1-8 (render, no switches, zero-downtime shape, pins, …) are WP-31's steps.

use crate::git::{self, Trailers};
use crate::kube::parse_yaml_stream;
use crate::obj::{Id, kind_info};
use crate::plan::render;
use anyhow::{Context, Result};
use serde_json::Value;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct Opts {
    pub repo: PathBuf,
    pub folder: PathBuf,
    pub sealed: Option<PathBuf>,
    pub access: Vec<PathBuf>,
    pub secrets: Option<PathBuf>,
    pub commits: Option<String>,
    pub exclude: Vec<Id>,
    pub home: Vec<String>,
    pub deployer: String,
    /// objects the cluster already holds: item 9 does not ask for `create` on them
    pub existing: Vec<Id>,
    pub sealed_as: String,
    pub kustomize: String,
}

#[derive(Clone, Debug)]
struct Rule {
    groups: Vec<String>,
    resources: Vec<String>,
    verbs: Vec<String>,
    names: Vec<String>,
}

fn strs(v: &Value) -> Vec<String> {
    v.as_array()
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

fn rules(v: &Value) -> Vec<Rule> {
    v["rules"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|r| Rule {
                    groups: strs(&r["apiGroups"]),
                    resources: strs(&r["resources"]),
                    verbs: strs(&r["verbs"]),
                    names: strs(&r["resourceNames"]),
                })
                .collect()
        })
        .unwrap_or_default()
}

fn has(list: &[String], x: &str) -> bool {
    list.iter().any(|l| l == "*" || l == x)
}

impl Rule {
    fn allows(&self, group: &str, resource: &str, verb: &str, name: Option<&str>) -> bool {
        has(&self.groups, group)
            && has(&self.resources, resource)
            && has(&self.verbs, verb)
            && (self.names.is_empty() || name.is_some_and(|n| self.names.iter().any(|x| x == n)))
    }
}

#[derive(Clone, Debug)]
struct Binding {
    ns: Option<String>,
    role_kind: String,
    role: String,
    subjects: Vec<(String, String, String)>,
}

/// The RBAC objects of the access files: who may do what, evaluated as the API server's authorizer does.
#[derive(Clone, Debug, Default)]
pub struct Rbac {
    cluster_roles: Vec<(String, Vec<Rule>)>,
    roles: Vec<(String, String, Vec<Rule>)>,
    bindings: Vec<Binding>,
    pub namespaces: BTreeSet<String>,
}

/// `User:gha:yyc-ai` or `system:serviceaccount:NS:NAME`.
fn subject_matches(subj: &str, kind: &str, name: &str, ns: &str) -> bool {
    if let Some(rest) = subj.strip_prefix("system:serviceaccount:") {
        let (sns, sname) = rest.split_once(':').unwrap_or(("", rest));
        kind == "ServiceAccount" && name == sname && ns == sns
    } else {
        kind == "User" && name == subj
    }
}

impl Rbac {
    pub fn load(files: &[PathBuf]) -> Result<Rbac> {
        let mut r = Rbac::default();
        for f in files {
            let data = std::fs::read(f).with_context(|| format!("reading {}", f.display()))?;
            for o in parse_yaml_stream(&data).with_context(|| format!("parsing {}", f.display()))? {
                r.add(&o);
            }
        }
        Ok(r)
    }

    pub fn add(&mut self, o: &Value) {
        let name = o["metadata"]["name"].as_str().unwrap_or("").to_string();
        let ns = o["metadata"]["namespace"].as_str().map(str::to_string);
        match o["kind"].as_str().unwrap_or("") {
            "Namespace" => {
                self.namespaces.insert(name);
            }
            "ClusterRole" => self.cluster_roles.push((name, rules(o))),
            "Role" => self.roles.push((ns.unwrap_or_default(), name, rules(o))),
            k @ ("RoleBinding" | "ClusterRoleBinding") => self.bindings.push(Binding {
                ns: if k == "RoleBinding" { ns } else { None },
                role_kind: o["roleRef"]["kind"].as_str().unwrap_or("").into(),
                role: o["roleRef"]["name"].as_str().unwrap_or("").into(),
                subjects: o["subjects"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .map(|s| {
                                let g = |k: &str| s[k].as_str().unwrap_or("").to_string();
                                (g("kind"), g("name"), g("namespace"))
                            })
                            .collect()
                    })
                    .unwrap_or_default(),
            }),
            _ => {}
        }
    }

    fn role_rules(&self, kind: &str, name: &str, ns: Option<&str>) -> Vec<Rule> {
        if kind == "ClusterRole" {
            self.cluster_roles
                .iter()
                .filter(|(n, _)| n == name)
                .flat_map(|(_, r)| r.clone())
                .collect()
        } else {
            self.roles
                .iter()
                .filter(|(rns, n, _)| n == name && Some(rns.as_str()) == ns)
                .flat_map(|(_, _, r)| r.clone())
                .collect()
        }
    }

    /// Rules that apply to `subject` for a request in `ns` (None = cluster-scoped).
    fn rules_for(&self, subject: &str, ns: Option<&str>) -> Vec<Rule> {
        let mut out = Vec::new();
        for b in &self.bindings {
            let applies = match (&b.ns, ns) {
                (None, _) => true,
                (Some(bns), Some(n)) => bns == n,
                (Some(_), None) => false,
            };
            if applies && b.subjects.iter().any(|(k, n, sns)| subject_matches(subject, k, n, sns)) {
                out.extend(self.role_rules(&b.role_kind, &b.role, b.ns.as_deref()));
            }
        }
        out
    }

    pub fn allows(
        &self,
        subject: &str,
        ns: Option<&str>,
        group: &str,
        resource: &str,
        verb: &str,
        name: Option<&str>,
    ) -> bool {
        self.rules_for(subject, ns)
            .iter()
            .any(|r| r.allows(group, resource, verb, name))
    }
}

/// Every line of `infra/secrets.txt`: the Secret's `NAMESPACE/NAME` (a `@cluster` suffix, e.g. `db/pg-backup-s3@talos`,
/// dropped: it only tells two clusters' lines for the same name apart) and the FILE its SealedSecret is written to.
fn secrets_map(path: &Path) -> Result<Vec<(String, String)>> {
    let text = std::fs::read_to_string(path).with_context(|| format!("reading {}", path.display()))?;
    Ok(text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .filter_map(|l| {
            let mut w = l.split_whitespace();
            let name = w.next()?;
            let file = w.nth(1).unwrap_or("");
            let name = name.split_once('@').map_or(name, |(n, _)| n);
            Some((name.to_string(), file.to_string()))
        })
        .collect())
}

/// `p` lexically, `.` and `..` resolved (the map's files need not exist yet).
fn lexical(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for c in p.components() {
        match c {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            c => out.push(c),
        }
    }
    out
}

/// Item 10's map rule for one SealedSecret: a line names it, and (when the map gives files inside the repo) one of
/// its lines writes into the sealed folder, so a name that only the other cluster's line carries does not pass.
fn map_says(map: &[(String, String)], key: &str, repo: &Path, sealed: Option<&Path>) -> Result<(), String> {
    let lines: Vec<&String> = map.iter().filter(|(n, _)| n == key).map(|(_, f)| f).collect();
    if lines.is_empty() {
        return Err(format!("no line `{key}` in infra/secrets.txt"));
    }
    let Some(sealed) = sealed else { return Ok(()) };
    let abs = |p: &Path| {
        lexical(&if p.is_absolute() {
            p.to_path_buf()
        } else {
            std::env::current_dir().unwrap_or_default().join(p)
        })
    };
    let (repo, sealed) = (abs(repo), abs(sealed));
    let inside = |f: &String| f.contains('/') && lexical(&repo.join(f)).starts_with(&sealed);
    // a map whose files are bare names (no folder) says nothing about clusters
    if lines.iter().all(|f| !f.contains('/')) || lines.iter().any(|f| inside(f)) {
        return Ok(());
    }
    Err(format!(
        "infra/secrets.txt's line for `{key}` writes {} (another cluster's), not into {}",
        lines.iter().map(|f| f.as_str()).collect::<Vec<_>>().join(", "),
        sealed.display()
    ))
}

/// Every check failure; empty = pass.
pub fn check(o: &Opts) -> Result<Vec<String>> {
    let mut fail = Vec::new();
    let objs = render(&o.kustomize, &o.folder, o.sealed.as_deref(), &o.exclude)?;
    let rbac = Rbac::load(&o.access)?;
    let secrets = o.secrets.as_deref().map(secrets_map).transpose()?;
    let fence_ns = o
        .sealed_as
        .strip_prefix("system:serviceaccount:")
        .and_then(|r| r.split_once(':'));

    for (m, sealed) in &objs {
        let (id, _) = Id::of(m)?;
        let Some(k) = kind_info(&id.group, &id.kind) else {
            fail.push(format!(
                "{id}: unknown kind {}/{} (add it to yyc-apply's kind table and the deployer's rights)",
                id.group, id.kind
            ));
            continue;
        };
        let ns = (!id.ns.is_empty()).then_some(id.ns.as_str());
        if *sealed {
            // item 10: SealedSecrets only, each in infra/secrets.txt; applied as the fence, which must be allowed there
            if !id.is_sealed_secret() {
                fail.push(format!("10 {id}: the sealed folder holds only kind SealedSecret"));
                continue;
            }
            // 4.2: the deployer writes SealedSecrets only in the folder's own namespaces, whatever else the fence
            // is bound to (it also writes yyc, mailmerge and db for other folders)
            if !o.home.contains(&id.ns) {
                fail.push(format!(
                    "9 {id}: a SealedSecret outside {} (the deployer's sealed writes are limited to them)",
                    o.home.join(", ")
                ));
                continue;
            }
            if let Some(s) = &secrets
                && let Err(e) = map_says(s, &format!("{}/{}", id.ns, id.name), &o.repo, o.sealed.as_deref())
            {
                fail.push(format!("10 {id}: {e}"));
            }
            let fence_ok = ["get", "patch", "create", "delete"]
                .iter()
                .all(|v| rbac.allows(&o.sealed_as, ns, &id.group, k.resource, v, Some(&id.name)));
            let imp_ok = fence_ns.is_some_and(|(fns, fname)| {
                rbac.allows(
                    &o.deployer,
                    Some(fns),
                    "",
                    "serviceaccounts",
                    "impersonate",
                    Some(fname),
                )
            });
            if !fence_ok || !imp_ok {
                fail.push(format!(
                    "9 {id}: the deployer {} may not apply it as {} (bind the fence in {} and grant impersonate: extend news-stream/deploy/clusters/talos/yyc-ai-access.yaml and sealed-secrets.yaml)",
                    o.deployer, o.sealed_as, id.ns
                ));
            }
            continue;
        }
        // item 12: no Namespace, no RBAC outside the folder's namespaces
        if id.group.is_empty() && id.kind == "Namespace" {
            fail.push(format!(
                "12 {id}: namespaces are cluster-level objects (news-stream/deploy/clusters/talos/yyc-ai-access.yaml)"
            ));
            continue;
        }
        if id.is_rbac() && (ns.is_none() || !o.home.contains(&id.ns)) {
            fail.push(format!("12 {id}: RBAC only in {}", o.home.join(", ")));
            continue;
        }
        // item 9: inside the deployer's rights: get and patch by name (a server-side apply), create (which cannot be
        // limited by name) unless the object is known to exist, and delete by name (every rendered object can be
        // pruned by a later commit)
        let mut missing: Vec<&str> = ["get", "patch", "delete"]
            .into_iter()
            .filter(|v| !rbac.allows(&o.deployer, ns, &id.group, k.resource, v, Some(&id.name)))
            .collect();
        if !o.existing.contains(&id) && !rbac.allows(&o.deployer, ns, &id.group, k.resource, "create", None) {
            missing.push("create");
        }
        if !missing.is_empty() {
            fail.push(format!(
                "9 {id}: outside the rights of {} (no {}; extend news-stream/deploy/clusters/talos/yyc-ai-access.yaml first{})",
                o.deployer,
                missing.join(", "),
                if missing == ["create"] {
                    ", or pass --existing for an object the cluster already holds"
                } else {
                    ""
                }
            ));
            continue;
        }
        // a Role or binding the render creates holds only what the deployer holds (no escalate/bind verb)
        let role_rules: Vec<Rule> = match id.kind.as_str() {
            "Role" => rules(m),
            "RoleBinding" => {
                let rk = m["roleRef"]["kind"].as_str().unwrap_or("");
                let rn = m["roleRef"]["name"].as_str().unwrap_or("");
                if rk == "Role" {
                    objs.iter()
                        .find(|(x, _)| {
                            x["kind"] == "Role"
                                && x["metadata"]["name"] == rn
                                && x["metadata"]["namespace"] == id.ns.as_str()
                        })
                        .map(|(x, _)| rules(x))
                        .unwrap_or_default()
                } else {
                    let r = rbac.role_rules("ClusterRole", rn, None);
                    if r.is_empty() {
                        fail.push(format!("9 {id}: binds ClusterRole {rn}, which no access file defines"));
                    }
                    r
                }
            }
            _ => vec![],
        };
        for r in &role_rules {
            for g in &r.groups {
                for res in &r.resources {
                    for v in &r.verbs {
                        let names: Vec<Option<&str>> = if r.names.is_empty() {
                            vec![None]
                        } else {
                            r.names.iter().map(|n| Some(n.as_str())).collect()
                        };
                        for n in names {
                            if !rbac.allows(&o.deployer, ns, g, res, v, n) {
                                fail.push(format!(
                                    "9 {id}: grants {v} {g}/{res}{} which {} does not hold (the API server would refuse it as an escalation)",
                                    n.map(|n| format!(" [{n}]")).unwrap_or_default(),
                                    o.deployer
                                ));
                            }
                        }
                    }
                }
            }
        }
    }

    // item 11: trailers of the pushed commits
    if let Some(range) = &o.commits {
        let serving = o.home.first().cloned().unwrap_or_default();
        let t = Trailers::parse(&git::trailers(&o.repo, range)?, &serving);
        fail.extend(t.errors.into_iter().map(|e| format!("11 {e}")));
    }
    Ok(fail)
}
