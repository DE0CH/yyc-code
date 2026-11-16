//! deploy-workflows 6.6's tests against a real API server (a throwaway kind cluster, `kubectl` on PATH talking to it,
//! image busybox:1.37.0 loaded): `cargo test --test kind -- --ignored`. Each test owns a namespace `t-<name>`.
#![allow(clippy::unwrap_used, clippy::expect_used)]

mod common;
use common::*;
use serde_json::Value;
use std::time::{Duration, Instant};

const FENCE: &str = "system:serviceaccount:t-fence:sealed-secrets-applier";

const SHARED: &str = r#"
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata: {name: sealedsecrets.bitnami.com}
spec:
  group: bitnami.com
  names: {kind: SealedSecret, plural: sealedsecrets, singular: sealedsecret, listKind: SealedSecretList}
  scope: Namespaced
  versions:
    - name: v1alpha1
      served: true
      storage: true
      subresources: {status: {}}
      schema: {openAPIV3Schema: {type: object, x-kubernetes-preserve-unknown-fields: true}}
---
apiVersion: v1
kind: Namespace
metadata: {name: t-fence}
---
apiVersion: v1
kind: ServiceAccount
metadata: {name: sealed-secrets-applier, namespace: t-fence}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: t-sealed-secrets-applier}
rules: [{apiGroups: [bitnami.com], resources: [sealedsecrets], verbs: [get, list, watch, create, update, patch, delete]}]
"#;

/// The shared CRDs and fence, and a fresh namespace `ns` where the fence may write SealedSecrets.
fn setup(ns: &str) {
    kc_stdin(
        &[
            "apply",
            "--server-side",
            "--force-conflicts",
            "--field-manager=test-setup",
            "-f",
            "-",
        ],
        SHARED,
    );
    kc(&[
        "wait",
        "--for=condition=Established",
        "crd/sealedsecrets.bitnami.com",
        "--timeout=60s",
    ]);
    let _ = kubectl(
        &[
            "delete",
            "namespace",
            ns,
            "--ignore-not-found",
            "--wait=true",
            "--timeout=120s",
        ],
        None,
    );
    kc(&["create", "namespace", ns]);
    kc(&[
        "-n",
        ns,
        "create",
        "rolebinding",
        "fence",
        "--clusterrole=t-sealed-secrets-applier",
        "--serviceaccount=t-fence:sealed-secrets-applier",
    ]);
}

fn cm(ns: &str, name: &str, data: &str) -> String {
    format!("apiVersion: v1\nkind: ConfigMap\nmetadata: {{name: {name}, namespace: {ns}}}\ndata: {{k: \"{data}\"}}\n")
}

fn deploy(ns: &str, ready: bool, rev: &str) -> String {
    format!(
        r#"apiVersion: apps/v1
kind: Deployment
metadata: {{name: web, namespace: {ns}}}
spec:
  replicas: 1
  selector: {{matchLabels: {{app: web}}}}
  strategy: {{type: RollingUpdate, rollingUpdate: {{maxSurge: 1, maxUnavailable: 0}}}}
  template:
    metadata: {{labels: {{app: web}}, annotations: {{rev: "{rev}"}}}}
    spec:
      terminationGracePeriodSeconds: 1
      containers:
        - name: c
          image: busybox:1.37.0
          imagePullPolicy: IfNotPresent
          command: [sleep, "3600"]
          readinessProbe: {{exec: {{command: ["{}"]}}, periodSeconds: 1}}
"#,
        if ready { "true" } else { "false" }
    )
}

/// A Deployment `name` whose container ignores SIGTERM (`sleep` as PID 1), so a deleted Pod stays Terminating for its
/// whole grace period.
fn slow_drain(ns: &str, name: &str, grace: u32) -> String {
    format!(
        r#"apiVersion: apps/v1
kind: Deployment
metadata: {{name: {name}, namespace: {ns}}}
spec:
  replicas: 1
  selector: {{matchLabels: {{app: {name}}}}}
  template:
    metadata: {{labels: {{app: {name}}}}}
    spec:
      terminationGracePeriodSeconds: {grace}
      containers:
        - name: c
          image: busybox:1.37.0
          imagePullPolicy: IfNotPresent
          command: [sleep, "3600"]
"#
    )
}

/// `slow_drain` at template revision `rev`, running as ServiceAccount `sa`.
fn slow_drain_rev(ns: &str, name: &str, grace: u32, rev: &str, sa: &str) -> String {
    slow_drain(ns, name, grace)
        .replace(
            &format!("metadata: {{labels: {{app: {name}}}}}"),
            &format!("metadata: {{labels: {{app: {name}}}, annotations: {{rev: \"{rev}\"}}}}"),
        )
        .replace(
            &format!("terminationGracePeriodSeconds: {grace}"),
            &format!("terminationGracePeriodSeconds: {grace}\n      serviceAccountName: {sa}"),
        )
}

fn sa(ns: &str, name: &str) -> String {
    format!("apiVersion: v1\nkind: ServiceAccount\nmetadata: {{name: {name}, namespace: {ns}}}\n")
}

fn netpol(ns: &str, name: &str, app: &str) -> String {
    format!(
        "apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata: {{name: {name}, namespace: {ns}}}\nspec: {{podSelector: {{matchLabels: {{app: {app}}}}}, policyTypes: [Ingress], ingress: [{{}}]}}\n"
    )
}

fn sealed(ns: &str, value: &str) -> String {
    format!(
        "apiVersion: bitnami.com/v1alpha1\nkind: SealedSecret\nmetadata: {{name: s, namespace: {ns}}}\nspec: {{encryptedData: {{k: \"{value}\"}}}}\n"
    )
}

struct T {
    ns: String,
    r: Repo,
}

