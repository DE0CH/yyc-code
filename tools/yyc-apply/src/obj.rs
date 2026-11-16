//! Object ids (Flux's inventory format), the kind table and the apply phases.

use anyhow::{Result, anyhow, bail};
use serde_json::Value;
use std::fmt;

/// `<namespace>_<name>_<group>_<Kind>`: the id format of Flux's inventory (namespace and group empty when there is
/// none).
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Id {
    pub ns: String,
    pub name: String,
    pub group: String,
    pub kind: String,
}

impl fmt::Display for Id {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}_{}_{}_{}", self.ns, self.name, self.group, self.kind)
    }
}

fn dns_ok(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 253
        && s.chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-' || c == '.')
        && !s.starts_with(['-', '.'])
}

impl Id {
    pub fn parse(s: &str) -> Result<Id> {
        let parts: Vec<&str> = s.trim().split('_').collect();
        if parts.len() != 4 {
            bail!("`{s}` is not an object id <namespace>_<name>_<group>_<Kind>");
        }
        let (ns, name, group, kind) = (parts[0], parts[1], parts[2], parts[3]);
        if !(ns.is_empty() || dns_ok(ns)) || !dns_ok(name) || !(group.is_empty() || dns_ok(group)) {
            bail!("`{s}`: namespace, name or group is not a DNS name");
        }
        if kind.is_empty()
            || !kind.starts_with(|c: char| c.is_ascii_uppercase())
            || !kind.chars().all(|c| c.is_ascii_alphanumeric())
        {
            bail!("`{s}`: `{kind}` is not a Kind");
        }
        Ok(Id {
            ns: ns.into(),
            name: name.into(),
            group: group.into(),
            kind: kind.into(),
        })
    }

    /// The id and the version of a manifest.
    pub fn of(obj: &Value) -> Result<(Id, String)> {
        let api = obj["apiVersion"]
            .as_str()
            .ok_or_else(|| anyhow!("object without apiVersion: {obj}"))?;
        let kind = obj["kind"]
            .as_str()
            .ok_or_else(|| anyhow!("object without kind: {obj}"))?;
        let name = obj["metadata"]["name"]
            .as_str()
            .ok_or_else(|| anyhow!("{kind} without metadata.name"))?;
        let ns = obj["metadata"]["namespace"].as_str().unwrap_or("");
        let (group, version) = match api.split_once('/') {
            Some((g, v)) => (g, v),
            None => ("", api),
        };
        let id = Id {
            ns: ns.into(),
            name: name.into(),
            group: group.into(),
            kind: kind.into(),
        };
        if let Some(k) = kind_info(group, kind) {
            if k.namespaced && ns.is_empty() {
                bail!("{id}: a namespaced {kind} without metadata.namespace");
            }
            if !k.namespaced && !ns.is_empty() {
                bail!("{id}: {kind} is cluster-scoped but has metadata.namespace");
            }
        }
        Ok((id, version.into()))
    }

    /// The resource argument for kubectl (`deployments.apps`, `configmaps`).
    pub fn kubectl_type(&self) -> String {
        let res = kind_info(&self.group, &self.kind).map_or_else(|| self.kind.to_lowercase(), |k| k.resource.into());
        if self.group.is_empty() {
            res
        } else {
            format!("{res}.{}", self.group)
        }
    }

    /// `kubectl get <type> <name> [-n ns]` arguments.
    pub fn kubectl_ref(&self) -> Vec<String> {
        let mut v = vec![self.kubectl_type(), self.name.clone()];
        if !self.ns.is_empty() {
            v.push("-n".into());
            v.push(self.ns.clone());
        }
        v
    }

    pub fn is_workload(&self) -> bool {
        matches!(
            (self.group.as_str(), self.kind.as_str()),
            ("apps", "Deployment" | "DaemonSet" | "StatefulSet")
                | ("autoscaling", "HorizontalPodAutoscaler")
                | ("batch", "CronJob" | "Job")
        )
    }

    /// Deployments, DaemonSets and StatefulSets: `kubectl rollout status` after the apply.
    pub fn has_rollout(&self) -> bool {
        self.group == "apps" && matches!(self.kind.as_str(), "Deployment" | "DaemonSet" | "StatefulSet")
    }

    pub fn is_sealed_secret(&self) -> bool {
        self.group == "bitnami.com" && self.kind == "SealedSecret"
    }

    pub fn is_rbac(&self) -> bool {
        self.group == "rbac.authorization.k8s.io"
    }
}

#[derive(Clone, Copy, Debug)]
pub struct KindInfo {
    pub resource: &'static str,
    pub namespaced: bool,
}

