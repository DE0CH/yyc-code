//! The checkout: HEAD, ancestry, and the `Deploy-*` trailers of a range of commits (deploy-workflows 6.3).

use crate::obj::{Id, kind_info};
use anyhow::{Result, bail};
use std::collections::BTreeSet;
use std::path::Path;
use std::process::Command;

fn git(repo: &Path, args: &[&str]) -> Result<std::process::Output> {
    Ok(Command::new("git").arg("-C").arg(repo).args(args).output()?)
}

pub fn head(repo: &Path) -> Result<String> {
    let o = git(repo, &["rev-parse", "HEAD"])?;
    if !o.status.success() {
        bail!(
            "git rev-parse HEAD in {}: {}",
            repo.display(),
            String::from_utf8_lossy(&o.stderr).trim()
        );
    }
    Ok(String::from_utf8_lossy(&o.stdout).trim().to_string())
}

pub fn has_commit(repo: &Path, sha: &str) -> Result<bool> {
    Ok(git(repo, &["cat-file", "-e", &format!("{sha}^{{commit}}")])?
        .status
        .success())
}

/// `a` is an ancestor of (or equal to) `b`.
pub fn is_ancestor(repo: &Path, a: &str, b: &str) -> Result<bool> {
    Ok(git(repo, &["merge-base", "--is-ancestor", a, b])?.status.success())
}

pub const KEYS: &[&str] = &[
    "Deploy-Prune",
    "Deploy-Prune-Namespace",
    "Deploy-Release",
    "Deploy-Replace",
];

#[derive(Debug, Default, Clone)]
pub struct Trailers {
    pub prune: BTreeSet<Id>,
    pub prune_ns: BTreeSet<String>,
    pub release: BTreeSet<Id>,
    pub replace: BTreeSet<Id>,
    /// malformed or refused trailers, with the commit: `check` item 11 fails the push on them; `plan` only warns
    /// (the commit is on main for good, and a plan blocked on it could never advance the inventory)
    pub errors: Vec<String>,
}

/// Kinds `Deploy-Replace` may not name (deploy-workflows 6.3): delete-then-create would take them away while serving.
pub fn replace_refused(id: &Id, serving_ns: &str) -> Option<&'static str> {
    if id.group.is_empty() && matches!(id.kind.as_str(), "PersistentVolume" | "PersistentVolumeClaim") {
        return Some("a PersistentVolume or claim is never replaced");
    }
    let serving = (id.group == "apps" && matches!(id.kind.as_str(), "Deployment" | "DaemonSet" | "StatefulSet"))
        || (id.group == "policy" && id.kind == "PodDisruptionBudget")
        || (id.group.is_empty() && id.kind == "Service");
    (serving && id.ns == serving_ns).then_some("rename it instead: the new object serves before the old one is pruned")
}

impl Trailers {
    /// Parses `(commit, key, value)` triples; keys other than `Deploy-*` are ignored.
    pub fn parse(list: &[(String, String, String)], serving_ns: &str) -> Trailers {
        let mut t = Trailers::default();
        for (commit, key, value) in list {
            if !key.starts_with("Deploy-") {
                continue;
            }
            let c = &commit[..commit.len().min(12)];
            if !KEYS.contains(&key.as_str()) {
                t.errors
                    .push(format!("{c}: unknown trailer `{key}` (known: {})", KEYS.join(", ")));
                continue;
            }
            for v in value.split(',').map(str::trim).filter(|v| !v.is_empty()) {
                match key.as_str() {
                    "Deploy-Prune-Namespace" => {
                        if v.chars()
                            .all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '-')
                        {
                            t.prune_ns.insert(v.into());
                        } else {
                            t.errors.push(format!("{c}: `{key}: {v}`: not a namespace name"));
                        }
                    }
                    _ => match Id::parse(v) {
                        Err(e) => t.errors.push(format!("{c}: `{key}`: {e}")),
                        Ok(id) => {
                            if kind_info(&id.group, &id.kind).is_none() {
                                t.errors
                                    .push(format!("{c}: `{key}: {v}`: unknown kind {}/{}", id.group, id.kind));
                            }
                            match key.as_str() {
                                "Deploy-Prune" => {
                                    t.prune.insert(id);
                                }
                                "Deploy-Release" => {
                                    t.release.insert(id);
                                }
                                _ => {
                                    // a refused Deploy-Replace never replaces: the object stays an update (or an
                                    // immutable-field error the plan reports)
                                    if let Some(why) = replace_refused(&id, serving_ns) {
                                        t.errors.push(format!("{c}: `Deploy-Replace: {v}` refused: {why}"));
                                    } else {
                                        t.replace.insert(id);
                                    }
                                }
                            }
                        }
                    },
                }
            }
        }
        t
    }
}