impl T {
    fn new(name: &str) -> T {
        let ns = format!("t-{name}");
        setup(&ns);
        T { r: Repo::new(name), ns }
    }
    /// Writes deploy/ with these files (and kustomization.yaml listing them; `sealed` files go to deploy/sealed/).
    fn files(&self, files: &[(&str, String)], sealed: &[(&str, String)], generator: Option<&str>) {
        let _ = std::fs::remove_dir_all(self.r.dir.join("deploy"));
        let names: Vec<&str> = files.iter().map(|(f, _)| *f).collect();
        for (f, b) in files {
            self.r.write(&format!("deploy/{f}"), b);
        }
        let mut k = format!("resources: [{}]\n", names.join(", "));
        if let Some(g) = generator {
            k.push_str(&format!(
                "configMapGenerator:\n  - {{name: gen, namespace: {}, literals: [\"k={g}\"]}}\n",
                self.ns
            ));
        }
        self.r.write("deploy/kustomization.yaml", &k);
        let snames: Vec<&str> = sealed.iter().map(|(f, _)| *f).collect();
        for (f, b) in sealed {
            self.r.write(&format!("deploy/sealed/{f}"), b);
        }
        self.r.write(
            "deploy/sealed/kustomization.yaml",
            &format!("resources: [{}]\n", snames.join(", ")),
        );
    }
    fn inv(&self) -> String {
        format!("{}/inventory", self.ns)
    }
    fn plan(&self) -> (bool, String) {
        let (repo, folder, sealed, out_, inv) = (
            self.r.path(""),
            self.r.path("deploy"),
            self.r.path("deploy/sealed"),
            format!("{}.plan.md", self.r.dir.display()),
            self.inv(),
        );
        let o = yyc(&[
            "plan",
            "--repo",
            &repo,
            "--folder",
            &folder,
            "--sealed",
            &sealed,
            "--namespaces",
            &self.ns,
            "--inventory",
            &inv,
            "--sealed-as",
            FENCE,
            "--out",
            &out_,
        ]);
        (o.status.success(), out(&o))
    }
    fn apply_args(&self, rollout: &str, sealed_secs: &str) -> Vec<String> {
        [
            "apply",
            "--plan",
            &format!("{}.plan.md", self.r.dir.display()),
            "--run",
            "test",
            "--rollout-timeout",
            rollout,
            "--sealed-timeout-secs",
            sealed_secs,
        ]
        .iter()
        .map(|s| (*s).to_string())
        .collect()
    }
    fn apply(&self, rollout: &str, sealed_secs: &str) -> (bool, String) {
        let a = self.apply_args(rollout, sealed_secs);
        let o = yyc(&a.iter().map(String::as_str).collect::<Vec<_>>());
        (o.status.success(), out(&o))
    }
    fn deploy_ok(&self, msg: &str) {
        self.r.commit(msg);
        let (ok, o) = self.plan();
        assert!(ok, "plan: {o}");
        let (ok, o) = self.apply("120s", "30");
        assert!(ok, "apply: {o}");
    }
    fn get(&self, what: &str) -> Option<Value> {
        let o = kubectl(
            &[
                "-n",
                &self.ns,
                "get",
                what,
                "-o",
                "json",
                "--ignore-not-found",
                "--show-managed-fields",
            ],
            None,
        );
        let s = String::from_utf8_lossy(&o.stdout);
        if s.trim().is_empty() {
            None
        } else {
            Some(serde_json::from_str(&s).unwrap())
        }
    }
    fn inventory(&self) -> (Vec<String>, String, String, String) {
        let c = self.get("configmap/inventory").expect("inventory");
        let entries = c["data"]["entries"]
            .as_str()
            .unwrap_or("")
            .lines()
            .map(|l| l.split(' ').next().unwrap().to_string())
            .collect();
        let a = |k: &str| c["metadata"]["annotations"][k].as_str().unwrap_or("").to_string();
        (
            entries,
            a("yyc-deploy/phase"),
            a("yyc-deploy/commit"),
            c["metadata"]["resourceVersion"].as_str().unwrap().into(),
        )
    }
    fn id(&self, name: &str, group: &str, kind: &str) -> String {
        format!("{}_{name}_{group}_{kind}", self.ns)
    }
}

#[test]
#[ignore = "needs a kind cluster"]
fn prune_gate_unnamed_removal_fails_before_any_write() {
    let t = T::new("gate");
    let ns = t.ns.clone();
    t.files(
        &[("a.yaml", cm(&ns, "a", "1")), ("b.yaml", cm(&ns, "b", "1"))],
        &[],
        Some("1"),
    );
    t.deploy_ok("c1");
    let gen1 = t
        .inventory()
        .0
        .into_iter()
        .find(|e| e.contains("_gen-"))
        .expect("generator in the inventory");

    // b dropped by accident (and a changed): refused, nothing written
    t.files(&[("a.yaml", cm(&ns, "a", "2"))], &[], Some("1"));
    t.r.commit("c2: oops");
    let (_, _, _, rv) = t.inventory();
    let (ok, o) = t.plan();
    assert!(!ok, "{o}");
    assert!(
        o.contains(&format!("Deploy-Prune: {}", t.id("b", "", "ConfigMap"))),
        "{o}"
    );
    let (ok, o) = t.apply("60s", "10");
    assert!(!ok && o.contains("blocked"), "{o}");
    assert!(t.get("configmap/b").is_some(), "b deleted");
    assert_eq!(
        t.get("configmap/a").unwrap()["data"]["k"],
        "1",
        "a changed although the run was refused"
    );
    assert_eq!(t.inventory().3, rv, "inventory written");

    let tr = yyc(&[
        "plan",
        "--repo",
        &t.r.path(""),
        "--folder",
        &t.r.path("deploy"),
        "--namespaces",
        &ns,
        "--inventory",
        &t.inv(),
        "--trailers",
    ]);
    assert!(
        out(&tr).contains(&format!("Deploy-Prune: {}", t.id("b", "", "ConfigMap"))),
        "{}",
        out(&tr)
    );

    // named by a trailer (and the generator's content changed): b and the old generator ConfigMap go
    t.files(&[("a.yaml", cm(&ns, "a", "2"))], &[], Some("2"));
    t.deploy_ok(&format!("c3\n\nDeploy-Prune: {}", t.id("b", "", "ConfigMap")));
    assert!(t.get("configmap/b").is_none(), "b not pruned");
    assert_eq!(t.get("configmap/a").unwrap()["data"]["k"], "2");
    let (entries, phase, _, _) = t.inventory();
    assert_eq!(phase, "done");
    assert!(!entries.contains(&gen1), "old generator name kept: {entries:?}");
    let old_gen_name = gen1.split('_').nth(1).unwrap();
    assert!(
        t.get(&format!("configmap/{old_gen_name}")).is_none(),
        "old generator ConfigMap not pruned"
    );
}

