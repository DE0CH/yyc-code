// yyc-socks: the cluster's way out to the internet (socks/README.md). A SOCKS5 server (RFC 1928, CONNECT only; an
// HTTP proxy on the same port for clients that speak only that) that a pod hands a domain name and a port: the server resolves the name itself, through its node's resolver over IPv6
// (or, for the `sandbox` profile, through malware-blocking resolvers), refuses addresses that aren't public, and
// connects IPv6 first, IPv4 (the node's WARP) 250 ms later (Happy Eyeballs, RFC 8305). Pods never look up outside
// names or care which address family a site has.
//
// One copy per node of the pool (a DaemonSet on the host network, deploy/apps/socks.yaml), listening on the node's
// private address only; Service socks.egress spreads connections over the copies whose readiness says DNS, IPv6 and
// IPv4 all work from that node, so a node whose way out breaks leaves the pool by itself. FAMILIES=ipv4: a node with no
// IPv6 at all (yyc-talos's workers, which leave through yyc-nat1's WARP): IPv4 addresses only, readiness without IPv6.
mod http;
mod policy;
mod resolve;
mod socks;

use std::net::{IpAddr, SocketAddr};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{RwLock, Semaphore};

use policy::Policy;
use resolve::{Profile, Resolvers, Refusal};
use socks::{Reply, Target};

/// What a connection ended as, for the counters on /metrics and the log line of a refusal.
#[derive(Default)]
pub struct Counters {
    pub accepted: AtomicU64,
    pub connected_v6: AtomicU64,
    pub connected_v4: AtomicU64,
    pub refused_address: AtomicU64,
    pub refused_threat: AtomicU64,
    pub refused_nxdomain: AtomicU64,
    pub dns_failed: AtomicU64,
    pub connect_failed: AtomicU64,
    pub bad_request: AtomicU64,
    pub busy: AtomicU64,
    pub bytes_up: AtomicU64,
    pub bytes_down: AtomicU64,
}

/// The node's way out as the prober last saw it (readiness).
#[derive(Clone, Copy, Default)]
struct Health {
    dns: Option<Instant>,
    v6: Option<Instant>,
    v4: Option<Instant>,
}

struct App {
    resolvers: Resolvers,
    policy: Policy,
    counters: Counters,
    health: RwLock<Health>,
    /// SIGTERM seen: readiness says no, so the Service stops sending new connections here
    draining: std::sync::atomic::AtomicBool,
    slots: Semaphore,
    connect_timeout: Duration,
    /// FAMILIES=ipv4: connect over IPv4 only and leave IPv6 out of readiness (the node has no IPv6)
    ipv4_only: bool,
}

fn env(name: &str, default: &str) -> String {
    std::env::var(name).ok().filter(|v| !v.is_empty()).unwrap_or_else(|| default.to_string())
}

