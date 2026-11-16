//! `check` items 9-12 (deploy-workflows 6.6) pass on a clean folder and fail on each planted violation. No cluster;
//! needs `kustomize` on PATH.
#![allow(clippy::unwrap_used)]

mod common;
use common::*;

const ACCESS: &str = r#"
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: deployer-ns}
rules:
  - apiGroups: [""]
    resources: [configmaps]
    verbs: [create, get, list, watch, patch, update, delete]
  - apiGroups: [apps]
    resources: [deployments]
    verbs: [create, get, list, watch, patch, update, delete]
  - apiGroups: [rbac.authorization.k8s.io]
    resources: [roles, rolebindings]
    verbs: [create, get, patch, delete]
  - apiGroups: [""]
    resources: [pods]
    verbs: [get, list]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: deployer, namespace: app}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: deployer-ns}
subjects: [{apiGroup: rbac.authorization.k8s.io, kind: User, name: "gha:test"}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: imp, namespace: fence}
rules: [{apiGroups: [""], resources: [serviceaccounts], resourceNames: [applier], verbs: [impersonate]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: imp, namespace: fence}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: Role, name: imp}
subjects: [{apiGroup: rbac.authorization.k8s.io, kind: User, name: "gha:test"}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: sealed-applier}
rules: [{apiGroups: [bitnami.com], resources: [sealedsecrets], verbs: [get, list, watch, create, update, patch, delete]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: sealed-applier, namespace: app}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: sealed-applier}
subjects: [{kind: ServiceAccount, name: applier, namespace: fence}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: named, namespace: named}
rules:
  - {apiGroups: [""], resources: [configmaps], resourceNames: [only], verbs: [get, patch, delete]}
  - {apiGroups: [""], resources: [configmaps], resourceNames: [kept], verbs: [get, patch]}
  - {apiGroups: [""], resources: [configmaps], verbs: [create]}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: named, namespace: nocreate}
rules: [{apiGroups: [""], resources: [configmaps], resourceNames: [only], verbs: [get, patch, delete]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: named, namespace: named}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: Role, name: named}
subjects: [{apiGroup: rbac.authorization.k8s.io, kind: User, name: "gha:test"}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: named, namespace: nocreate}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: Role, name: named}
subjects: [{apiGroup: rbac.authorization.k8s.io, kind: User, name: "gha:test"}]
"#;

const CM: &str = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: c, namespace: app}\ndata: {k: v}\n";
const ROLE: &str = "apiVersion: rbac.authorization.k8s.io/v1\nkind: Role\nmetadata: {name: reader, namespace: app}\nrules: [{apiGroups: [\"\"], resources: [pods], verbs: [get, list]}]\n";
const SS: &str = "apiVersion: bitnami.com/v1alpha1\nkind: SealedSecret\nmetadata: {name: s1, namespace: app}\nspec: {encryptedData: {k: AgB}}\n";

/// A repo with deploy/ (resources `extra` added to the clean set) and deploy/sealed/.
fn repo(name: &str, extra: &[(&str, &str)], sealed_extra: &[(&str, &str)]) -> Repo {
    let r = Repo::new(name);
    r.write("access.yaml", ACCESS);
    r.write(
        "secrets.txt",
        "# map\napp/s1  Opaque  deploy/sealed/s1.yaml  k=STORE_KEY\n",
    );
    let mut res = vec!["c.yaml".to_string(), "role.yaml".to_string()];
    r.write("deploy/c.yaml", CM);
    r.write("deploy/role.yaml", ROLE);
    for (f, body) in extra {
        r.write(&format!("deploy/{f}"), body);
        res.push((*f).to_string());
    }
    r.write(
        "deploy/kustomization.yaml",
        &format!("resources: [{}]\n", res.join(", ")),
    );
    let mut sres = vec!["s1.yaml".to_string()];
    r.write("deploy/sealed/s1.yaml", SS);
    for (f, body) in sealed_extra {
        r.write(&format!("deploy/sealed/{f}"), body);
        sres.push((*f).to_string());
    }
    r.write(
        "deploy/sealed/kustomization.yaml",
        &format!("resources: [{}]\n", sres.join(", ")),
    );
    r.commit("base");
    r
}

fn check(r: &Repo, commits: Option<&str>) -> (bool, String) {
    check_ns(r, commits, "app")
}

