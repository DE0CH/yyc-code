//! pg-heartbeat (README.md): the heartbeat of YYC's Postgres clusters and the watch that replaces a primary that
//! stopped committing.
//!
//!   pg-heartbeat watch CLUSTER:DATABASE:USER:PASSWORD_ENV...   every 2 s per cluster: the heartbeat row, and the rule
//!                                                              of `watch.rs` (in the Pod, namespace from its account;
//!                                                              HCLOUD_TOKEN and HETZNER_PROXY for a node reset)
//!   pg-heartbeat probe URL                                     one heartbeat, printed as committed|stalled|unreachable

mod watch;

use std::sync::Arc;
use std::time::{Duration, Instant};

use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, SignatureScheme};
use tokio_postgres::Client;
use tokio_postgres_rustls::MakeRustlsConnect;

use watch::{Action, Beat, Cluster, PodState, Watch};

/// Between heartbeats of one cluster.
const EVERY: Duration = Duration::from_secs(2);
/// A connection must be made within this long.
const CONNECT: Duration = Duration::from_secs(3);
/// The heartbeat's write must be answered within this long (a one-row upsert; `db/switchover.py` leaves schema ops
/// out of its write fence, so a planned switchover never holds it).
const WRITE: Duration = Duration::from_secs(5);
/// The read that tells a stall from a database that is away.
const READ: Duration = Duration::from_secs(3);

const SQL: &str = "SET client_min_messages = warning; CREATE SCHEMA IF NOT EXISTS ops;
     CREATE TABLE IF NOT EXISTS ops.heartbeat (src text PRIMARY KEY, at timestamptz NOT NULL);
     INSERT INTO ops.heartbeat VALUES ('pg-heartbeat', now()) ON CONFLICT (src) DO UPDATE SET at = excluded.at;";

const SA: &str = "/var/run/secrets/kubernetes.io/serviceaccount";

fn now() -> String {
    let t = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    format!("{t}")
}

macro_rules! say {
    ($($a:tt)*) => { println!("{} {}", now(), format!($($a)*)) };
}

/// TLS without certificate checks (`sslmode=require`: the operator's CA is short-lived, the path is the cluster's).
#[derive(Debug)]
struct AnyCert(rustls::crypto::WebPkiSupportedAlgorithms);

impl ServerCertVerifier for AnyCert {
    fn verify_server_cert(
        &self,
        _: &CertificateDer<'_>,
        _: &[CertificateDer<'_>],
        _: &ServerName<'_>,
        _: &[u8],
        _: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        Ok(ServerCertVerified::assertion())
    }
    fn verify_tls12_signature(
        &self,
        m: &[u8],
        c: &CertificateDer<'_>,
        d: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(m, c, d, &self.0)
    }
    fn verify_tls13_signature(
        &self,
        m: &[u8],
        c: &CertificateDer<'_>,
        d: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(m, c, d, &self.0)
    }
    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.0.supported_schemes()
    }
}

fn tls() -> MakeRustlsConnect {
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let algs = provider.signature_verification_algorithms;
    let cfg = rustls::ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .expect("ring supports the default TLS versions")
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(AnyCert(algs)))
        .with_no_client_auth();
    MakeRustlsConnect::new(cfg)
}

/// A new connection within [`CONNECT`], driven by its own task.
async fn connect(url: &str) -> Result<Client, String> {
    let fut = tokio_postgres::connect(url, tls());
    let (client, conn) = tokio::time::timeout(CONNECT, fut)
        .await
        .map_err(|_| format!("no connection in {} s", CONNECT.as_secs()))?
        .map_err(|e| e.to_string())?;
    tokio::spawn(conn);
    Ok(client)
}

/// One heartbeat on `url`. Every connection is new and dropped after (a stalled one must not carry over).
async fn beat(url: &str) -> (Beat, String) {
    let client = match connect(url).await {
        Ok(c) => c,
        Err(e) => return (Beat::Unreachable, e),
    };
    match tokio::time::timeout(WRITE, client.batch_execute(SQL)).await {
        Ok(Ok(())) => return (Beat::Committed, String::new()),
        Ok(Err(e)) => return (Beat::Unreachable, format!("the write failed: {e}")),
        Err(_) => {}
    }
    drop(client);
    // the write got no answer: does the primary still answer a read?
    let read = async {
        let c = connect(url).await?;
        c.simple_query("select 1").await.map_err(|e| e.to_string())
    };
    match tokio::time::timeout(READ, read).await {
        Ok(Ok(_)) => (
            Beat::Stalled,
            format!(
                "the write got no answer in {} s, a read did",
                WRITE.as_secs()
            ),
        ),
        Ok(Err(e)) => (
            Beat::Unreachable,
            format!("the write and a read failed: {e}"),
        ),
        Err(_) => (
            Beat::Unreachable,
            "neither the write nor a read was answered".into(),
        ),
    }
}