#[tokio::main]
async fn main() {
    let host = env("HOST_IP", "127.0.0.1");
    let listen: SocketAddr = format!("{}:{}", bracket(&host), env("SOCKS_PORT", "1080")).parse().expect("HOST_IP/SOCKS_PORT");
    let health_listen: SocketAddr =
        format!("{}:{}", bracket(&host), env("HEALTH_PORT", "1081")).parse().expect("HOST_IP/HEALTH_PORT");
    let app = Arc::new(App {
        resolvers: Resolvers::new(&env("DEFAULT_DNS", "system"), &env("SANDBOX_DNS", resolve::SANDBOX_DNS)),
        policy: Policy::new(&env("DENY_CIDRS", "")),
        counters: Counters::default(),
        health: RwLock::new(Health::default()),
        draining: std::sync::atomic::AtomicBool::new(false),
        slots: Semaphore::new(env("MAX_CONNECTIONS", "8192").parse().expect("MAX_CONNECTIONS")),
        connect_timeout: Duration::from_millis(env("CONNECT_TIMEOUT_MS", "10000").parse().expect("CONNECT_TIMEOUT_MS")),
        ipv4_only: match env("FAMILIES", "both").as_str() {
            "both" => false,
            "ipv4" => true,
            f => panic!("FAMILIES: both or ipv4, not {f}"),
        },
    });
    app.policy.refresh_local();

    let socks_listener = TcpListener::bind(listen).await.unwrap_or_else(|e| panic!("bind {listen}: {e}"));
    let health_listener = TcpListener::bind(health_listen).await.unwrap_or_else(|e| panic!("bind {health_listen}: {e}"));
    eprintln!("yyc-socks {} on {listen}, health on {health_listen}", env!("CARGO_PKG_VERSION"));

    tokio::spawn(probe_loop(app.clone()));
    tokio::spawn(local_addresses_loop(app.clone()));
    tokio::spawn(health_server(app.clone(), health_listener));

    // SIGTERM (a rollout, a drain): readiness turns 503 at once, connections are still taken for LINGER_SECONDS while
    // the Service's endpoints catch up, then the listener closes and the open connections get up to DRAIN_SECONDS
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).expect("SIGTERM");
    let linger = Duration::from_secs(env("LINGER_SECONDS", "15").parse().expect("LINGER_SECONDS"));
    let mut stop_at: Option<tokio::time::Instant> = None;
    loop {
        let until = stop_at.unwrap_or_else(|| tokio::time::Instant::now() + Duration::from_secs(3600));
        tokio::select! {
            r = socks_listener.accept() => match r {
                Ok((stream, peer)) => { tokio::spawn(serve(app.clone(), stream, peer)); }
                Err(e) => { eprintln!("accept: {e}"); tokio::time::sleep(Duration::from_millis(100)).await; }
            },
            _ = term.recv(), if stop_at.is_none() => {
                app.draining.store(true, Ordering::SeqCst);
                stop_at = Some(tokio::time::Instant::now() + linger);
                eprintln!("SIGTERM: not ready; taking connections for {}s more", linger.as_secs());
            }
            _ = tokio::time::sleep_until(until), if stop_at.is_some() => break,
        }
    }
    drop(socks_listener);
    let drain = Duration::from_secs(env("DRAIN_SECONDS", "600").parse().expect("DRAIN_SECONDS"));
    let max: usize = env("MAX_CONNECTIONS", "8192").parse().unwrap();
    let started = Instant::now();
    eprintln!("listener closed; draining {} connections up to {}s", max - app.slots.available_permits(), drain.as_secs());
    while app.slots.available_permits() < max && started.elapsed() < drain {
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}

fn bracket(host: &str) -> String {
    if host.contains(':') { format!("[{host}]") } else { host.to_string() }
}

enum Proto {
    Socks,
    /// HTTP proxy; Some(bytes) = a plain request to send upstream first
    Http(Option<Vec<u8>>),
}

/// One client: SOCKS5 or HTTP proxy (told apart by the first byte), the policy, the connect, then bytes both ways
/// until either side closes.
async fn serve(app: Arc<App>, mut client: TcpStream, peer: SocketAddr) {
    let _ = client.set_nodelay(true);
    let Ok(slot) = app.slots.try_acquire() else {
        app.counters.busy.fetch_add(1, Ordering::Relaxed);
        return;
    };
    app.counters.accepted.fetch_add(1, Ordering::Relaxed);
    let head = tokio::time::timeout(Duration::from_secs(10), async {
        let mut first = [0u8; 1];
        if client.peek(&mut first).await.map_err(|e| e.to_string())? == 0 {
            return Err("closed".to_string());
        }
        if first[0] == 5 {
            match socks::handshake(&mut client).await {
                Ok(r) => Ok((r.target, r.profile, Proto::Socks)),
                Err(e) => {
                    if let Some(reply) = e.reply {
                        let _ = socks::reply(&mut client, reply, None).await;
                    }
                    Err(e.message)
                }
            }
        } else {
            client.read_exact(&mut first).await.map_err(|e| e.to_string())?;
            match http::read_request(&mut client, first[0]).await {
                Ok(r) => Ok((r.target, r.profile, Proto::Http(r.forward))),
                Err(e) => {
                    let reason = match e.status { 407 => "Proxy Authentication Required", 431 => "Request Header Fields Too Large", _ => "Bad Request" };
                    let _ = http::error(&mut client, e.status, reason, "bad_request", &e.message).await;
                    Err(e.message)
                }
            }
        }
    })
    .await;
    let (target, profile, proto) = match head {
        Ok(Ok(x)) => x,
        Ok(Err(m)) => {
            app.counters.bad_request.fetch_add(1, Ordering::Relaxed);
            if m != "closed" {
                eprintln!("bad request from {peer}: {m}");
            }
            return;
        }
        Err(_) => {
            app.counters.bad_request.fetch_add(1, Ordering::Relaxed);
            return;
        }
    };
    let what = target.to_string();
    match open(&app, &target, profile).await {
        Ok(mut upstream) => {
            let ok = match &proto {
                Proto::Socks => socks::reply(&mut client, Reply::Succeeded, upstream.local_addr().ok()).await.is_ok(),
                Proto::Http(None) => http::established(&mut client).await.is_ok(),
                Proto::Http(Some(first)) => upstream.write_all(first).await.is_ok(),
            };
            if !ok {
                return;
            }
            if let Ok((up, down)) = tokio::io::copy_bidirectional(&mut client, &mut upstream).await {
                app.counters.bytes_up.fetch_add(up, Ordering::Relaxed);
                app.counters.bytes_down.fetch_add(down, Ordering::Relaxed);
            }
        }
        Err(refusal) => {
            let (reply, counter) = match &refusal {
                Refusal::Address(_) => (Reply::NotAllowed, &app.counters.refused_address),
                Refusal::Threat => (Reply::Threat, &app.counters.refused_threat),
                Refusal::NoSuchName => (Reply::HostUnreachable, &app.counters.refused_nxdomain),
                Refusal::Dns(_) => (Reply::GeneralFailure, &app.counters.dns_failed),
                Refusal::Connect(_) => (Reply::from_connect(&refusal), &app.counters.connect_failed),
            };
            counter.fetch_add(1, Ordering::Relaxed);
            eprintln!("{peer} {} {what}: {refusal}", profile.name());
            let _ = match proto {
                Proto::Socks => socks::reply(&mut client, reply, None).await,
                Proto::Http(_) => http::refuse(&mut client, &refusal).await,
            };
        }
    }
    drop(slot);
}