#[test]
#[ignore = "needs a kind cluster"]
fn killed_run_leaves_inventory_superset() {
    let t = T::new("kill");
    let ns = t.ns.clone();
    t.files(
        &[("web.yaml", deploy(&ns, true, "1")), ("old.yaml", cm(&ns, "old", "1"))],
        &[],
        None,
    );
    t.deploy_ok("c1");
    let c1 = t.inventory().2;

    t.files(
        &[("web.yaml", deploy(&ns, false, "2")), ("new.yaml", cm(&ns, "new", "1"))],
        &[],
        None,
    );
    t.r.commit(&format!("c2\n\nDeploy-Prune: {}", t.id("old", "", "ConfigMap")));
    let (ok, o) = t.plan();
    assert!(ok, "{o}");
    let mut a = t.apply_args("300s", "10");
    a.extend(["--lease-secs".into(), "8".into()]);
    let mut child = std::process::Command::new(bin()).args(&a).spawn().unwrap();
    let start = Instant::now();
    loop {
        let gen_ = t
            .get("deployment/web")
            .and_then(|d| d["metadata"]["generation"].as_i64())
            .unwrap_or(0);
        if gen_ >= 2 && t.inventory().1 == "applying" {
            break;
        }
        assert!(
            start.elapsed() < Duration::from_secs(60),
            "the run never reached the rollout wait"
        );
        std::thread::sleep(Duration::from_millis(300));
    }
    std::thread::sleep(Duration::from_secs(2));
    child.kill().unwrap(); // SIGKILL: the runner lost mid-run
    let _ = child.wait();

    let (entries, phase, commit, _) = t.inventory();
    assert_eq!(phase, "applying");
    assert_eq!(commit, c1, "commit must stay the last one applied in full");
    for n in [
        t.id("web", "apps", "Deployment"),
        t.id("old", "", "ConfigMap"),
        t.id("new", "", "ConfigMap"),
    ] {
        assert!(entries.contains(&n), "{n} missing from {entries:?}");
    }
    assert!(t.get("configmap/old").is_some(), "pruned before the rollout was ready");

    // the killed run's lease is still live: another run's plan is refused until it expires (at most --lease-secs)
    t.files(
        &[("web.yaml", deploy(&ns, true, "3")), ("new.yaml", cm(&ns, "new", "1"))],
        &[],
        None,
    );
    t.r.commit("c3: fix readiness");
    let (ok, o) = t.plan();
    assert!(!ok && o.contains("held by run test"), "{o}");
    std::thread::sleep(Duration::from_secs(9));

    // the next run (a fix) finishes the job: the trailer of c2 still counts, old is pruned
    let (ok, o) = t.plan();
    assert!(ok, "{o}");
    let (ok, o) = t.apply("120s", "30");
    assert!(ok, "{o}");
    let (entries, phase, _, _) = t.inventory();
    assert_eq!(phase, "done");
    assert!(!entries.contains(&t.id("old", "", "ConfigMap")), "{entries:?}");
    assert!(t.get("configmap/old").is_none(), "old not pruned");
}

#[test]
#[ignore = "needs a kind cluster"]
fn stale_run_is_a_green_noop() {
    let t = T::new("stale");
    let ns = t.ns.clone();
    t.files(&[("a.yaml", cm(&ns, "a", "1"))], &[], None);
    t.deploy_ok("c1");
    let c1 = t.inventory().2;
    t.files(
        &[("a.yaml", cm(&ns, "a", "2")), ("b.yaml", cm(&ns, "b", "2"))],
        &[],
        None,
    );
    t.deploy_ok("c2");
    let c2 = t.inventory().2;
    t.r.checkout(&c1); // an old run re-run after a newer one applied
    let (ok, o) = t.plan();
    assert!(ok && o.contains("nothing to do"), "{o}");
    let (ok, o) = t.apply("60s", "10");
    assert!(ok && o.contains("nothing to do"), "{o}");
    assert_eq!(t.get("configmap/a").unwrap()["data"]["k"], "2");
    assert!(t.get("configmap/b").is_some(), "the old run pruned b");
    assert_eq!(t.inventory().2, c2);
}

