// The SOCKS5 wire format (RFC 1928, RFC 1929 for the username that picks the profile). CONNECT only: BIND and UDP
// ASSOCIATE answer "command not supported".
use std::fmt;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};

use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

use crate::resolve::{Profile, Refusal};

#[derive(Debug, Clone, PartialEq)]
pub enum Target {
    Name(String, u16),
    Ip(IpAddr, u16),
}

impl Target {
    pub fn port(&self) -> u16 {
        match self {
            Target::Name(_, p) | Target::Ip(_, p) => *p,
        }
    }
}

impl fmt::Display for Target {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Target::Name(n, p) => write!(f, "{n}:{p}"),
            Target::Ip(IpAddr::V6(a), p) => write!(f, "[{a}]:{p}"),
            Target::Ip(a, p) => write!(f, "{a}:{p}"),
        }
    }
}

#[derive(Debug)]
pub struct Request {
    pub target: Target,
    pub profile: Profile,
}

/// The reply codes (RFC 1928 section 6), and 0x80: the name is blocked as a security threat (unassigned by the RFC;
/// any SOCKS client reads it as a failure, YYC AI's egress reads it as deny:threat).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Reply {
    Succeeded = 0x00,
    GeneralFailure = 0x01,
    NotAllowed = 0x02,
    NetworkUnreachable = 0x03,
    HostUnreachable = 0x04,
    ConnectionRefused = 0x05,
    TtlExpired = 0x06,
    CommandNotSupported = 0x07,
    AddressTypeNotSupported = 0x08,
    Threat = 0x80,
}

impl Reply {
    pub fn from_connect(r: &Refusal) -> Reply {
        let Refusal::Connect(e) = r else { return Reply::GeneralFailure };
        match e.kind() {
            std::io::ErrorKind::ConnectionRefused => Reply::ConnectionRefused,
            std::io::ErrorKind::TimedOut => Reply::TtlExpired,
            std::io::ErrorKind::NetworkUnreachable => Reply::NetworkUnreachable,
            std::io::ErrorKind::HostUnreachable => Reply::HostUnreachable,
            _ => Reply::GeneralFailure,
        }
    }
}

#[derive(Debug)]
pub struct HandshakeError {
    pub message: String,
    /// what to answer before closing (None: the client didn't get far enough for a reply to make sense)
    pub reply: Option<Reply>,
}

fn err(message: impl Into<String>, reply: Option<Reply>) -> HandshakeError {
    HandshakeError { message: message.into(), reply }
}

/// Greeting, the optional username/password sub-negotiation, then the request.
pub async fn handshake<S: AsyncRead + AsyncWrite + Unpin>(s: &mut S) -> Result<Request, HandshakeError> {
    let io = |e: std::io::Error| err(e.to_string(), None);
    let mut head = [0u8; 2];
    s.read_exact(&mut head).await.map_err(io)?;
    if head[0] != 5 {
        return Err(err(format!("not SOCKS5 (version {})", head[0]), None));
    }
    let mut methods = vec![0u8; head[1] as usize];
    s.read_exact(&mut methods).await.map_err(io)?;
    // username/password when offered (it names the profile), else no authentication
    let profile = if methods.contains(&0x02) {
        s.write_all(&[5, 0x02]).await.map_err(io)?;
        let mut v = [0u8; 2];
        s.read_exact(&mut v).await.map_err(io)?;
        let mut user = vec![0u8; v[1] as usize];
        s.read_exact(&mut user).await.map_err(io)?;
        let mut plen = [0u8; 1];
        s.read_exact(&mut plen).await.map_err(io)?;
        let mut pass = vec![0u8; plen[0] as usize];
        s.read_exact(&mut pass).await.map_err(io)?;
        let name = String::from_utf8_lossy(&user).to_string();
        match Profile::from_name(&name) {
            Some(p) => {
                s.write_all(&[1, 0]).await.map_err(io)?;
                p
            }
            None => {
                let _ = s.write_all(&[1, 1]).await;
                return Err(err(format!("unknown profile {name:?}"), None));
            }
        }
    } else if methods.contains(&0x00) {
        s.write_all(&[5, 0x00]).await.map_err(io)?;
        Profile::Default
    } else {
        let _ = s.write_all(&[5, 0xff]).await;
        return Err(err("no acceptable method", None));
    };

    let mut req = [0u8; 4];
    s.read_exact(&mut req).await.map_err(io)?;
    if req[0] != 5 {
        return Err(err("bad request version", Some(Reply::GeneralFailure)));
    }
    if req[1] != 1 {
        return Err(err(format!("command {} not supported", req[1]), Some(Reply::CommandNotSupported)));
    }
    let target = match req[3] {
        1 => {
            let mut b = [0u8; 6];
            s.read_exact(&mut b).await.map_err(io)?;
            Target::Ip(IpAddr::V4(Ipv4Addr::new(b[0], b[1], b[2], b[3])), u16::from_be_bytes([b[4], b[5]]))
        }
        3 => {
            let mut len = [0u8; 1];
            s.read_exact(&mut len).await.map_err(io)?;
            let mut name = vec![0u8; len[0] as usize];
            s.read_exact(&mut name).await.map_err(io)?;
            let mut p = [0u8; 2];
            s.read_exact(&mut p).await.map_err(io)?;
            let name = String::from_utf8(name).map_err(|_| err("name is not UTF-8", Some(Reply::GeneralFailure)))?;
            let port = u16::from_be_bytes(p);
            // a literal sent as a name ("1.2.3.4", "[::1]") is an address like any other
            match name.trim_start_matches('[').trim_end_matches(']').parse::<IpAddr>() {
                Ok(ip) => Target::Ip(ip, port),
                Err(_) => {
                    if !valid_name(&name) {
                        return Err(err(format!("not a host name: {name:?}"), Some(Reply::HostUnreachable)));
                    }
                    Target::Name(name.to_ascii_lowercase(), port)
                }
            }
        }
        4 => {
            let mut b = [0u8; 18];
            s.read_exact(&mut b).await.map_err(io)?;
            let mut a = [0u8; 16];
            a.copy_from_slice(&b[..16]);
            Target::Ip(IpAddr::V6(Ipv6Addr::from(a)), u16::from_be_bytes([b[16], b[17]]))
        }
        t => return Err(err(format!("address type {t}"), Some(Reply::AddressTypeNotSupported))),
    };
    if target.port() == 0 {
        return Err(err("port 0", Some(Reply::NotAllowed)));
    }
    Ok(Request { target, profile })
}