/// (group, Kind, resource, namespaced): every kind the YYC folders render or Flux's inventories hold. An unknown kind
/// fails `check` (so a new kind is added here and to the deployer's rights first).
const KINDS: &[(&str, &str, &str, bool)] = &[
    ("", "ConfigMap", "configmaps", true),
    ("", "Secret", "secrets", true),
    ("", "Service", "services", true),
    ("", "ServiceAccount", "serviceaccounts", true),
    ("", "PersistentVolumeClaim", "persistentvolumeclaims", true),
    ("", "LimitRange", "limitranges", true),
    ("", "ResourceQuota", "resourcequotas", true),
    ("", "Pod", "pods", true),
    ("", "Namespace", "namespaces", false),
    ("", "PersistentVolume", "persistentvolumes", false),
    ("apps", "Deployment", "deployments", true),
    ("apps", "DaemonSet", "daemonsets", true),
    ("apps", "StatefulSet", "statefulsets", true),
    ("apps", "ReplicaSet", "replicasets", true),
    ("batch", "Job", "jobs", true),
    ("batch", "CronJob", "cronjobs", true),
    (
        "autoscaling",
        "HorizontalPodAutoscaler",
        "horizontalpodautoscalers",
        true,
    ),
    ("policy", "PodDisruptionBudget", "poddisruptionbudgets", true),
    ("networking.k8s.io", "NetworkPolicy", "networkpolicies", true),
    ("cilium.io", "CiliumNetworkPolicy", "ciliumnetworkpolicies", true),
    ("discovery.k8s.io", "EndpointSlice", "endpointslices", true),
    ("networking.k8s.io", "Ingress", "ingresses", true),
    ("rbac.authorization.k8s.io", "Role", "roles", true),
    ("rbac.authorization.k8s.io", "RoleBinding", "rolebindings", true),
    ("rbac.authorization.k8s.io", "ClusterRole", "clusterroles", false),
    (
        "rbac.authorization.k8s.io",
        "ClusterRoleBinding",
        "clusterrolebindings",
        false,
    ),
    ("node.k8s.io", "RuntimeClass", "runtimeclasses", false),
    ("scheduling.k8s.io", "PriorityClass", "priorityclasses", false),
    ("storage.k8s.io", "StorageClass", "storageclasses", false),
    (
        "apiextensions.k8s.io",
        "CustomResourceDefinition",
        "customresourcedefinitions",
        false,
    ),
    ("bitnami.com", "SealedSecret", "sealedsecrets", true),
    ("helm.toolkit.fluxcd.io", "HelmRelease", "helmreleases", true),
    ("source.toolkit.fluxcd.io", "HelmRepository", "helmrepositories", true),
    ("source.toolkit.fluxcd.io", "GitRepository", "gitrepositories", true),
    ("kustomize.toolkit.fluxcd.io", "Kustomization", "kustomizations", true),
    ("postgresql.cnpg.io", "Cluster", "clusters", true),
    ("postgresql.cnpg.io", "Database", "databases", true),
    ("postgresql.cnpg.io", "ScheduledBackup", "scheduledbackups", true),
];

pub fn kind_info(group: &str, kind: &str) -> Option<KindInfo> {
    KINDS
        .iter()
        .find(|(g, k, _, _)| *g == group && *k == kind)
        .map(|&(_, _, resource, namespaced)| KindInfo { resource, namespaced })
}

/// The apply order of deploy-workflows 6.4 (prune runs in the reverse sense, see `prune_rank`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
pub enum Phase {
    /// 1: cluster-scoped objects and objects outside the folder's own namespaces
    Cluster,
    /// 2: the sealed folder, applied as the fenced ServiceAccount
    Sealed,
    /// 3: everything that is not a workload
    Config,
    /// 4: Deployments, DaemonSets, StatefulSets, HPAs, CronJobs, Jobs
    Workload,
}

impl Phase {
    pub fn of(id: &Id, sealed: bool, home: &[String]) -> Phase {
        if sealed {
            Phase::Sealed
        } else if id.ns.is_empty() || !home.contains(&id.ns) {
            Phase::Cluster
        } else if id.is_workload() {
            Phase::Workload
        } else {
            Phase::Config
        }
    }
}

/// Prune order (6.4 step 6): workloads first, then the rest, then what a draining old Pod may still use until it is
/// gone (NetworkPolicies, ConfigMaps, SealedSecrets, and its ServiceAccount, Roles and RoleBindings), then
/// cluster-scoped and cross-namespace objects.
pub fn prune_rank(id: &Id, home: &[String]) -> u8 {
    let cross = id.ns.is_empty() || !home.contains(&id.ns);
    if cross {
        3
    } else if id.is_workload() {
        0
    } else if (id.group == "networking.k8s.io" && id.kind == "NetworkPolicy")
        || (id.group.is_empty() && id.kind == "ConfigMap")
        || id.is_sealed_secret()
        || (id.group.is_empty() && id.kind == "ServiceAccount")
        || id.is_rbac()
    {
        2
    } else {
        1
    }
}

/// kustomize's hash suffix alphabet (api/hasher `encode`: hex with 0, 1, 3, a, e mapped to g, h, k, m, t).
const GENERATOR_HASH: &str = "bcdfghkmt2456789";