/// The target's public addresses for this profile, then Happy Eyeballs over them.
async fn open(app: &App, target: &Target, profile: Profile) -> Result<TcpStream, Refusal> {
    let addrs: Vec<IpAddr> = match target {
        Target::Ip(ip, _) => vec![*ip],
        Target::Name(name, _) => app.resolvers.resolve(name, profile).await?,
    };
    let public: Vec<IpAddr> = addrs.iter().copied().filter(|a| app.policy.allowed(*a)).collect();
    if public.is_empty() {
        return Err(Refusal::Address(format!("{} is not a public address", addrs.iter().map(|a| a.to_string()).collect::<Vec<_>>().join(", "))));
    }
    let public = families(public, app.ipv4_only);
    if public.is_empty() {
        return Err(Refusal::Connect(std::io::Error::new(std::io::ErrorKind::NetworkUnreachable, "IPv6 only, and this node has no IPv6")));
    }
    let stream = connect_happy(&order(public), target.port(), app.connect_timeout).await?;
    if stream.peer_addr().map(|a| a.is_ipv6()).unwrap_or(false) {
        app.counters.connected_v6.fetch_add(1, Ordering::Relaxed);
    } else {
        app.counters.connected_v4.fetch_add(1, Ordering::Relaxed);
    }
    Ok(stream)
}

/// FAMILIES=ipv4: drop the IPv6 addresses (the node cannot reach them).
fn families(addrs: Vec<IpAddr>, ipv4_only: bool) -> Vec<IpAddr> {
    if ipv4_only { addrs.into_iter().filter(|a| a.is_ipv4()).collect() } else { addrs }
}

/// RFC 8305 section 4: IPv6 first, then alternate the families.
fn order(addrs: Vec<IpAddr>) -> Vec<IpAddr> {
    let (v6, v4): (Vec<IpAddr>, Vec<IpAddr>) = addrs.into_iter().partition(|a| a.is_ipv6());
    let mut out = Vec::with_capacity(v6.len() + v4.len());
    let (mut a, mut b) = (v6.into_iter(), v4.into_iter());
    loop {
        match (a.next(), b.next()) {
            (None, None) => break,
            (x, y) => {
                out.extend(x);
                out.extend(y);
            }
        }
    }
    out
}