/// A DNS name: labels of letters, digits, '-' and '_' (seen in the wild), 1-63 each, 253 in all, a trailing dot allowed.
fn valid_name(n: &str) -> bool {
    let n = n.strip_suffix('.').unwrap_or(n);
    !n.is_empty()
        && n.len() <= 253
        && n.split('.').all(|l| !l.is_empty() && l.len() <= 63 && l.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_'))
}

pub async fn reply<S: AsyncWrite + Unpin>(s: &mut S, code: Reply, bound: Option<SocketAddr>) -> std::io::Result<()> {
    let mut out = vec![5, code as u8, 0];
    match bound {
        Some(SocketAddr::V4(a)) => {
            out.push(1);
            out.extend_from_slice(&a.ip().octets());
            out.extend_from_slice(&a.port().to_be_bytes());
        }
        Some(SocketAddr::V6(a)) => {
            out.push(4);
            out.extend_from_slice(&a.ip().octets());
            out.extend_from_slice(&a.port().to_be_bytes());
        }
        None => out.extend_from_slice(&[1, 0, 0, 0, 0, 0, 0]),
    }
    s.write_all(&out).await
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn run(input: Vec<u8>) -> (Result<Request, HandshakeError>, Vec<u8>) {
        let (mut client, mut server) = tokio::io::duplex(1024);
        let t = tokio::spawn(async move {
            client.write_all(&input).await.unwrap();
            client.shutdown().await.unwrap();
            let mut out = Vec::new();
            client.read_to_end(&mut out).await.unwrap();
            out
        });
        let r = handshake(&mut server).await;
        drop(server);
        (r, t.await.unwrap())
    }

    #[tokio::test]
    async fn domain_name_no_auth() {
        let mut m = vec![5, 1, 0, 5, 1, 0, 3, 10];
        m.extend_from_slice(b"api.exa.ai");
        m.extend_from_slice(&443u16.to_be_bytes());
        let (r, out) = run(m).await;
        let r = r.unwrap();
        assert_eq!(r.target, Target::Name("api.exa.ai".into(), 443));
        assert_eq!(r.profile, Profile::Default);
        assert_eq!(out, vec![5, 0]);
    }

    #[tokio::test]
    async fn username_picks_the_sandbox_profile() {
        let mut m = vec![5, 2, 0, 2, 1, 7];
        m.extend_from_slice(b"sandbox");
        m.push(0);
        m.extend_from_slice(&[5, 1, 0, 3, 11]);
        m.extend_from_slice(b"example.org");
        m.extend_from_slice(&80u16.to_be_bytes());
        let (r, out) = run(m).await;
        assert_eq!(r.unwrap().profile, Profile::Sandbox);
        assert_eq!(out, vec![5, 2, 1, 0]);
    }

    #[tokio::test]
    async fn unknown_profile_is_refused() {
        let mut m = vec![5, 1, 2, 1, 4];
        m.extend_from_slice(b"root");
        m.push(0);
        let (r, out) = run(m).await;
        assert!(r.is_err());
        assert_eq!(out, vec![5, 2, 1, 1]);
    }

    #[tokio::test]
    async fn ipv6_literal_and_literal_as_name() {
        let mut m = vec![5, 1, 0, 5, 1, 0, 4];
        m.extend_from_slice(&"2606:4700::1".parse::<Ipv6Addr>().unwrap().octets());
        m.extend_from_slice(&443u16.to_be_bytes());
        assert_eq!(run(m).await.0.unwrap().target, Target::Ip("2606:4700::1".parse().unwrap(), 443));
        let mut m = vec![5, 1, 0, 5, 1, 0, 3, 9];
        m.extend_from_slice(b"127.0.0.1");
        m.extend_from_slice(&22u16.to_be_bytes());
        assert_eq!(run(m).await.0.unwrap().target, Target::Ip("127.0.0.1".parse().unwrap(), 22));
    }

    #[tokio::test]
    async fn udp_associate_not_supported() {
        let m = vec![5, 1, 0, 5, 3, 0, 1, 0, 0, 0, 0, 0, 0];
        let (r, _) = run(m).await;
        assert_eq!(r.unwrap_err().reply, Some(Reply::CommandNotSupported));
    }

    #[test]
    fn names() {
        assert!(valid_name("geo.iproyal.com"));
        assert!(valid_name("_dmarc.example.com."));
        assert!(!valid_name("a..b"));
        assert!(!valid_name("evil host"));
        assert!(!valid_name(""));
    }
}
