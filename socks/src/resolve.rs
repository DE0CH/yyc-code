// Names to addresses, per profile. Each profile has its own resolver and cache (hickory-resolver):
//   default: the node's resolver (DEFAULT_DNS=system reads /etc/resolv.conf: kubelet hands a host-network pod the
//            node's /etc/k8s-resolv.conf, i.e. the node's systemd-resolved, which asks Hetzner's resolvers over IPv6;
//            cluster/node-dns.sh), or a comma-separated list of servers;
//   sandbox: malware-blocking resolvers over IPv6 (YYC AI's sandboxes, through its egress): Cloudflare for Families
//            "security" (a blocked name answers 0.0.0.0 / ::) and Quad9 (a blocked name answers NXDOMAIN).
// Both ask for A and AAAA together.
use std::fmt;
use std::net::IpAddr;
use std::time::Duration;

use hickory_resolver::config::{LookupIpStrategy, NameServerConfig, ResolverConfig, ResolverOpts, ServerOrderingStrategy};
use hickory_resolver::net::runtime::TokioRuntimeProvider;
use hickory_resolver::net::{DnsError, NetError};
use hickory_resolver::proto::op::ResponseCode;
use hickory_resolver::TokioResolver;

pub const SANDBOX_DNS: &str = "2606:4700:4700::1112,2606:4700:4700::1002,2620:fe::fe,2620:fe::9";

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Profile {
    Default,
    Sandbox,
}

impl Profile {
    pub fn from_name(n: &str) -> Option<Profile> {
        match n {
            "" | "default" => Some(Profile::Default),
            "sandbox" => Some(Profile::Sandbox),
            _ => None,
        }
    }
    pub fn name(&self) -> &'static str {
        match self {
            Profile::Default => "default",
            Profile::Sandbox => "sandbox",
        }
    }
}

/// Why a connection was not made.
#[derive(Debug)]
pub enum Refusal {
    /// only non-public addresses (or an address the policy refuses)
    Address(String),
    /// the sandbox resolvers block the name as malware or phishing
    Threat,
    /// no such name, or a name with no address
    NoSuchName,
    /// no resolver answered
    Dns(String),
    Connect(std::io::Error),
}

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Refusal::Address(s) => write!(f, "refused: {s}"),
            Refusal::Threat => write!(f, "refused: blocked as a security threat"),
            Refusal::NoSuchName => write!(f, "no such name"),
            Refusal::Dns(s) => write!(f, "DNS failed: {s}"),
            Refusal::Connect(e) => write!(f, "connect failed: {e}"),
        }
    }
}

pub struct Resolvers {
    default: TokioResolver,
    sandbox: TokioResolver,
}

fn opts() -> ResolverOpts {
    let mut o = ResolverOpts::default();
    o.ip_strategy = LookupIpStrategy::Ipv6AndIpv4;
    o.timeout = Duration::from_secs(2);
    o.attempts = 2;
    o.cache_size = 16_384;
    o.ndots = 0;
    o
}

/// `in_order`: ask the servers in the list's order (the sandbox list: Cloudflare for Families first, whose block answer
/// 0.0.0.0 is a Threat, Quad9 only when it fails), never by measured latency: Quad9 answers some names Cloudflare
/// blocks (malware.testcategory.com, 2026-11-05), and on yyc-talos's IPv4 path it was picked first.
fn from_list(list: &str, in_order: bool) -> TokioResolver {
    let servers: Vec<NameServerConfig> = list
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| NameServerConfig::udp_and_tcp(s.parse::<IpAddr>().unwrap_or_else(|e| panic!("DNS server {s:?}: {e}"))))
        .collect();
    assert!(!servers.is_empty(), "no DNS servers in {list:?}");
    TokioResolver::builder_with_config(ResolverConfig::from_name_servers(servers), TokioRuntimeProvider::default())
        .with_options({
            let mut o = opts();
            if in_order {
                o.server_ordering_strategy = ServerOrderingStrategy::UserProvidedOrder;
            }
            o
        })
        .build()
        .expect("resolver")
}

impl Resolvers {
    pub fn new(default: &str, sandbox: &str) -> Resolvers {
        let default = if default == "system" {
            let mut b = TokioResolver::builder_tokio().expect("/etc/resolv.conf");
            *b.options_mut() = opts();
            b.build().expect("resolver")
        } else {
            from_list(default, false)
        };
        Resolvers { default, sandbox: from_list(sandbox, true) }
    }

    pub async fn resolve(&self, name: &str, profile: Profile) -> Result<Vec<IpAddr>, Refusal> {
        let r = match profile {
            Profile::Default => &self.default,
            Profile::Sandbox => &self.sandbox,
        };
        // absolute: no search domains, whatever resolv.conf says
        let fqdn = if name.ends_with('.') { name.to_string() } else { format!("{name}.") };
        match r.lookup_ip(fqdn.as_str()).await {
            Ok(l) => {
                let addrs: Vec<IpAddr> = l.iter().collect();
                if profile == Profile::Sandbox && addrs.iter().any(|a| a.is_unspecified()) {
                    return Err(Refusal::Threat);
                }
                if addrs.is_empty() {
                    return Err(Refusal::NoSuchName);
                }
                Ok(addrs)
            }
            Err(e) => Err(classify(e)),
        }
    }
}

fn classify(e: NetError) -> Refusal {
    match &e {
        NetError::Dns(DnsError::NoRecordsFound(nr)) if matches!(nr.response_code, ResponseCode::NXDomain | ResponseCode::NoError) => Refusal::NoSuchName,
        _ => Refusal::Dns(e.to_string()),
    }
}