#[test]
#[ignore = "needs a kind cluster"]
fn sealed_secret_synced_wait() {
    let t = T::new("sealed");
    let ns = t.ns.clone();
    t.files(
        &[("web.yaml", deploy(&ns, true, "1"))],
        &[("s.yaml", sealed(&ns, "v1"))],
        None,
    );
    t.r.commit("c1");
    let (ok, o) = t.plan();
    assert!(ok, "{o}");

    // a fake controller: decrypts 3 s after the SealedSecret appears, checking no workload was applied meanwhile
    let ns2 = ns.clone();
    let ctl = std::thread::spawn(move || {
        let start = Instant::now();
        let get = |what: &str| {
            let o = kubectl(&["-n", &ns2, "get", what, "-o", "json", "--ignore-not-found"], None);
            let s = String::from_utf8_lossy(&o.stdout).to_string();
            (!s.trim().is_empty()).then(|| serde_json::from_str::<Value>(&s).unwrap())
        };
        let ss = loop {
            if let Some(s) = get("sealedsecrets.bitnami.com/s") {
                break s;
            }
            assert!(start.elapsed() < Duration::from_secs(60));
            std::thread::sleep(Duration::from_millis(200));
        };
        let early1 = get("deployment/web").is_some();
        std::thread::sleep(Duration::from_secs(3));
        let early2 = get("deployment/web").is_some();
        let g = ss["metadata"]["generation"].as_i64().unwrap();
        let p =
            format!(r#"{{"status":{{"observedGeneration":{g},"conditions":[{{"type":"Synced","status":"True"}}]}}}}"#);
        kc(&[
            "-n",
            &ns2,
            "patch",
            "sealedsecrets.bitnami.com",
            "s",
            "--subresource=status",
            "--type=merge",
            "-p",
            &p,
        ]);
        early1 || early2
    });
    let (ok, o) = t.apply("120s", "60");
    assert!(ok, "{o}");
    assert!(
        !ctl.join().unwrap(),
        "a workload was applied before its SealedSecret was Synced"
    );
    assert!(t.get("deployment/web").is_some());

    // resealed, never decrypted: red, and the workload change is not applied
    t.files(
        &[("web.yaml", deploy(&ns, true, "2"))],
        &[("s.yaml", sealed(&ns, "v2"))],
        None,
    );
    t.r.commit("c2");
    let (ok, o) = t.plan();
    assert!(ok, "{o}");
    let (ok, o) = t.apply("60s", "6");
    assert!(!ok && o.contains("not Synced"), "{o}");
    assert_eq!(
        t.get("deployment/web").unwrap()["spec"]["template"]["metadata"]["annotations"]["rev"],
        "1"
    );
}

#[test]
#[ignore = "needs a kind cluster"]
fn never_ready_rollout_turns_the_run_red_with_the_old_pod_serving() {
    let t = T::new("ready");
    let ns = t.ns.clone();
    t.files(
        &[("web.yaml", deploy(&ns, true, "1")), ("x.yaml", cm(&ns, "x", "1"))],
        &[],
        None,
    );
    t.deploy_ok("c1");
    t.files(&[("web.yaml", deploy(&ns, false, "2"))], &[], None);
    t.r.commit(&format!("c2\n\nDeploy-Prune: {}", t.id("x", "", "ConfigMap")));
    let (ok, o) = t.plan();
    assert!(ok, "{o}");
    let (ok, o) = t.apply("25s", "10");
    assert!(!ok && o.contains("rollout not ready"), "{o}");
    let d = t.get("deployment/web").unwrap();
    assert_eq!(
        d["status"]["availableReplicas"], 1,
        "the old Pod must keep serving: {}",
        d["status"]
    );
    let pods = t.get("pods").unwrap();
    let ready_old = pods["items"].as_array().unwrap().iter().any(|p| {
        p["metadata"]["annotations"]["rev"] == "1"
            && p["status"]["conditions"]
                .as_array()
                .is_some_and(|c| c.iter().any(|c| c["type"] == "Ready" && c["status"] == "True"))
    });
    assert!(ready_old, "no ready old Pod");
    assert!(t.get("configmap/x").is_some(), "pruned although the rollout failed");
    assert_eq!(t.inventory().1, "applying");
}

/// A stand-in for the Sealed Secrets controller: marks every SealedSecret of `ns` Synced at its generation, until
/// the returned flag is set.
fn fake_sealed_controller(
    ns: &str,
) -> (
    std::sync::Arc<std::sync::atomic::AtomicBool>,
    std::thread::JoinHandle<()>,
) {
    use std::sync::atomic::Ordering;
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let (s2, ns) = (stop.clone(), ns.to_string());
    let h = std::thread::spawn(move || {
        while !s2.load(Ordering::Relaxed) {
            let o = kubectl(&["-n", &ns, "get", "sealedsecrets.bitnami.com", "-o", "json"], None);
            if let Ok(l) = serde_json::from_slice::<Value>(&o.stdout) {
                for x in l["items"].as_array().cloned().unwrap_or_default() {
                    let g = x["metadata"]["generation"].as_i64().unwrap_or(0);
                    if x["status"]["observedGeneration"].as_i64() != Some(g) {
                        let p = format!(
                            r#"{{"status":{{"observedGeneration":{g},"conditions":[{{"type":"Synced","status":"True"}}]}}}}"#
                        );
                        let _ = kubectl(
                            &[
                                "-n",
                                &ns,
                                "patch",
                                "sealedsecrets.bitnami.com",
                                x["metadata"]["name"].as_str().unwrap(),
                                "--subresource=status",
                                "--type=merge",
                                "-p",
                                &p,
                            ],
                            None,
                        );
                    }
                }
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    });
    (stop, h)
}

#[test]
#[ignore = "needs a kind cluster"]
fn bad_trailer_on_main_does_not_block_later_runs() {
    let t = T::new("trailer");
    let ns = t.ns.clone();
    t.files(
        &[("web.yaml", deploy(&ns, true, "1")), ("a.yaml", cm(&ns, "a", "1"))],
        &[],
        None,
    );
    t.deploy_ok("c1");
    // a typo, an unknown kind and a refused Deploy-Replace reach main (check item 11 turned that push red)
    t.files(
        &[("web.yaml", deploy(&ns, true, "2")), ("a.yaml", cm(&ns, "a", "2"))],
        &[],
        None,
    );
    let web = t.id("web", "apps", "Deployment");
    t.r.commit(&format!(
        "c2\n\nDeploy-Prnue: {}\nDeploy-Prune: {ns}_x_example.com_Widget\nDeploy-Replace: {web}",
        t.id("a", "", "ConfigMap")
    ));
    let (ok, o) = t.plan();
    assert!(ok, "a bad trailer blocked the plan:\n{o}");
    assert!(o.contains("trailer ignored") && o.contains("Deploy-Prnue"), "{o}");
    assert!(o.contains(&format!("`Deploy-Replace: {web}` refused")), "{o}");
    let (ok, o) = t.apply("120s", "10");
    assert!(ok, "{o}");
    // the refused Deploy-Replace did not delete the serving Deployment: it rolled as an update
    let uid1 = t.get("deployment/web").unwrap()["metadata"]["uid"].clone();
    // and the next, good commit applies normally with the inventory past the bad one
    t.files(
        &[("web.yaml", deploy(&ns, true, "3")), ("a.yaml", cm(&ns, "a", "3"))],
        &[],
        None,
    );
    let c3 = t.r.commit("c3");
    let (ok, o) = t.plan();
    assert!(ok, "{o}");
    assert!(!o.contains("Deploy-Prnue"), "the bad commit is still in range:\n{o}");
    let (ok, o) = t.apply("120s", "10");
    assert!(ok, "{o}");
    assert_eq!(t.inventory().2, c3);
    assert_eq!(t.get("deployment/web").unwrap()["metadata"]["uid"], uid1);
    assert_eq!(t.get("configmap/a").unwrap()["data"]["k"], "3");
}

#[test]
#[ignore = "needs a kind cluster"]
fn pruned_workloads_pods_outlive_their_network_policy_and_config() {
    let t = T::new("drain");
    let ns = t.ns.clone();
    t.files(
        &[
            ("old.yaml", slow_drain(&ns, "old", 15)),
            ("np.yaml", netpol(&ns, "old-allow", "old")),
            ("cfg.yaml", cm(&ns, "old-cfg", "1")),
            ("keep.yaml", cm(&ns, "keep", "1")),
        ],
        &[],
        None,
    );
    t.deploy_ok("c1");
    t.files(&[("keep.yaml", cm(&ns, "keep", "1"))], &[], None);
    t.r.commit(&format!(
        "c2: retire old\n\nDeploy-Prune: {}, {}, {}",
        t.id("old", "apps", "Deployment"),
        t.id("old-allow", "networking.k8s.io", "NetworkPolicy"),
        t.id("old-cfg", "", "ConfigMap")
    ));
    let (ok, o) = t.plan();
    assert!(ok, "{o}");
    let a = t.apply_args("120s", "10");
    let mut child = std::process::Command::new(bin())
        .args(&a)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    // watch: once the Deployment is gone its Pod is Terminating for ~15 s; the policy and config must outlive it
    let mut saw_terminating = false;
    let start = Instant::now();
    while child.try_wait().unwrap().is_none() {
        // policy and config first: a Pod seen after them existed when they were read
        let np = t.get("networkpolicy/old-allow").is_some();
        let cfg = t.get("configmap/old-cfg").is_some();
        let pods = t.get("pods").unwrap_or_default();
        let old_pods = pods["items"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .iter()
            .filter(|p| p["metadata"]["labels"]["app"] == "old")
            .count();
        if old_pods > 0 && t.get("deployment/old").is_none() {
            saw_terminating = true;
        }
        assert!(
            old_pods == 0 || (np && cfg),
            "the NetworkPolicy or ConfigMap went while a pruned Pod was still draining"
        );
        assert!(start.elapsed() < Duration::from_secs(150), "apply hung");
        std::thread::sleep(Duration::from_millis(300));
    }
    let o = child.wait_with_output().unwrap();
    assert!(o.status.success(), "{}", out(&o));
    assert!(
        saw_terminating,
        "never saw the old Pod draining: the test proves nothing"
    );
    assert!(t.get("networkpolicy/old-allow").is_none() && t.get("configmap/old-cfg").is_none());
    assert!(
        start.elapsed() >= Duration::from_secs(10),
        "the run did not wait for the 15 s drain"
    );
}

#[test]
#[ignore = "needs a kind cluster"]
fn updated_workloads_old_pods_outlive_their_renamed_policy_and_service_account() {
    let t = T::new("drain2");
    let ns = t.ns.clone();
    t.files(
        &[
            ("app.yaml", slow_drain_rev(&ns, "app", 15, "1", "app-v1")),
            ("sa.yaml", sa(&ns, "app-v1")),
            ("np.yaml", netpol(&ns, "app-allow-v1", "app")),
        ],
        &[],
        None,
    );
    t.deploy_ok("c1");
    // a new revision with a renamed policy and ServiceAccount: the old Pod drains for 15 s after the rollout is done
    t.files(
        &[
            ("app.yaml", slow_drain_rev(&ns, "app", 15, "2", "app-v2")),
            ("sa.yaml", sa(&ns, "app-v2")),
            ("np.yaml", netpol(&ns, "app-allow-v2", "app")),
        ],
        &[],
        None,
    );
    t.r.commit(&format!(
        "c2: rename\n\nDeploy-Prune: {}, {}",
        t.id("app-allow-v1", "networking.k8s.io", "NetworkPolicy"),
        t.id("app-v1", "", "ServiceAccount")
    ));
    let (ok, o) = t.plan();
    assert!(ok, "{o}");
    let a = t.apply_args("120s", "10");
    let mut child = std::process::Command::new(bin())
        .args(&a)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let mut saw_draining = false;
    let start = Instant::now();
    while child.try_wait().unwrap().is_none() {
        let np = t.get("networkpolicy/app-allow-v1").is_some();
        let sa1 = t.get("serviceaccount/app-v1").is_some();
        let items = t.get("pods").unwrap_or_default()["items"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let old: Vec<&Value> = items
            .iter()
            .filter(|p| p["metadata"]["annotations"]["rev"] == "1")
            .collect();
        let new_ready = items.iter().any(|p| {
            p["metadata"]["annotations"]["rev"] == "2"
                && p["status"]["conditions"]
                    .as_array()
                    .is_some_and(|c| c.iter().any(|c| c["type"] == "Ready" && c["status"] == "True"))
        });
        if new_ready && old.iter().any(|p| !p["metadata"]["deletionTimestamp"].is_null()) {
            saw_draining = true;
        }
        assert!(
            old.is_empty() || (np && sa1),
            "the old revision's NetworkPolicy or ServiceAccount went while its Pod was still draining"
        );
        assert!(start.elapsed() < Duration::from_secs(150), "apply hung");
        std::thread::sleep(Duration::from_millis(300));
    }
    let o = child.wait_with_output().unwrap();
    assert!(o.status.success(), "{}", out(&o));
    assert!(saw_draining, "never saw the old Pod draining: the test proves nothing");
    assert!(t.get("networkpolicy/app-allow-v1").is_none() && t.get("serviceaccount/app-v1").is_none());
    assert!(
        start.elapsed() >= Duration::from_secs(10),
        "the run did not wait for the 15 s drain"
    );
}

#[test]
#[ignore = "needs a kind cluster"]
fn a_second_applier_is_refused_while_a_run_holds_the_inventory() {
    let t = T::new("race");
    let ns = t.ns.clone();
    t.files(
        &[("web.yaml", deploy(&ns, true, "1")), ("a.yaml", cm(&ns, "a", "1"))],
        &[],
        None,
    );
    t.deploy_ok("c1");
    // run A: a rollout that never gets ready keeps it applying for 20 s, its 6 s lease renewed meanwhile
    t.files(
        &[("web.yaml", deploy(&ns, false, "2")), ("a.yaml", cm(&ns, "a", "2"))],
        &[],
        None,
    );
    t.r.commit("c2");
    let (ok, o) = t.plan();
    assert!(ok, "{o}");
    let mut a = t.apply_args("20s", "10");
    a.extend(["--lease-secs".into(), "6".into()]);
    let mut child = std::process::Command::new(bin())
        .args(&a)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let start = Instant::now();
    while t.inventory().1 != "applying" {
        assert!(
            start.elapsed() < Duration::from_secs(30),
            "run A never started applying"
        );
        std::thread::sleep(Duration::from_millis(200));
    }
    // run B (a session, another commit), planned after A wrote `applying` and past A's first lease period: refused
    std::thread::sleep(Duration::from_secs(8));
    t.files(
        &[("web.yaml", deploy(&ns, true, "3")), ("a.yaml", cm(&ns, "a", "3"))],
        &[],
        None,
    );
    t.r.commit("c3");
    let (ok, o) = t.plan();
    assert!(
        !ok && o.contains("held by run test"),
        "B planned beside a running A:\n{o}"
    );
    assert!(child.try_wait().unwrap().is_none(), "A ended early");
    let o = child.wait_with_output().unwrap();
    assert!(
        !o.status.success(),
        "A should fail on its never-ready rollout: {}",
        out(&o)
    );
    // A failed and cleared its lease: B goes ahead at once
    let (_, phase, _, _) = t.inventory();
    assert_eq!(phase, "applying");
    let (ok, o) = t.plan();
    assert!(ok, "{o}");
    let (ok, o) = t.apply("120s", "10");
    assert!(ok, "{o}");
    assert_eq!(t.inventory().1, "done");
    assert_eq!(t.get("configmap/a").unwrap()["data"]["k"], "3");
}

// ---- the deployer's real rights: clusters/talos/yyc-ai-access.yaml applied, the applier run as gha:yyc-ai ----

const RBAC_NS: &[(&str, &str)] = &[
    ("yyc-ai", "t-r-ai"),
    ("yyc-ai-fabric", "t-r-fab"),
    ("yyc", "t-r-yyc"),
    ("yyc-deploy", "t-r-deploy"),
    ("db", "t-r-db"),
];

fn map_ns(n: &str) -> String {
    RBAC_NS
        .iter()
        .find(|(a, _)| *a == n)
        .map_or_else(|| n.to_string(), |(_, b)| (*b).to_string())
}

/// The RBAC objects of an access file, with YYC's namespaces mapped to the test's (Pod Security labels dropped).
fn access_objects(file: &str) -> Vec<Value> {
    let path = format!(
        "{}/../../news-stream/deploy/clusters/talos/{file}",
        env!("CARGO_MANIFEST_DIR")
    );
    let text = std::fs::read_to_string(&path).unwrap();
    let mut out = Vec::new();
    for doc in serde_yaml::Deserializer::from_str(&text) {
        use serde::Deserialize;
        let y = serde_yaml::Value::deserialize(doc).unwrap();
        let mut v: Value = serde_json::to_value(y).unwrap();
        let kind = v["kind"].as_str().unwrap_or("").to_string();
        if !matches!(
            kind.as_str(),
            "Namespace" | "ServiceAccount" | "ClusterRole" | "ClusterRoleBinding" | "Role" | "RoleBinding"
        ) {
            continue;
        }
        if kind == "Namespace" {
            let n = map_ns(v["metadata"]["name"].as_str().unwrap());
            v["metadata"] = serde_json::json!({"name": n});
        }
        if let Some(n) = v["metadata"]["namespace"].as_str() {
            v["metadata"]["namespace"] = Value::String(map_ns(n));
        }
        if let Some(subs) = v.get_mut("subjects").and_then(Value::as_array_mut) {
            for s in subs {
                if let Some(n) = s["namespace"].as_str() {
                    s["namespace"] = Value::String(map_ns(n));
                }
            }
        }
        out.push(v);
    }
    out
}

/// A kubeconfig for user `gha:yyc-ai` (a client certificate from the cluster's own signer), beside `dir`.
fn gha_kubeconfig(dir: &std::path::Path) -> String {
    let key = dir.join("gha.key");
    let csr = dir.join("gha.csr");
    let ok = |o: std::process::Output| assert!(o.status.success(), "{}", out(&o));
    ok(run("openssl", &["genrsa", "-out", key.to_str().unwrap(), "2048"], None));
    ok(run(
        "openssl",
        &[
            "req",
            "-new",
            "-key",
            key.to_str().unwrap(),
            "-subj",
            "/CN=gha:yyc-ai",
            "-out",
            csr.to_str().unwrap(),
        ],
        None,
    ));
    let b64 =
        |p: &std::path::Path| String::from_utf8(run("base64", &["-w0", p.to_str().unwrap()], None).stdout).unwrap();
    let _ = kubectl(&["delete", "csr", "t-gha", "--ignore-not-found"], None);
    let req = serde_json::json!({
        "apiVersion": "certificates.k8s.io/v1", "kind": "CertificateSigningRequest",
        "metadata": {"name": "t-gha"},
        "spec": {"request": b64(&csr), "signerName": "kubernetes.io/kube-apiserver-client",
                 "usages": ["client auth"], "expirationSeconds": 3600}
    });
    kc_stdin(&["create", "-f", "-"], &req.to_string());
    kc(&["certificate", "approve", "t-gha"]);
    let start = Instant::now();
    let cert = loop {
        let c = kc(&["get", "csr", "t-gha", "-o", "jsonpath={.status.certificate}"]);
        if !c.is_empty() {
            break c;
        }
        assert!(start.elapsed() < Duration::from_secs(60), "the CSR was never signed");
        std::thread::sleep(Duration::from_millis(500));
    };
    let mut cfg: Value = serde_json::from_str(&kc(&["config", "view", "--raw", "--minify", "-o", "json"])).unwrap();
    cfg["users"][0]["user"] = serde_json::json!({"client-certificate-data": cert, "client-key-data": b64(&key)});
    let path = dir.join("gha.kubeconfig");
    std::fs::write(&path, cfg.to_string()).unwrap();
    path.display().to_string()
}

#[test]
#[ignore = "needs a kind cluster"]
fn the_deployers_own_rights_suffice_and_hold() {
    // namespaces, the access files (as their Kustomization applies them), the CRDs
    setup("t-r-ai");
    for (_, n) in RBAC_NS.iter().skip(1) {
        let _ = kubectl(
            &[
                "delete",
                "namespace",
                n,
                "--ignore-not-found",
                "--wait=true",
                "--timeout=120s",
            ],
            None,
        );
        kc(&["create", "namespace", n]);
    }
    let _ = kubectl(
        &[
            "delete",
            "persistentvolume",
            "hermit-jfs",
            "--ignore-not-found",
            "--wait=true",
        ],
        None,
    );
    let mut objs = access_objects("yyc-ai-access.yaml");
    objs.extend(access_objects("sealed-secrets.yaml"));
    kc_stdin(
        &[
            "apply",
            "--server-side",
            "--force-conflicts",
            "--field-manager=test-setup",
            "-f",
            "-",
        ],
        &serde_json::json!({"apiVersion": "v1", "kind": "List", "items": objs}).to_string(),
    );
    let r = Repo::new("rbac");
    let gha = gha_kubeconfig(&r.dir);
    assert_eq!(
        kc(&[
            "--kubeconfig",
            &gha,
            "auth",
            "whoami",
            "-o",
            "jsonpath={.status.userInfo.username}"
        ]),
        "gha:yyc-ai"
    );
    assert_eq!(
        String::from_utf8_lossy(
            &kubectl(
                &["--kubeconfig", &gha, "-n", "t-r-ai", "auth", "can-i", "get", "secrets"],
                None
            )
            .stdout
        )
        .trim(),
        "no"
    );
    // nothing in the apps' namespace (the ConfigMap yyc-catalogue it wrote there is gone, 2026-11-16)
    for verb in ["create", "get", "patch", "delete"] {
        assert_eq!(
            String::from_utf8_lossy(
                &kubectl(
                    &[
                        "--kubeconfig",
                        &gha,
                        "-n",
                        "t-r-yyc",
                        "auth",
                        "can-i",
                        verb,
                        "configmaps/yyc-catalogue"
                    ],
                    None
                )
                .stdout
            )
            .trim(),
            "no",
            "{verb} configmaps in t-r-yyc"
        );
    }

    let ai = "t-r-ai";
    let fence = "system:serviceaccount:t-r-deploy:sealed-secrets-applier";
    let pv = "apiVersion: v1\nkind: PersistentVolume\nmetadata: {name: hermit-jfs}\nspec: {capacity: {storage: 1Gi}, accessModes: [ReadWriteMany], persistentVolumeReclaimPolicy: Retain, hostPath: {path: /tmp/t-hermit}}\n";
    let role = format!(
        "apiVersion: rbac.authorization.k8s.io/v1\nkind: Role\nmetadata: {{name: reader, namespace: {ai}}}\nrules: [{{apiGroups: [\"\"], resources: [configmaps], verbs: [get, list]}}]\n---\napiVersion: v1\nkind: ServiceAccount\nmetadata: {{name: app, namespace: {ai}}}\n---\napiVersion: rbac.authorization.k8s.io/v1\nkind: RoleBinding\nmetadata: {{name: reader, namespace: {ai}}}\nroleRef: {{apiGroup: rbac.authorization.k8s.io, kind: Role, name: reader}}\nsubjects: [{{kind: ServiceAccount, name: app, namespace: {ai}}}]\n"
    );
    // the Role the render creates (spec-platform 9.11 hermit-read): the API server's escalation check passes only if
    // the deployer holds every right it grants
    let spec_roles = format!(
        "apiVersion: rbac.authorization.k8s.io/v1\nkind: Role\nmetadata: {{name: hermit-read, namespace: {ai}}}\nrules:\n  - {{apiGroups: [apps], resources: [deployments], verbs: [get, list]}}\n  - {{apiGroups: [\"\"], resources: [pods], verbs: [get, list]}}\n  - {{apiGroups: [discovery.k8s.io], resources: [endpointslices], verbs: [get, list, watch]}}\n"
    );
    let write = |extra: &[(&str, String)], sealed_files: &[(&str, String)]| {
        let _ = std::fs::remove_dir_all(r.dir.join("deploy"));
        let mut files: Vec<(&str, String)> = vec![
            ("web.yaml", deploy(ai, true, "1")),
            ("c.yaml", cm(ai, "c", "1")),
            ("pv.yaml", pv.to_string()),
            ("spec-roles.yaml", spec_roles.clone()),
        ];
        files.extend(extra.iter().cloned());
        for (f, b) in &files {
            r.write(&format!("deploy/{f}"), b);
        }
        let names: Vec<&str> = files.iter().map(|(f, _)| *f).collect();
        r.write(
            "deploy/kustomization.yaml",
            &format!("resources: [{}]\n", names.join(", ")),
        );
        for (f, b) in sealed_files {
            r.write(&format!("deploy/sealed/{f}"), b);
        }
        let sn: Vec<&str> = sealed_files.iter().map(|(f, _)| *f).collect();
        r.write(
            "deploy/sealed/kustomization.yaml",
            &format!("resources: [{}]\n", sn.join(", ")),
        );
    };
    let (repo, folder, sealed_dir, planf) = (
        r.path(""),
        r.path("deploy"),
        r.path("deploy/sealed"),
        format!("{}.plan.md", r.dir.display()),
    );
    let plan = || {
        let o = yyc_as(
            &gha,
            &[
                "plan",
                "--repo",
                &repo,
                "--folder",
                &folder,
                "--sealed",
                &sealed_dir,
                "--namespaces",
                "t-r-ai,t-r-fab",
                "--inventory",
                "t-r-deploy/inventory-yyc-ai",
                "--sealed-as",
                fence,
                "--out",
                &planf,
            ],
        );
        (o.status.success(), out(&o))
    };
    let apply = || {
        let o = yyc_as(
            &gha,
            &[
                "apply",
                "--plan",
                &planf,
                "--run",
                "test",
                "--rollout-timeout",
                "120s",
                "--sealed-timeout-secs",
                "60",
            ],
        );
        (o.status.success(), out(&o))
    };
    let (stop, ctl) = fake_sealed_controller(ai);

    // c1: everything kind of object the deployer applies, as gha:yyc-ai (the SealedSecret as the fence, impersonated)
    write(&[("role.yaml", role.clone())], &[("s.yaml", sealed(ai, "v1"))]);
    r.commit("c1");
    let (ok, o) = plan();
    assert!(ok, "plan as gha:yyc-ai: {o}");
    let (ok, o) = apply();
    assert!(ok, "apply as gha:yyc-ai: {o}");
    for (ns, what) in [
        (ai, "deployment/web"),
        (ai, "role/reader"),
        (ai, "rolebinding/reader"),
        (ai, "role/hermit-read"),
        (ai, "sealedsecrets.bitnami.com/s"),
    ] {
        assert!(!kc(&["-n", ns, "get", what, "-o", "name"]).is_empty(), "{ns} {what}");
    }
    // c2: a Role escalating to Secrets is refused by the API server's escalation check, before any write
    let sneaky = format!(
        "apiVersion: rbac.authorization.k8s.io/v1\nkind: Role\nmetadata: {{name: sneaky, namespace: {ai}}}\nrules: [{{apiGroups: [\"\"], resources: [secrets], verbs: [get]}}]\n"
    );
    write(
        &[("role.yaml", role.clone()), ("sneaky.yaml", sneaky)],
        &[("s.yaml", sealed(ai, "v1"))],
    );
    r.commit("c2");
    let (ok, o) = plan();
    assert!(
        !ok && o.contains("sneaky") && (o.contains("not currently held") || o.contains("escalat")),
        "{o}"
    );

    // c3: a SealedSecret for db (outside the folder's namespaces) is refused by the plan
    write(
        &[("role.yaml", role.clone())],
        &[("s.yaml", sealed(ai, "v1")), ("db.yaml", sealed("t-r-db", "pw"))],
    );
    r.commit("c3");
    let (ok, o) = plan();
    assert!(
        !ok && o.contains("t-r-db_s_bitnami.com_SealedSecret: a sealed object outside"),
        "{o}"
    );

    // c4: prune as gha:yyc-ai (the Role, its binding, a ConfigMap; the drain wait lists Pods and ReplicaSets)
    write(&[], &[("s.yaml", sealed(ai, "v2"))]);
    r.commit(&format!(
        "c4\n\nDeploy-Prune: {ai}_reader_rbac.authorization.k8s.io_Role, {ai}_reader_rbac.authorization.k8s.io_RoleBinding, {ai}_app__ServiceAccount"
    ));
    let (ok, o) = plan();
    assert!(ok, "{o}");
    let (ok, o) = apply();
    assert!(ok, "{o}");
    assert!(kubectl(&["-n", ai, "get", "role/reader"], None).status.code() != Some(0));
    stop.store(true, std::sync::atomic::Ordering::Relaxed);
    ctl.join().unwrap();
    let _ = kubectl(
        &["delete", "persistentvolume", "hermit-jfs", "--ignore-not-found"],
        None,
    );
}