/// The base of a kustomize generator name `<base>-<10-character hash>`, for a ConfigMap or Secret only (the kinds
/// kustomize's generators make; deploy-workflows 6.3).
pub fn generator_base(id: &Id) -> Option<&str> {
    if !(id.group.is_empty() && matches!(id.kind.as_str(), "ConfigMap" | "Secret")) {
        return None;
    }
    let (base, hash) = id.name.rsplit_once('-')?;
    (hash.len() == 10 && hash.chars().all(|c| GENERATOR_HASH.contains(c)) && !base.is_empty()).then_some(base)
}

/// The manifest with the fields the server owns removed, for comparing a live object with a dry-run result.
pub fn normalized(obj: &Value) -> Value {
    let mut o = obj.clone();
    if let Some(m) = o.get_mut("metadata").and_then(Value::as_object_mut) {
        for k in [
            "managedFields",
            "resourceVersion",
            "generation",
            "creationTimestamp",
            "uid",
            "selfLink",
        ] {
            m.remove(k);
        }
    }
    if let Some(m) = o.as_object_mut() {
        m.remove("status");
    }
    o
}

/// Paths where two JSON values differ (at most `limit`).
pub fn diff_paths(a: &Value, b: &Value, limit: usize) -> Vec<String> {
    fn walk(a: &Value, b: &Value, path: &str, out: &mut Vec<String>, limit: usize) {
        if out.len() >= limit || a == b {
            return;
        }
        match (a, b) {
            (Value::Object(x), Value::Object(y)) => {
                let mut keys: Vec<&String> = x.keys().chain(y.keys()).collect();
                keys.sort();
                keys.dedup();
                for k in keys {
                    let p = format!("{path}.{k}");
                    match (x.get(k), y.get(k)) {
                        (Some(u), Some(v)) => walk(u, v, &p, out, limit),
                        _ => out.push(p),
                    }
                    if out.len() >= limit {
                        return;
                    }
                }
            }
            (Value::Array(x), Value::Array(y)) if x.len() == y.len() => {
                for (i, (u, v)) in x.iter().zip(y).enumerate() {
                    walk(u, v, &format!("{path}[{i}]"), out, limit);
                }
            }
            _ => out.push(if path.is_empty() { ".".into() } else { path.into() }),
        }
    }
    let mut out = Vec::new();
    walk(a, b, "", &mut out, limit);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_round_trip_and_reject_garbage() {
        let id = Id::parse("jarvis_web_apps_Deployment").unwrap();
        assert_eq!(id.kubectl_type(), "deployments.apps");
        assert_eq!(id.to_string(), "jarvis_web_apps_Deployment");
        assert_eq!(
            Id::parse("_gvisor_node.k8s.io_RuntimeClass").unwrap().kubectl_type(),
            "runtimeclasses.node.k8s.io"
        );
        assert_eq!(Id::parse("jarvis_web__Service").unwrap().kubectl_type(), "services");
        for bad in [
            "jarvis/web",
            "jarvis_web_apps",
            "jarvis_Web_apps_Deployment",
            "jarvis_web_apps_deployment",
            "a_b_c_D_e",
        ] {
            assert!(Id::parse(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn generator_names() {
        let g = |s: &str| generator_base(&Id::parse(s).unwrap()).map(str::to_string);
        assert_eq!(
            g("jarvis_jarvis-config-52d68hh998__ConfigMap").as_deref(),
            Some("jarvis-config")
        );
        assert_eq!(g("jarvis_ui-env-kt2456789b__Secret").as_deref(), Some("ui-env"));
        assert_eq!(g("jarvis_yyc-catalogue__ConfigMap"), None);
        assert_eq!(g("jarvis_web__ConfigMap"), None);
        // not a generator kind, or not kustomize's alphabet
        assert_eq!(g("yyc-ai_sandbox-controller_apps_Deployment"), None);
        assert_eq!(g("yyc-ai_sandbox-52d68hh998_apps_Deployment"), None);
        assert_eq!(g("yyc-ai_sandbox-controller__ConfigMap"), None);
        assert_eq!(g("yyc-ai_cfg-52d68hh990__ConfigMap"), None);
    }

    #[test]
    fn phases() {
        let home = vec!["jarvis".to_string()];
        assert_eq!(
            Phase::of(&Id::parse("jarvis_web_apps_Deployment").unwrap(), false, &home),
            Phase::Workload
        );
        assert_eq!(
            Phase::of(&Id::parse("jarvis_web__Service").unwrap(), false, &home),
            Phase::Config
        );
        assert_eq!(
            Phase::of(&Id::parse("kube-system_r_apps_DaemonSet").unwrap(), false, &home),
            Phase::Cluster
        );
        assert_eq!(
            Phase::of(&Id::parse("_pv__PersistentVolume").unwrap(), false, &home),
            Phase::Cluster
        );
    }

    #[test]
    fn diffs() {
        let a = serde_json::json!({"a": {"b": 1, "c": [1, 2]}, "d": 1});
        let b = serde_json::json!({"a": {"b": 2, "c": [1, 3]}, "e": 1});
        assert_eq!(diff_paths(&a, &b, 10), vec![".a.b", ".a.c[1]", ".d", ".e"]);
    }
}