/// Start one attempt, the next 250 ms later (or as soon as one fails); the first that connects wins, the others are
/// dropped. All of it within `timeout`.
async fn connect_happy(addrs: &[IpAddr], port: u16, timeout: Duration) -> Result<TcpStream, Refusal> {
    let deadline = tokio::time::Instant::now() + timeout;
    let mut set = tokio::task::JoinSet::new();
    let mut next = 0usize;
    let mut last_err: Option<std::io::Error> = None;
    loop {
        if next < addrs.len() {
            let sa = SocketAddr::new(addrs[next], port);
            set.spawn(async move { TcpStream::connect(sa).await });
            next += 1;
        }
        let stagger = tokio::time::sleep(Duration::from_millis(250));
        tokio::pin!(stagger);
        loop {
            tokio::select! {
                r = set.join_next(), if !set.is_empty() => match r {
                    Some(Ok(Ok(s))) => { let _ = s.set_nodelay(true); return Ok(s); }
                    Some(Ok(Err(e))) => { last_err = Some(e); if next < addrs.len() { break; } }
                    Some(Err(_)) => {}
                    None => {}
                },
                _ = &mut stagger, if next < addrs.len() => break,
                _ = tokio::time::sleep_until(deadline) => {
                    return Err(Refusal::Connect(std::io::Error::new(std::io::ErrorKind::TimedOut, "connect timed out")));
                }
            }
            if set.is_empty() && next >= addrs.len() {
                return Err(Refusal::Connect(last_err.unwrap_or_else(|| std::io::Error::other("no address"))));
            }
        }
    }
}