/// The Kubernetes API with the Pod's own account.
struct Kube {
    http: reqwest::Client,
    token: String,
    ns: String,
}

impl Kube {
    fn new() -> Result<Self, String> {
        let read =
            |f: &str| std::fs::read(format!("{SA}/{f}")).map_err(|e| format!("{SA}/{f}: {e}"));
        let ca = reqwest::Certificate::from_pem(&read("ca.crt")?).map_err(|e| e.to_string())?;
        let http = reqwest::Client::builder()
            .no_proxy()
            .add_root_certificate(ca)
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|e| e.to_string())?;
        let s = |b: Vec<u8>| String::from_utf8_lossy(&b).trim().to_owned();
        Ok(Self {
            http,
            token: s(read("token")?),
            ns: s(read("namespace")?),
        })
    }

    fn url(&self, path: &str) -> String {
        format!("https://kubernetes.default.svc{path}")
    }

    async fn cluster(&self, name: &str) -> Result<Cluster, String> {
        let path = format!(
            "/apis/postgresql.cnpg.io/v1/namespaces/{}/clusters/{name}",
            self.ns
        );
        let v: serde_json::Value = self
            .http
            .get(self.url(&path))
            .bearer_auth(&self.token)
            .send()
            .await
            .and_then(reqwest::Response::error_for_status)
            .map_err(|e| e.to_string())?
            .json()
            .await
            .map_err(|e| e.to_string())?;
        let st = &v["status"];
        Ok(Cluster {
            primary: st["currentPrimary"].as_str().unwrap_or_default().to_owned(),
            ready: u32::try_from(st["readyInstances"].as_u64().unwrap_or(0)).unwrap_or(0),
            switchover: st["phase"].as_str() == Some("Switchover in progress"),
        })
    }

    /// The deleted Pod: still terminating (its node), or gone (a new Pod of the name is not terminating).
    async fn pod(&self, pod: &str) -> Result<PodState, String> {
        let path = format!("/api/v1/namespaces/{}/pods/{pod}", self.ns);
        let r = self
            .http
            .get(self.url(&path))
            .bearer_auth(&self.token)
            .send()
            .await
            .map_err(|e| e.to_string())?;
        if r.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(PodState::Gone);
        }
        let v: serde_json::Value = r
            .error_for_status()
            .map_err(|e| e.to_string())?
            .json()
            .await
            .map_err(|e| e.to_string())?;
        let node = v["spec"]["nodeName"]
            .as_str()
            .unwrap_or_default()
            .to_owned();
        Ok(
            if v["metadata"]["deletionTimestamp"].is_string() && !node.is_empty() {
                PodState::Terminating(node)
            } else {
                PodState::Gone
            },
        )
    }

    async fn delete_pod(&self, pod: &str) -> Result<(), String> {
        let path = format!("/api/v1/namespaces/{}/pods/{pod}", self.ns);
        self.http
            .delete(self.url(&path))
            .bearer_auth(&self.token)
            .send()
            .await
            .and_then(reqwest::Response::error_for_status)
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
}

/// The Hetzner Cloud API (project yyc) for a node reset, through the cluster's socks pool.
struct Hetzner {
    http: reqwest::Client,
    token: String,
}

impl Hetzner {
    /// `None` without `HCLOUD_TOKEN` (the watch then cannot reset a node, and says so when it would).
    fn from_env() -> Result<Option<Self>, String> {
        let Ok(token) = std::env::var("HCLOUD_TOKEN") else {
            return Ok(None);
        };
        let mut b = reqwest::Client::builder().timeout(Duration::from_secs(10));
        if let Ok(p) = std::env::var("HETZNER_PROXY") {
            b = b.proxy(reqwest::Proxy::all(&p).map_err(|e| format!("HETZNER_PROXY: {e}"))?);
        }
        Ok(Some(Self {
            http: b.build().map_err(|e| e.to_string())?,
            token,
        }))
    }