/// `(commit, key, value)` of every trailer of the commits in `range` (`A..B`, or one revision = its whole history).
pub fn trailers(repo: &Path, range: &str) -> Result<Vec<(String, String, String)>> {
    trailers_of(repo, &[range])
}

/// The trailers of the commits `git log <args>` lists.
pub fn trailers_of(repo: &Path, args: &[&str]) -> Result<Vec<(String, String, String)>> {
    let mut a = vec!["log", "--format=%H%x1f%(trailers:only,unfold)%x1e"];
    a.extend_from_slice(args);
    let o = git(repo, &a)?;
    if !o.status.success() {
        bail!(
            "git log {}: {}",
            args.join(" "),
            String::from_utf8_lossy(&o.stderr).trim()
        );
    }
    let mut out = Vec::new();
    for rec in String::from_utf8_lossy(&o.stdout).split('\x1e') {
        let Some((sha, body)) = rec.trim_start_matches('\n').split_once('\x1f') else {
            continue;
        };
        for line in body.lines() {
            if let Some((k, v)) = line.split_once(':') {
                out.push((sha.trim().to_string(), k.trim().to_string(), v.trim().to_string()));
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn t(key: &str, value: &str) -> (String, String, String) {
        ("0123456789abcdef".into(), key.into(), value.into())
    }

    #[test]
    fn well_formed_trailers_parse() {
        let tr = Trailers::parse(
            &[
                t("Deploy-Prune", "jarvis_web__Service, _yyc-ai-jfs__PersistentVolume"),
                t("Deploy-Prune-Namespace", "jarvis"),
                t(
                    "Deploy-Release",
                    "flux-system_juicefs-csi-driver_helm.toolkit.fluxcd.io_HelmRelease",
                ),
                t("Deploy-Replace", "yyc-ai_import_batch_Job"),
                t("Co-Authored-By", "x"),
            ],
            "yyc-ai",
        );
        assert!(tr.errors.is_empty(), "{:?}", tr.errors);
        assert_eq!(tr.prune.len(), 2);
        assert!(tr.prune_ns.contains("jarvis"));
        assert_eq!(tr.release.len(), 1);
        assert_eq!(tr.replace.len(), 1);
    }

    #[test]
    fn malformed_and_refused_trailers_are_errors() {
        for (k, v) in [
            ("Deploy-Prune", "jarvis/web"),
            ("Deploy-Prune", "jarvis_web_example.com_Widget"),
            ("Deploy-Prnue", "jarvis_web__Service"),
            ("Deploy-Prune-Namespace", "Jarvis_x"),
            ("Deploy-Replace", "yyc-ai_pigeon_apps_Deployment"),
            ("Deploy-Replace", "yyc-ai_pigeon__Service"),
            ("Deploy-Replace", "yyc-ai_pigeon_policy_PodDisruptionBudget"),
            ("Deploy-Replace", "jarvis_jfs__PersistentVolumeClaim"),
            ("Deploy-Replace", "_yyc-ai-jfs__PersistentVolume"),
        ] {
            let tr = Trailers::parse(&[t(k, v)], "yyc-ai");
            assert_eq!(tr.errors.len(), 1, "{k}: {v}");
            assert!(
                tr.replace.is_empty(),
                "{k}: {v}: a refused Deploy-Replace must not replace"
            );
        }
    }
}