fn check_ns(r: &Repo, commits: Option<&str>, namespaces: &str) -> (bool, String) {
    check_with(r, commits, namespaces, &[])
}

fn check_with(r: &Repo, commits: Option<&str>, namespaces: &str, extra: &[&str]) -> (bool, String) {
    let (folder, sealed, access, secrets, repo) = (
        r.path("deploy"),
        r.path("deploy/sealed"),
        r.path("access.yaml"),
        r.path("secrets.txt"),
        r.path(""),
    );
    let mut a = vec![
        "check",
        "--repo",
        &repo,
        "--folder",
        &folder,
        "--sealed",
        &sealed,
        "--access",
        &access,
        "--secrets",
        &secrets,
        "--namespaces",
        namespaces,
        "--deployer",
        "gha:test",
        "--sealed-as",
        "system:serviceaccount:fence:applier",
    ];
    if let Some(c) = commits {
        a.extend(["--commits", c]);
    }
    a.extend_from_slice(extra);
    let o = yyc(&a);
    (o.status.success(), out(&o))
}

fn fails_with(r: &Repo, item: &str, what: &str) {
    let (ok, o) = check(r, None);
    assert!(!ok, "expected a failure of item {item}:\n{o}");
    assert!(
        o.lines()
            .any(|l| l.starts_with(&format!("FAIL {item} ")) && l.contains(what)),
        "item {item} `{what}`:\n{o}"
    );
}

#[test]
fn clean_folder_passes() {
    let r = repo("clean", &[], &[]);
    let (ok, o) = check(&r, Some("HEAD"));
    assert!(ok, "{o}");
}

#[test]
fn item9_kind_outside_the_deployers_rights() {
    let svc = "apiVersion: v1\nkind: Service\nmetadata: {name: s, namespace: app}\nspec: {ports: [{port: 80}]}\n";
    fails_with(
        &repo("i9-svc", &[("svc.yaml", svc)], &[]),
        "9",
        "app_s__Service: outside the rights",
    );
}

#[test]
fn item9_object_in_another_namespace() {
    let cm = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: c, namespace: other}\n";
    fails_with(&repo("i9-ns", &[("o.yaml", cm)], &[]), "9", "other_c__ConfigMap");
}

#[test]
fn item9_role_escalating_beyond_the_deployer() {
    let role = "apiVersion: rbac.authorization.k8s.io/v1\nkind: Role\nmetadata: {name: sneaky, namespace: app}\nrules: [{apiGroups: [\"\"], resources: [secrets], verbs: [get]}]\n";
    fails_with(&repo("i9-esc", &[("r.yaml", role)], &[]), "9", "grants get /secrets");
}

#[test]
fn item10_sealed_folder_with_another_kind() {
    fails_with(
        &repo("i10-kind", &[], &[("c.yaml", &CM.replace("name: c", "name: sneaky"))]),
        "10",
        "only kind SealedSecret",
    );
}

#[test]
fn item10_sealed_secret_missing_from_the_map() {
    fails_with(
        &repo("i10-map", &[], &[("s2.yaml", &SS.replace("s1", "s2"))]),
        "10",
        "no line `app/s2`",
    );
}

#[test]
fn item10_map_lines_of_two_clusters() {
    // the same Secret name on both clusters: the old cluster's line writes elsewhere, the `@talos` line into this
    // folder; the name with its suffix dropped is the SealedSecret's
    let r = repo("i10-talos", &[], &[]);
    r.write(
        "secrets.txt",
        "app/s1  Opaque  old/sealed/s1.yaml  k=K\napp/s1@talos  Opaque  deploy/sealed/s1.yaml  k=K\n",
    );
    r.commit("both clusters");
    let (ok, o) = check(&r, None);
    assert!(ok, "{o}");
    // only the other cluster's line: refused
    r.write("secrets.txt", "app/s1  Opaque  old/sealed/s1.yaml  k=K\n");
    r.commit("old only");
    fails_with(&r, "10", "writes old/sealed/s1.yaml (another cluster's)");
}