/// Every 5 s: can this node resolve a name, and reach the internet over IPv6 and over IPv4? (readiness)
async fn probe_loop(app: Arc<App>) {
    let v6_targets: Vec<SocketAddr> = ["[2606:4700:4700::1111]:443", "[2001:4860:4860::8888]:443"].iter().map(|s| s.parse().unwrap()).collect();
    let v4_targets: Vec<SocketAddr> = ["1.1.1.1:443", "8.8.8.8:443"].iter().map(|s| s.parse().unwrap()).collect();
    let names = env("PROBE_NAMES", "github.com,www.cloudflare.com");
    let names: Vec<String> = names.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
    let mut i = 0usize;
    loop {
        let name = &names[i % names.len()];
        let dns_ok = matches!(tokio::time::timeout(Duration::from_secs(4), app.resolvers.resolve(name, Profile::Default)).await, Ok(Ok(ref a)) if !a.is_empty());
        let v6_ok = !app.ipv4_only && probe_connect(&v6_targets[i % v6_targets.len()]).await;
        let v4_ok = probe_connect(&v4_targets[i % v4_targets.len()]).await;
        {
            let now = Instant::now();
            let mut h = app.health.write().await;
            if dns_ok { h.dns = Some(now); }
            if v6_ok { h.v6 = Some(now); }
            if v4_ok { h.v4 = Some(now); }
        }
        if !(dns_ok && (v6_ok || app.ipv4_only) && v4_ok) {
            eprintln!("probe: dns {dns_ok} ({name}), ipv6 {v6_ok}, ipv4 {v4_ok}");
        }
        i += 1;
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}

async fn probe_connect(sa: &SocketAddr) -> bool {
    matches!(tokio::time::timeout(Duration::from_secs(4), TcpStream::connect(sa)).await, Ok(Ok(_)))
}

async fn local_addresses_loop(app: Arc<App>) {
    loop {
        tokio::time::sleep(Duration::from_secs(60)).await;
        app.policy.refresh_local();
    }
}

/// GET /healthz (alive), /readyz (DNS, IPv6 and IPv4 all seen working within 20 s), /metrics (Prometheus text).
async fn health_server(app: Arc<App>, listener: TcpListener) {
    loop {
        let Ok((mut s, _)) = listener.accept().await else { continue };
        let app = app.clone();
        tokio::spawn(async move {
            let mut buf = [0u8; 1024];
            let n = match tokio::time::timeout(Duration::from_secs(5), s.read(&mut buf)).await {
                Ok(Ok(n)) => n,
                _ => return,
            };
            let line = String::from_utf8_lossy(&buf[..n]);
            let path = line.split_whitespace().nth(1).unwrap_or("/");
            let (code, body) = match path {
                "/healthz" => (200, "ok\n".to_string()),
                "/readyz" => {
                    let h = *app.health.read().await;
                    let fresh = |t: Option<Instant>| t.map(|t| t.elapsed() < Duration::from_secs(20)).unwrap_or(false);
                    let (d, v6, v4) = (fresh(h.dns), fresh(h.v6), fresh(h.v4));
                    let dr = app.draining.load(Ordering::SeqCst);
                    let v6_needed = !app.ipv4_only;
                    (if d && (v6 || !v6_needed) && v4 && !dr { 200 } else { 503 },
                     format!("dns {d}\nipv6 {}\nipv4 {v4}\ndraining {dr}\n", if v6_needed { v6.to_string() } else { "not used".to_string() }))
                }
                "/metrics" => (200, metrics(&app).await),
                _ => (404, "not found\n".to_string()),
            };
            let status = match code { 200 => "OK", 503 => "Service Unavailable", _ => "Not Found" };
            let resp = format!("HTTP/1.1 {code} {status}\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
            let _ = s.write_all(resp.as_bytes()).await;
        });
    }
}

async fn metrics(app: &App) -> String {
    let c = &app.counters;
    let g = |a: &AtomicU64| a.load(Ordering::Relaxed);
    let h = *app.health.read().await;
    let age = |t: Option<Instant>| t.map(|t| t.elapsed().as_secs_f64()).unwrap_or(-1.0);
    let max: usize = env("MAX_CONNECTIONS", "8192").parse().unwrap();
    format!(
        "yyc_socks_accepted_total {}\nyyc_socks_connected_total{{family=\"ipv6\"}} {}\nyyc_socks_connected_total{{family=\"ipv4\"}} {}\n\
yyc_socks_refused_total{{reason=\"address\"}} {}\nyyc_socks_refused_total{{reason=\"threat\"}} {}\nyyc_socks_refused_total{{reason=\"nxdomain\"}} {}\n\
yyc_socks_failed_total{{reason=\"dns\"}} {}\nyyc_socks_failed_total{{reason=\"connect\"}} {}\nyyc_socks_failed_total{{reason=\"bad_request\"}} {}\n\
yyc_socks_failed_total{{reason=\"busy\"}} {}\nyyc_socks_bytes_total{{direction=\"up\"}} {}\nyyc_socks_bytes_total{{direction=\"down\"}} {}\n\
yyc_socks_open_connections {}\nyyc_socks_probe_age_seconds{{check=\"dns\"}} {}\nyyc_socks_probe_age_seconds{{check=\"ipv6\"}} {}\nyyc_socks_probe_age_seconds{{check=\"ipv4\"}} {}\n",
        g(&c.accepted), g(&c.connected_v6), g(&c.connected_v4), g(&c.refused_address), g(&c.refused_threat), g(&c.refused_nxdomain),
        g(&c.dns_failed), g(&c.connect_failed), g(&c.bad_request), g(&c.busy), g(&c.bytes_up), g(&c.bytes_down),
        max - app.slots.available_permits(), age(h.dns), age(h.v6), age(h.v4),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ipv6_first_then_alternating() {
        let a: Vec<IpAddr> = ["1.1.1.1", "2606:4700::1", "1.0.0.1", "2606:4700::2", "2606:4700::3"].iter().map(|s| s.parse().unwrap()).collect();
        let o: Vec<String> = order(a).iter().map(|x| x.to_string()).collect();
        assert_eq!(o, ["2606:4700::1", "1.1.1.1", "2606:4700::2", "1.0.0.1", "2606:4700::3"]);
    }

    #[test]
    fn ipv4_only_drops_ipv6() {
        let a: Vec<IpAddr> = ["2606:4700::1", "1.1.1.1", "2606:4700::2"].iter().map(|s| s.parse().unwrap()).collect();
        assert_eq!(families(a.clone(), false).len(), 3);
        let o: Vec<String> = families(a, true).iter().map(|x| x.to_string()).collect();
        assert_eq!(o, ["1.1.1.1"]);
    }

    #[tokio::test]
    async fn happy_eyeballs_skips_a_dead_address() {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = l.local_addr().unwrap().port();
        tokio::spawn(async move { loop { let _ = l.accept().await; } });
        // a blackholed address first (TEST-NET-1 drops), the listener second: the stagger reaches it
        let addrs: Vec<IpAddr> = ["192.0.2.1", "127.0.0.1"].iter().map(|s| s.parse().unwrap()).collect();
        let t = Instant::now();
        let s = connect_happy(&addrs, port, Duration::from_secs(5)).await.unwrap();
        assert_eq!(s.peer_addr().unwrap().ip().to_string(), "127.0.0.1");
        assert!(t.elapsed() < Duration::from_secs(2));
    }

    #[tokio::test]
    async fn connect_refused_everywhere_fails_fast() {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = l.local_addr().unwrap().port();
        drop(l);
        let addrs: Vec<IpAddr> = vec!["127.0.0.1".parse().unwrap()];
        let t = Instant::now();
        assert!(connect_happy(&addrs, port, Duration::from_secs(5)).await.is_err());
        assert!(t.elapsed() < Duration::from_secs(1));
    }
}
