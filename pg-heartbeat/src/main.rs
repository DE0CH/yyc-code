//! pg-heartbeat (README.md): the heartbeat of YYC's Postgres clusters and the watch that replaces a primary that
//! stopped committing.
//!
//!   pg-heartbeat watch CLUSTER:DATABASE:USER:PASSWORD_ENV...   every 10 s per cluster: the heartbeat row, and the rule
//!                                                              of `watch.rs` (in the Pod, namespace from its account)
//!   pg-heartbeat probe URL                                     one heartbeat, printed as committed|stalled|unreachable

mod watch;

use std::sync::Arc;
use std::time::{Duration, Instant};

use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, SignatureScheme};
use tokio_postgres::Client;
use tokio_postgres_rustls::MakeRustlsConnect;

use watch::{Action, Beat, Cluster, Watch};

/// Between heartbeats of one cluster.
const EVERY: Duration = Duration::from_secs(10);
/// A connection must be made within this long.
const CONNECT: Duration = Duration::from_secs(5);
/// The heartbeat's write must be answered within this long (a planned switchover holds writes about 10 s).
const WRITE: Duration = Duration::from_secs(15);
/// The read that tells a stall from a database that is away.
const READ: Duration = Duration::from_secs(5);

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
            format!("the write got no answer in {} s, a read did", WRITE.as_secs()),
        ),
        Ok(Err(e)) => (Beat::Unreachable, format!("the write and a read failed: {e}")),
        Err(_) => (Beat::Unreachable, "neither the write nor a read was answered".into()),
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
        let read = |f: &str| std::fs::read(format!("{SA}/{f}")).map_err(|e| format!("{SA}/{f}: {e}"));
        let ca = reqwest::Certificate::from_pem(&read("ca.crt")?).map_err(|e| e.to_string())?;
        let http = reqwest::Client::builder()
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
        let path = format!("/apis/postgresql.cnpg.io/v1/namespaces/{}/clusters/{name}", self.ns);
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
        })
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

/// One cluster's loop: a heartbeat every [`EVERY`] and the rule.
async fn watch(kube: Arc<Kube>, name: String, url: String) {
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
                    say!("{name}: the primary {} committed nothing for {} s; ready instances {}", c.primary,
                        watch::STALL_LIMIT.as_secs(), c.ready);
                    w.confirm(&c, Instant::now())
                }
                Err(e) => {
                    say!("{name}: cluster state not read: {e}");
                    Action::Nothing
                }
            };
        }
        if let Action::DeletePod(pod) = action {
            match kube.delete_pod(&pod).await {
                Ok(()) => say!("{name}: deleted the stalled primary's Pod {pod}; a standby takes over"),
                Err(e) => say!("{name}: deleting {pod} failed: {e}"),
            }
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
            let mut tasks = Vec::new();
            for spec in &args[1..] {
                match target(spec, &kube.ns) {
                    Ok((name, url)) => {
                        say!("{name}: watching");
                        tasks.push(tokio::spawn(watch(kube.clone(), name, url)));
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
            eprintln!("usage: pg-heartbeat watch CLUSTER:DATABASE:USER:PASSWORD_ENV... | pg-heartbeat probe URL");
            std::process::exit(64);
        }
    }
}
