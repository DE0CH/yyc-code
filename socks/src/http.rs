// The same way out for clients that speak HTTP proxy rather than SOCKS5 (urllib, Node's env proxy, yt-dlp, ffmpeg,
// pip): on the same port, told apart by the first byte. `CONNECT host:port` tunnels (https), an absolute-form request
// (`GET http://host/path`) is sent on with its request line in origin form and `Connection: close`. The profile comes
// from `Proxy-Authorization: Basic` (the username), as with SOCKS5. Refusals answer with a status and the reason in
// `X-Yyc-Socks-Refusal`.
use std::net::IpAddr;

use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

use crate::resolve::{Profile, Refusal};
use crate::socks::Target;

pub struct HttpRequest {
    pub target: Target,
    pub profile: Profile,
    /// CONNECT: nothing; a plain request: the bytes to send upstream first (rewritten head + any body bytes read)
    pub forward: Option<Vec<u8>>,
}

pub struct HttpError {
    pub status: u16,
    pub message: String,
}

fn bad(status: u16, message: impl Into<String>) -> HttpError {
    HttpError { status, message: message.into() }
}

const MAX_HEAD: usize = 64 * 1024;

/// Reads the request head (`first` = the byte already read to tell the protocols apart).
pub async fn read_request<S: AsyncRead + Unpin>(s: &mut S, first: u8) -> Result<HttpRequest, HttpError> {
    let mut buf = vec![first];
    let mut chunk = [0u8; 4096];
    let head_end = loop {
        if let Some(i) = find(&buf, b"\r\n\r\n") {
            break i + 4;
        }
        if buf.len() > MAX_HEAD {
            return Err(bad(431, "request head too large"));
        }
        let n = s.read(&mut chunk).await.map_err(|e| bad(400, e.to_string()))?;
        if n == 0 {
            return Err(bad(400, "closed before the end of the request head"));
        }
        buf.extend_from_slice(&chunk[..n]);
    };
    let head = String::from_utf8_lossy(&buf[..head_end]).to_string();
    let rest = buf[head_end..].to_vec();
    let mut lines = head.split("\r\n");
    let request_line = lines.next().unwrap_or("");
    let mut parts = request_line.split(' ');
    let (method, uri, version) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""), parts.next().unwrap_or(""));
    if !version.starts_with("HTTP/1.") {
        return Err(bad(400, format!("not an HTTP/1 request: {request_line:?}")));
    }
    let headers: Vec<(String, String)> = lines
        .filter(|l| !l.is_empty())
        .filter_map(|l| l.split_once(':').map(|(k, v)| (k.trim().to_string(), v.trim().to_string())))
        .collect();
    let profile = profile_of(&headers)?;

    if method.eq_ignore_ascii_case("CONNECT") {
        let target = authority(uri, None)?;
        return Ok(HttpRequest { target, profile, forward: None });
    }
    // absolute form: http://host[:port]/path
    let Some(after) = uri.strip_prefix("http://") else {
        return Err(bad(400, format!("a proxy request needs CONNECT or an absolute http:// URI, got {uri:?}")));
    };
    let (auth, path) = match after.find('/') {
        Some(i) => (&after[..i], &after[i..]),
        None => (after, "/"),
    };
    let target = authority(auth, Some(80))?;
    let mut out = format!("{method} {path} {version}\r\n");
    for (k, v) in &headers {
        let lk = k.to_ascii_lowercase();
        if lk.starts_with("proxy-") || lk == "connection" || lk == "keep-alive" {
            continue;
        }
        out.push_str(&format!("{k}: {v}\r\n"));
    }
    if !headers.iter().any(|(k, _)| k.eq_ignore_ascii_case("host")) {
        out.push_str(&format!("Host: {auth}\r\n"));
    }
    // one request per connection: a client's next request may be for another host
    out.push_str("Connection: close\r\n\r\n");
    let mut forward = out.into_bytes();
    forward.extend_from_slice(&rest);
    Ok(HttpRequest { target, profile, forward: Some(forward) })
}

fn profile_of(headers: &[(String, String)]) -> Result<Profile, HttpError> {
    let Some((_, v)) = headers.iter().find(|(k, _)| k.eq_ignore_ascii_case("proxy-authorization")) else {
        return Ok(Profile::Default);
    };
    let b64 = v.strip_prefix("Basic ").or_else(|| v.strip_prefix("basic ")).ok_or_else(|| bad(407, "Proxy-Authorization must be Basic"))?;
    let raw = base64_decode(b64.trim()).ok_or_else(|| bad(407, "Proxy-Authorization is not base64"))?;
    let raw = String::from_utf8_lossy(&raw).to_string();
    let user = raw.split(':').next().unwrap_or("");
    Profile::from_name(user).ok_or_else(|| bad(407, format!("unknown profile {user:?}")))
}