    /// A hard reset of the server named `node` (a Talos node is named as its server).
    async fn reset(&self, node: &str) -> Result<(), String> {
        let api = "https://api.hetzner.cloud/v1";
        let v: serde_json::Value = self
            .http
            .get(format!("{api}/servers"))
            .query(&[("name", node)])
            .bearer_auth(&self.token)
            .send()
            .await
            .and_then(reqwest::Response::error_for_status)
            .map_err(|e| e.to_string())?
            .json()
            .await
            .map_err(|e| e.to_string())?;
        let Some(id) = v["servers"][0]["id"].as_u64() else {
            return Err(format!("no server named {node}"));
        };
        self.http
            .post(format!("{api}/servers/{id}/actions/reset"))
            .bearer_auth(&self.token)
            .send()
            .await
            .and_then(reqwest::Response::error_for_status)
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
}

/// One cluster's loop: a heartbeat every [`EVERY`] and the rule.
async fn watch(kube: Arc<Kube>, hetzner: Option<Arc<Hetzner>>, name: String, url: String) {
    let mut w = Watch::default();
    loop {
        let started = Instant::now();
        let (b, why) = beat(&url).await;
        if b != Beat::Committed {
            say!("{name}: beat {b:?}: {why}");
        }
        let mut action = w.observe(b, Instant::now());
        if action == Action::ReadCluster {
            action = match kube.cluster(&name).await {
                Ok(c) => {
                    say!(
                        "{name}: the primary {} committed nothing for {} s; ready instances {}",
                        c.primary,
                        watch::STALL_LIMIT.as_secs(),
                        c.ready
                    );
                    w.confirm(&c, Instant::now())
                }
                Err(e) => {
                    say!("{name}: cluster state not read: {e}");
                    Action::Nothing
                }
            };
        }
        if let Action::CheckPod(pod) = &action {
            action = match kube.pod(pod).await {
                Ok(state) => w.checked(&state),
                Err(e) => {
                    say!("{name}: {pod} not read: {e}");
                    Action::Nothing
                }
            };
        }
        match action {
            Action::DeletePod(pod) => match kube.delete_pod(&pod).await {
                Ok(()) => {
                    say!("{name}: deleted the stalled primary's Pod {pod}; a standby takes over")
                }
                Err(e) => say!("{name}: deleting {pod} failed: {e}"),
            },
            Action::ResetNode(node) => match &hetzner {
                Some(h) => match h.reset(&node).await {
                    Ok(()) => say!(
                        "{name}: the stalled primary's Pod could not be stopped: reset node {node}"
                    ),
                    Err(e) => say!("{name}: resetting node {node} failed: {e}"),
                },
                None => say!(
                    "{name}: the stalled primary's Pod could not be stopped, and no HCLOUD_TOKEN to reset {node}"
                ),
            },
            _ => {}
        }
        tokio::time::sleep(EVERY.saturating_sub(started.elapsed())).await;
    }
}

/// `CLUSTER:DATABASE:USER:PASSWORD_ENV` → (cluster, connection string).
fn target(spec: &str, ns: &str) -> Result<(String, String), String> {
    let p: Vec<&str> = spec.split(':').collect();
    let [cluster, db, user, pw_env] = p[..] else {
        return Err(format!("not CLUSTER:DATABASE:USER:PASSWORD_ENV: {spec}"));
    };
    let pw = std::env::var(pw_env).map_err(|_| format!("{pw_env} is not set"))?;
    let url = format!(
        "host={cluster}-rw.{ns}.svc.cluster.local dbname={db} user={user} password='{}' sslmode=require \
         application_name=pg-heartbeat",
        pw.replace('\\', "\\\\").replace('\'', "\\'")
    );
    Ok((cluster.to_owned(), url))
}

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("probe") if args.len() == 2 => {
            let (b, why) = beat(&args[1]).await;
            println!("{}", format!("{b:?}").to_lowercase());
            if !why.is_empty() {
                eprintln!("{why}");
            }
        }
        Some("watch") if args.len() > 1 => {
            let kube = match Kube::new() {
                Ok(k) => Arc::new(k),
                Err(e) => {
                    eprintln!("pg-heartbeat: the Pod's account: {e}");
                    std::process::exit(78);
                }
            };
            let hetzner = match Hetzner::from_env() {
                Ok(h) => h.map(Arc::new),
                Err(e) => {
                    eprintln!("pg-heartbeat: {e}");
                    std::process::exit(78);
                }
            };
            if hetzner.is_none() {
                say!("no HCLOUD_TOKEN: a stalled primary whose Pod cannot be stopped is not reset");
            }
            let mut tasks = Vec::new();
            for spec in &args[1..] {
                match target(spec, &kube.ns) {
                    Ok((name, url)) => {
                        say!("{name}: watching");
                        tasks.push(tokio::spawn(watch(
                            kube.clone(),
                            hetzner.clone(),
                            name,
                            url,
                        )));
                    }
                    Err(e) => {
                        eprintln!("pg-heartbeat: {e}");
                        std::process::exit(78);
                    }
                }
            }
            for t in tasks {
                let _ = t.await;
            }
        }
        _ => {
            eprintln!(
                "usage: pg-heartbeat watch CLUSTER:DATABASE:USER:PASSWORD_ENV... | pg-heartbeat probe URL"
            );
            std::process::exit(64);
        }
    }
}