#[test]
fn item10_sealed_secret_where_the_fence_is_not_bound() {
    // `nofence` is one of the folder's namespaces, but the fence has no binding there
    let ss = SS.replace("namespace: app", "namespace: nofence");
    let r = repo("i10-fence", &[], &[("s3.yaml", &ss)]);
    r.write("secrets.txt", "app/s1 Opaque x k=K\nnofence/s1 Opaque x k=K\n");
    r.commit("map");
    let (ok, o) = check_ns(&r, None, "app,nofence");
    assert!(
        !ok && o
            .lines()
            .any(|l| l.starts_with("FAIL 9 ") && l.contains("may not apply it as")),
        "{o}"
    );
}

#[test]
fn item9_sealed_secret_outside_home_although_the_fence_is_bound_there() {
    // the fence also writes db's SealedSecrets for another folder: that binding
    // must not let this folder write them
    let mut access = ACCESS.to_string();
    access.push_str("---\napiVersion: rbac.authorization.k8s.io/v1\nkind: RoleBinding\nmetadata: {name: sealed-applier, namespace: db}\nroleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: sealed-applier}\nsubjects: [{kind: ServiceAccount, name: applier, namespace: fence}]\n");
    let ss = SS.replace("namespace: app", "namespace: db").replace("s1", "pg");
    let r = repo("i9-db", &[], &[("pg.yaml", &ss)]);
    r.write("access.yaml", &access);
    r.write("secrets.txt", "app/s1 Opaque x k=K\ndb/pg Opaque x k=K\n");
    r.commit("db");
    fails_with(&r, "9", "db_pg_bitnami.com_SealedSecret: a SealedSecret outside app");
}

#[test]
fn item11_malformed_and_refused_trailers() {
    let r = repo("i11", &[], &[]);
    let base = r.commit("nothing");
    r.commit("drop it\n\nDeploy-Prune: app/c\nDeploy-Replace: app_web_apps_Deployment");
    let (ok, o) = check(&r, Some(&format!("{base}..HEAD")));
    assert!(!ok, "{o}");
    assert!(o.contains("FAIL 11 ") && o.contains("`Deploy-Prune`"), "{o}");
    assert!(o.contains("Deploy-Replace: app_web_apps_Deployment` refused"), "{o}");
    let (ok, o) = check(&r, Some(&format!("{base}~1..{base}")));
    assert!(ok, "the earlier commits are clean:\n{o}");
}

#[test]
fn item12_namespace_object() {
    let ns = "apiVersion: v1\nkind: Namespace\nmetadata: {name: app}\n";
    fails_with(
        &repo("i12-ns", &[("ns.yaml", ns)], &[]),
        "12",
        "namespaces are cluster-level",
    );
}

#[test]
fn item12_cluster_rbac_and_rbac_elsewhere() {
    let cr = "apiVersion: rbac.authorization.k8s.io/v1\nkind: ClusterRole\nmetadata: {name: wide}\nrules: []\n";
    fails_with(
        &repo("i12-cr", &[("cr.yaml", cr)], &[]),
        "12",
        "_wide_rbac.authorization.k8s.io_ClusterRole",
    );
    let rb = "apiVersion: rbac.authorization.k8s.io/v1\nkind: RoleBinding\nmetadata: {name: b, namespace: kube-system}\nroleRef: {apiGroup: rbac.authorization.k8s.io, kind: Role, name: x}\nsubjects: []\n";
    fails_with(&repo("i12-rb", &[("rb.yaml", rb)], &[]), "12", "RBAC only in app");
}

#[test]
fn item9_create_and_delete_are_checked_too() {
    // get, patch and delete by name, create allowed: passes
    let ok_cm = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: only, namespace: named}\n";
    let (ok, o) = check(&repo("i9-cd-ok", &[("o.yaml", ok_cm)], &[]), None);
    assert!(ok, "{o}");
    // get and patch only: a later prune would fail
    let kept = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: kept, namespace: named}\n";
    fails_with(
        &repo("i9-nodelete", &[("k.yaml", kept)], &[]),
        "9",
        "named_kept__ConfigMap: outside the rights of gha:test (no delete",
    );
    // no create right: fails, unless the object is known to exist
    let nc = "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: only, namespace: nocreate}\n";
    let r = repo("i9-nocreate", &[("n.yaml", nc)], &[]);
    fails_with(
        &r,
        "9",
        "nocreate_only__ConfigMap: outside the rights of gha:test (no create",
    );
    let (ok, o) = check_with(&r, None, "app", &["--existing", "nocreate_only__ConfigMap"]);
    assert!(ok, "{o}");
}
