//! kubectl and kustomize, driven as processes (the applier owns ordering, gating and inventory; kubectl owns the
//! server-side apply itself).

use crate::obj::Id;
use anyhow::{Context, Result, anyhow, bail};
use serde_json::{Value, json};
use std::io::Write;
use std::path::Path;
use std::process::{Command, Output, Stdio};

pub const FIELD_MANAGER: &str = "yyc-ai-deploy";

#[derive(Clone, Debug)]
pub struct Kube {
    pub bin: String,
}

fn run(mut cmd: Command, stdin: Option<&[u8]>) -> Result<Output> {
    cmd.stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd.spawn().with_context(|| format!("starting {cmd:?}"))?;
    if let Some(data) = stdin {
        let mut s = child.stdin.take().ok_or_else(|| anyhow!("no stdin"))?;
        s.write_all(data)?;
    }
    Ok(child.wait_with_output()?)
}

/// Every JSON value kubectl printed, with `List`s flattened.
pub fn json_stream(out: &[u8]) -> Result<Vec<Value>> {
    let mut v = Vec::new();
    for item in serde_json::Deserializer::from_slice(out).into_iter::<Value>() {
        let item = item.context("kubectl printed something that is not JSON")?;
        if item["kind"].as_str().is_some_and(|k| k.ends_with("List")) && item["items"].is_array() {
            v.extend(item["items"].as_array().cloned().unwrap_or_default());
        } else {
            v.push(item);
        }
    }
    Ok(v)
}

pub fn list(items: &[Value]) -> Vec<u8> {
    serde_json::to_vec(&json!({"apiVersion": "v1", "kind": "List", "items": items})).unwrap_or_default()
}

impl Kube {
    pub fn new(bin: &str) -> Kube {
        Kube { bin: bin.into() }
    }

    pub fn raw(&self, args: &[String], stdin: Option<&[u8]>) -> Result<Output> {
        let mut c = Command::new(&self.bin);
        c.args(args);
        run(c, stdin)
    }

    /// Runs kubectl and returns stdout, or fails with its stderr.
    pub fn ok(&self, args: &[String], stdin: Option<&[u8]>) -> Result<Vec<u8>> {
        let o = self.raw(args, stdin)?;
        if !o.status.success() {
            bail!(
                "kubectl {}: {}",
                args.join(" "),
                String::from_utf8_lossy(&o.stderr).trim()
            );
        }
        Ok(o.stdout)
    }

    pub fn get(&self, id: &Id, as_: Option<&str>) -> Result<Option<Value>> {
        let mut a = vec!["get".to_string()];
        a.extend(id.kubectl_ref());
        a.extend([
            "-o".into(),
            "json".into(),
            "--ignore-not-found".into(),
            "--show-managed-fields".into(),
        ]);
        if let Some(u) = as_ {
            a.push(format!("--as={u}"));
        }
        let out = self.ok(&a, None)?;
        if out.iter().all(u8::is_ascii_whitespace) {
            return Ok(None);
        }
        Ok(Some(serde_json::from_slice(&out)?))
    }

    /// Server-side apply of `objs` as one List (manager `yyc-ai-deploy`, `--force-conflicts`: this manager owns what
    /// git says). Returns the objects as the server stored them (or would, with `dry`).
    pub fn apply(&self, objs: &[Value], dry: bool, as_: Option<&str>) -> Result<Vec<Value>> {
        if objs.is_empty() {
            return Ok(vec![]);
        }
        let mut a: Vec<String> = ["apply", "--server-side", "--force-conflicts", "-o", "json", "-f", "-"]
            .iter()
            .map(|s| (*s).to_string())
            .collect();
        a.push(format!("--field-manager={FIELD_MANAGER}"));
        if dry {
            a.push("--dry-run=server".into());
        }
        if let Some(u) = as_ {
            a.push(format!("--as={u}"));
        }
        json_stream(&self.ok(&a, Some(&list(objs)))?)
    }

    pub fn delete(&self, id: &Id, as_: Option<&str>, timeout: &str) -> Result<()> {
        let mut a = vec!["delete".to_string()];
        a.extend(id.kubectl_ref());
        a.extend([
            "--ignore-not-found".into(),
            "--wait".into(),
            format!("--timeout={timeout}"),
        ]);
        if let Some(u) = as_ {
            a.push(format!("--as={u}"));
        }
        self.ok(&a, None).map(|_| ())
    }

    /// Every object of `resource` (a kubectl type, e.g. `pods`, `replicasets.apps`) in `ns`.
    pub fn list(&self, resource: &str, ns: &str) -> Result<Vec<Value>> {
        let a: Vec<String> = vec![
            "get".into(),
            resource.into(),
            "-n".into(),
            ns.into(),
            "-o".into(),
            "json".into(),
        ];
        json_stream(&self.ok(&a, None)?)
    }

    pub fn rollout_status(&self, id: &Id, timeout: &str) -> Result<()> {
        let mut a = vec![
            "rollout".to_string(),
            "status".into(),
            format!("{}/{}", id.kubectl_type(), id.name),
        ];
        if !id.ns.is_empty() {
            a.extend(["-n".into(), id.ns.clone()]);
        }
        a.push(format!("--timeout={timeout}"));
        self.ok(&a, None).map(|_| ())
    }
}

/// `kustomize build DIR`, every document as JSON (Lists flattened, empty documents dropped).
pub fn kustomize_build(kustomize: &str, dir: &Path) -> Result<Vec<Value>> {
    let mut c = Command::new(kustomize);
    c.arg("build").arg(dir);
    let o = run(c, None)?;
    if !o.status.success() {
        bail!(
            "kustomize build {}: {}",
            dir.display(),
            String::from_utf8_lossy(&o.stderr).trim()
        );
    }
    parse_yaml_stream(&o.stdout)
}

pub fn parse_yaml_stream(data: &[u8]) -> Result<Vec<Value>> {
    use serde::Deserialize;
    let mut v = Vec::new();
    for doc in serde_yaml::Deserializer::from_slice(data) {
        let y = serde_yaml::Value::deserialize(doc)?;
        if y.is_null() {
            continue;
        }
        let j: Value = serde_json::to_value(y)?;
        if j["kind"].as_str().is_some_and(|k| k.ends_with("List")) && j["items"].is_array() {
            v.extend(j["items"].as_array().cloned().unwrap_or_default());
        } else {
            v.push(j);
        }
    }
    Ok(v)
}
