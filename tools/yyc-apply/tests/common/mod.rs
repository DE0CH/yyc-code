//! Test helpers: a throwaway git repo holding a kustomize folder, and the binary and kubectl as processes.
#![allow(dead_code, clippy::unwrap_used, clippy::expect_used)]

use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

pub fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_yyc-apply")
}

pub struct Repo {
    pub dir: PathBuf,
}

fn git(dir: &Path, args: &[&str]) -> String {
    let o = Command::new("git").arg("-C").arg(dir).args(args).output().unwrap();
    assert!(
        o.status.success(),
        "git {args:?}: {}",
        String::from_utf8_lossy(&o.stderr)
    );
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}

impl Repo {
    pub fn new(name: &str) -> Repo {
        let n = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let dir = std::env::temp_dir()
            .join("yyc-apply-tests")
            .join(format!("{name}-{}-{n}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        git(&dir, &["init", "-q", "-b", "main"]);
        git(&dir, &["config", "user.email", "test@example.invalid"]);
        git(&dir, &["config", "user.name", "test"]);
        Repo { dir }
    }
    pub fn write(&self, path: &str, content: &str) {
        let p = self.dir.join(path);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, content).unwrap();
    }
    pub fn remove(&self, path: &str) {
        let _ = std::fs::remove_file(self.dir.join(path));
    }
    /// Commits everything; `msg` may carry trailers. Returns the sha.
    pub fn commit(&self, msg: &str) -> String {
        git(&self.dir, &["add", "-A"]);
        git(&self.dir, &["commit", "-q", "--allow-empty", "-m", msg]);
        git(&self.dir, &["rev-parse", "HEAD"])
    }
    pub fn checkout(&self, rev: &str) {
        git(&self.dir, &["checkout", "-q", rev]);
    }
    pub fn path(&self, p: &str) -> String {
        self.dir.join(p).display().to_string()
    }
}

pub fn run(cmd: &str, args: &[&str], stdin: Option<&str>) -> Output {
    let mut c = Command::new(cmd);
    c.args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() });
    let mut ch = c.spawn().unwrap_or_else(|e| panic!("{cmd}: {e}"));
    if let Some(s) = stdin {
        use std::io::Write;
        ch.stdin.take().unwrap().write_all(s.as_bytes()).unwrap();
    }
    ch.wait_with_output().unwrap()
}

pub fn out(o: &Output) -> String {
    format!(
        "{}{}",
        String::from_utf8_lossy(&o.stdout),
        String::from_utf8_lossy(&o.stderr)
    )
}

pub fn yyc(args: &[&str]) -> Output {
    run(bin(), args, None)
}

/// The binary with `KUBECONFIG` set (another identity than the test's cluster-admin).
pub fn yyc_as(kubeconfig: &str, args: &[&str]) -> Output {
    Command::new(bin())
        .args(args)
        .env("KUBECONFIG", kubeconfig)
        .stdin(Stdio::null())
        .output()
        .unwrap()
}

pub fn kubectl(args: &[&str], stdin: Option<&str>) -> Output {
    run("kubectl", args, stdin)
}

pub fn kc(args: &[&str]) -> String {
    let o = kubectl(args, None);
    assert!(o.status.success(), "kubectl {args:?}: {}", out(&o));
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}

pub fn kc_stdin(args: &[&str], stdin: &str) -> String {
    let o = kubectl(args, Some(stdin));
    assert!(o.status.success(), "kubectl {args:?}: {}", out(&o));
    String::from_utf8_lossy(&o.stdout).trim().to_string()
}
