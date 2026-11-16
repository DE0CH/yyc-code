// Which addresses a connection may go to: public unicast only. Everything inside (pods, Services, nodes' private
// network, the metadata service, loopback) is reached directly, never through the proxy, so the proxy refuses it;
// and the node's own public addresses, which the Hetzner firewall doesn't stand in front of (from the node itself).
use std::collections::HashSet;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};
use std::sync::RwLock;

use ipnet::IpNet;

/// Special-purpose ranges (IANA's IPv4 and IPv6 special-purpose registries) that are never a site on the internet.
const NOT_PUBLIC: &[&str] = &[
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24",
    "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4",
    "::/96", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "2001::/23", "2001:db8::/32",
    "2002::/16", "3fff::/20", "5f00::/16", "fc00::/7", "fe80::/10", "ff00::/8",
];

pub struct Policy {
    deny: Vec<IpNet>,
    local: RwLock<HashSet<IpAddr>>,
}

impl Policy {
    /// `extra`: more ranges to refuse, comma-separated (DENY_CIDRS).
    pub fn new(extra: &str) -> Policy {
        let mut deny: Vec<IpNet> = NOT_PUBLIC.iter().map(|s| s.parse().unwrap()).collect();
        for s in extra.split(',').map(str::trim).filter(|s| !s.is_empty()) {
            deny.push(s.parse().unwrap_or_else(|e| panic!("DENY_CIDRS {s:?}: {e}")));
        }
        Policy { deny, local: RwLock::new(HashSet::new()) }
    }

    pub fn allowed(&self, ip: IpAddr) -> bool {
        // an IPv4-mapped or -compatible IPv6 address is judged as the IPv4 address it carries
        let ip = match ip {
            IpAddr::V6(v6) => v6.to_ipv4_mapped().map(IpAddr::V4).unwrap_or(IpAddr::V6(v6)),
            v4 => v4,
        };
        if self.deny.iter().any(|n| n.contains(&ip)) {
            return false;
        }
        !self.local.read().unwrap().contains(&ip)
    }

    /// The node's own addresses (the pods run on the host network): IPv6 from /proc/net/if_inet6; IPv4 ones are all
    /// in private ranges here, but read too for a node that ever has a public one.
    pub fn refresh_local(&self) {
        let mut set = HashSet::new();
        if let Ok(s) = std::fs::read_to_string("/proc/net/if_inet6") {
            for line in s.lines() {
                if let Some(hex) = line.split_whitespace().next()
                    && let Ok(v) = u128::from_str_radix(hex, 16) {
                        set.insert(IpAddr::V6(Ipv6Addr::from(v)));
                    }
            }
        }
        if let Ok(s) = std::fs::read_to_string("/proc/net/fib_trie") {
            // "/32 host LOCAL" lines follow the address they describe
            let mut last: Option<Ipv4Addr> = None;
            for line in s.lines() {
                let t = line.trim();
                if let Some(a) = t.strip_prefix("|-- ") {
                    last = a.parse().ok();
                } else if t.contains("host LOCAL")
                    && let Some(a) = last {
                        set.insert(IpAddr::V4(a));
                    }
            }
        }
        *self.local.write().unwrap() = set;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok(p: &Policy, s: &str) -> bool {
        p.allowed(s.parse().unwrap())
    }

    #[test]
    fn public_only() {
        let p = Policy::new("");
        for a in ["1.1.1.1", "172.66.43.165", "2606:4700:3108::ac42:2ba5", "2a01:4f8:1c1e::1"] {
            assert!(ok(&p, a), "{a}");
        }
        for a in [
            "10.0.0.3", "10.244.4.74", "10.45.0.10", "127.0.0.1", "169.254.169.254", "169.254.20.10", "172.16.0.2",
            "192.168.1.1", "100.100.1.1", "0.0.0.0", "224.0.0.1", "::1", "::", "fe80::1", "fd00::1", "ff02::1",
            "::ffff:10.0.0.3", "::ffff:127.0.0.1", "2001:db8::1", "64:ff9b::a00:3", "2002:a00:3::1",
        ] {
            assert!(!ok(&p, a), "{a}");
        }
        // a mapped public address is the public address
        assert!(ok(&p, "::ffff:1.1.1.1"));
    }

    #[test]
    fn extra_ranges_and_local_addresses() {
        let p = Policy::new("2a01:4f8:c013::/48, 203.0.114.0/24");
        assert!(!ok(&p, "2a01:4f8:c013:e0b5::1"));
        assert!(!ok(&p, "203.0.114.9"));
        p.local.write().unwrap().insert("2a01:4f8:1c1e:aaaa::1".parse().unwrap());
        assert!(!ok(&p, "2a01:4f8:1c1e:aaaa::1"));
        assert!(ok(&p, "2a01:4f8:1c1e:aaaa::2"));
    }

    #[test]
    fn reads_this_machines_addresses() {
        let p = Policy::new("");
        p.refresh_local();
        assert!(p.local.read().unwrap().contains(&"::1".parse::<IpAddr>().unwrap()) || !std::path::Path::new("/proc/net/if_inet6").exists());
    }
}