/// host:port, [v6]:port, or a bare host with the scheme's default port
fn authority(a: &str, default_port: Option<u16>) -> Result<Target, HttpError> {
    let (host, port) = if let Some(rest) = a.strip_prefix('[') {
        let (h, tail) = rest.split_once(']').ok_or_else(|| bad(400, format!("bad authority {a:?}")))?;
        (h.to_string(), tail.strip_prefix(':').map(|p| p.to_string()))
    } else {
        match a.rsplit_once(':') {
            Some((h, p)) => (h.to_string(), Some(p.to_string())),
            None => (a.to_string(), None),
        }
    };
    let port: u16 = match (port, default_port) {
        (Some(p), _) => p.parse().map_err(|_| bad(400, format!("bad port in {a:?}")))?,
        (None, Some(d)) => d,
        (None, None) => return Err(bad(400, format!("CONNECT needs host:port, got {a:?}"))),
    };
    if port == 0 || host.is_empty() {
        return Err(bad(400, format!("bad authority {a:?}")));
    }
    Ok(match host.parse::<IpAddr>() {
        Ok(ip) => Target::Ip(ip, port),
        Err(_) => Target::Name(host.to_ascii_lowercase(), port),
    })
}

pub async fn established<S: AsyncWrite + Unpin>(s: &mut S) -> std::io::Result<()> {
    s.write_all(b"HTTP/1.1 200 Connection established\r\n\r\n").await
}

pub async fn refuse<S: AsyncWrite + Unpin>(s: &mut S, r: &Refusal) -> std::io::Result<()> {
    let (status, reason, tag) = match r {
        Refusal::Address(_) => (403, "Forbidden", "address"),
        Refusal::Threat => (403, "Forbidden", "threat"),
        Refusal::NoSuchName => (502, "Bad Gateway", "nxdomain"),
        Refusal::Dns(_) => (502, "Bad Gateway", "dns"),
        Refusal::Connect(e) if e.kind() == std::io::ErrorKind::TimedOut => (504, "Gateway Timeout", "connect"),
        Refusal::Connect(_) => (502, "Bad Gateway", "connect"),
    };
    error(s, status, reason, tag, &r.to_string()).await
}

pub async fn error<S: AsyncWrite + Unpin>(s: &mut S, status: u16, reason: &str, tag: &str, message: &str) -> std::io::Result<()> {
    let body = format!("{message}\n");
    let resp = format!(
        "HTTP/1.1 {status} {reason}\r\nX-Yyc-Socks-Refusal: {tag}\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    s.write_all(resp.as_bytes()).await
}

fn find(h: &[u8], n: &[u8]) -> Option<usize> {
    h.windows(n.len()).position(|w| w == n)
}

fn base64_decode(s: &str) -> Option<Vec<u8>> {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = Vec::new();
    let (mut acc, mut bits) = (0u32, 0u32);
    for c in s.bytes().filter(|c| *c != b'=') {
        let v = T.iter().position(|x| *x == c)? as u32;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn parse(raw: &str) -> Result<HttpRequest, HttpError> {
        let b = raw.as_bytes();
        let mut r = &b[1..];
        read_request(&mut r, b[0]).await
    }

    #[tokio::test]
    async fn connect() {
        let r = parse("CONNECT api.exa.ai:443 HTTP/1.1\r\nHost: api.exa.ai:443\r\n\r\n").await.ok().unwrap();
        assert_eq!(r.target, Target::Name("api.exa.ai".into(), 443));
        assert!(r.forward.is_none());
        let r = parse("CONNECT [2606:4700::1]:443 HTTP/1.1\r\n\r\n").await.ok().unwrap();
        assert_eq!(r.target, Target::Ip("2606:4700::1".parse().unwrap(), 443));
    }

    #[tokio::test]
    async fn absolute_form_is_rewritten() {
        let r = parse("GET http://rthk.hk/news?x=1 HTTP/1.1\r\nHost: rthk.hk\r\nProxy-Connection: keep-alive\r\nConnection: keep-alive\r\nAccept: */*\r\n\r\nBODY").await.ok().unwrap();
        assert_eq!(r.target, Target::Name("rthk.hk".into(), 80));
        let f = String::from_utf8(r.forward.unwrap()).unwrap();
        assert_eq!(f, "GET /news?x=1 HTTP/1.1\r\nHost: rthk.hk\r\nAccept: */*\r\nConnection: close\r\n\r\nBODY");
    }

    #[tokio::test]
    async fn profile_from_basic_auth() {
        // sandbox:x
        let r = parse("CONNECT example.org:443 HTTP/1.1\r\nProxy-Authorization: Basic c2FuZGJveDp4\r\n\r\n").await.ok().unwrap();
        assert_eq!(r.profile, Profile::Sandbox);
        // root:x
        assert_eq!(parse("CONNECT example.org:443 HTTP/1.1\r\nProxy-Authorization: Basic cm9vdDp4\r\n\r\n").await.err().unwrap().status, 407);
    }

    #[tokio::test]
    async fn refusals() {
        assert_eq!(parse("CONNECT example.org HTTP/1.1\r\n\r\n").await.err().unwrap().status, 400);
        assert_eq!(parse("GET /x HTTP/1.1\r\n\r\n").await.err().unwrap().status, 400);
    }
}
