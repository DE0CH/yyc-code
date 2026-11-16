//! The CI replica's stand-in for tags.yyc-internal.com (nametags/README.md), std only.
//!
//! Tokens (header `cf-access-token`): `test-token` = signed in, printer online; `offline-token` = signed in, printer
//! offline; anything else = an expired login (302 to an HTML login page, like Cloudflare Access).
//! `POST /api/print` checks the PNG is 1-bit greyscale 848x590 and saves it to OUT_DIR; a job goes queued, queued,
//! printing, printing, then done (or failed "ribbon" for guest g5) on successive polls.
//! Events (`GET /api/events`): `surgical-futures-forum` (the default: `GET /api/guests` without `event`, guests g1–g6)
//! and `yyc-health-summit` (g7, g8); a guest list and a check-in carry their event as an object, like the Worker. `GET /api/checkin?code=C`: `CODE_ACCEPTED` → g2, `CODE_PENDING` → 409, anything
//! else → 404, with the Worker's error texts.
//! Usage: nametags-stub PORT OUT_DIR

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};

const GUESTS: &str = r#"{"updated":"2026-10-29T08:00:00Z","guests":[
{"id":"g1","name":"Dr Jing HUANG","institution":"Ministry of Service and Technology","salutation":"Dr","surname":"HUANG","firstname":"Jing","title":"Director","email":"jing.huang@example.org","mobile":"+852 5555 0101","status":"Sent","table":null},
{"id":"g2","name":"Ms Mei Ling WONG","institution":"Hong Kong Science Park","salutation":"Ms","surname":"WONG","firstname":"Mei Ling","title":"Head of Partnerships","email":"ml.wong@example.org","mobile":"+852 5555 0102","status":"Sent","table":12},
{"id":"g3","name":"Professor Alexandria Montgomery-Fitzwilliam","institution":"Barking, Havering and Redbridge University Hospitals NHS Trust","salutation":"Professor","surname":"MONTGOMERY-FITZWILLIAM","firstname":"Alexandria","title":"Consultant Cardiologist and Clinical Lead for Research","email":"a.mf@example.org","mobile":"+44 7700 900123","status":"Sent","table":null},
{"id":"g4","name":"Mr Ki Cheung Otto KWOK","institution":"Canossa Hospital","salutation":"Mr","surname":"KWOK","firstname":"Ki Cheung Otto","title":"Chief Executive","email":"otto.kwok@example.org","mobile":"+852 5555 0104","status":"Withdrawn","table":null},
{"id":"g5","name":"Mr Tai Man CHAN","institution":"The University of Hong Kong","salutation":"Mr","surname":"CHAN","firstname":"Tai Man","title":"Research Fellow","email":"tm.chan@example.org","mobile":"+852 5555 0105","status":"Sent","table":null},
{"id":"g6","name":"陳大文","institution":"香港大學 中國商業學院","salutation":null,"surname":"陳","firstname":"大文","title":"教授","email":"dw.chan@example.org","mobile":"+852 5555 0106","status":null,"table":"3"}]}"#;


const GUESTS_SUMMIT: &str = r#"{"updated":"2026-10-30T08:00:00Z","guests":[
{"id":"g7","name":"Dr Siu Ming LEE","institution":"Queen Mary Hospital","salutation":"Dr","surname":"LEE","firstname":"Siu Ming","title":null,"email":"sm.lee@example.org","mobile":"+852 5555 0107","status":null,"table":null,"event":"yyc-health-summit","source":"form","state":"accepted","mergedInto":null,"created":"2026-10-30T07:00:00Z","code":"Lk4pW8sN1qRt6vYb3zXc9d","dup":{"state":"none","candidates":[]}},
{"id":"g8","name":"李嘉欣","institution":"香港中文大學醫學院","salutation":null,"surname":null,"firstname":null,"title":null,"email":"kayan.li@example.org","mobile":"+852 5555 0108","status":null,"table":null,"event":"yyc-health-summit","source":"form","state":"accepted","mergedInto":null,"created":"2026-10-30T07:05:00Z","code":"Mf7gH2jK5lZx8cVb1nQw4e","dup":{"state":"cleared","candidates":[]}}]}"#;

const EVENT_FORUM: &str = r#"{"id":"surgical-futures-forum","name":"Surgical Futures Forum","nameEn":null,"date":"2026-11-20","venue":"Hong Kong Science Park","mode":"review","open":true,"created":"2026-10-29T08:00:00Z"}"#;
const EVENT_SUMMIT: &str = r#"{"id":"yyc-health-summit","name":"YYC 醫療創新峰會","nameEn":"YYC Health Innovation Summit","date":"2026-12-05","venue":null,"mode":"auto","open":true,"created":"2026-10-30T07:00:00Z"}"#;

/// `GET /api/events`: each event with the staff page's counts.
fn events() -> String {
    let counts = |c: &str| format!(r#","counts":{c}}}"#);
    format!(
        r#"{{"events":[{}{},{}{}]}}"#,
        &EVENT_FORUM[..EVENT_FORUM.len() - 1],
        counts(r#"{"pending":1,"flagged":0,"accepted":6,"rejected":0,"outbox":6}"#),
        &EVENT_SUMMIT[..EVENT_SUMMIT.len() - 1],
        counts(r#"{"pending":0,"flagged":0,"accepted":2,"rejected":0,"outbox":2}"#),
    )
}

/// The QR code of an accepted registration (guest g2, the default event).
const CODE_ACCEPTED: &str = "Vq3xT9bL2mPa7sKd4nWc1e";
/// A registration still waiting for staff.
const CODE_PENDING: &str = "Pn8dR2hY6tGk0wQz5cXa3f";

const CHECKIN_GUEST: &str = r#"{"id":"g2","name":"Ms Mei Ling WONG","institution":"Hong Kong Science Park","salutation":"Ms","surname":"WONG","firstname":"Mei Ling","title":"Head of Partnerships","email":"ml.wong@example.org","mobile":"+852 5555 0102","status":"Sent","table":12,"event":"surgical-futures-forum","source":"import","state":"accepted","mergedInto":null,"created":"2026-10-29T08:00:00Z","code":"Vq3xT9bL2mPa7sKd4nWc1e","dup":{"state":"none","candidates":[]}}"#;

type Resp = (u16, &'static str, Vec<(String, String)>, Vec<u8>);

struct Job {
    guest: String,
    polls: u32,
}

struct State {
    out: String,
    jobs: HashMap<String, Job>,
    next: u32,
}

struct Req {
    method: String,
    path: String,
    query: HashMap<String, String>,
    headers: HashMap<String, String>,
    body: Vec<u8>,
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let port = args.get(1).map(String::as_str).unwrap_or("8787");
    let out = args.get(2).cloned().unwrap_or_else(|| ".".into());
    std::fs::create_dir_all(&out).expect("out dir");
    let state = Arc::new(Mutex::new(State { out, jobs: HashMap::new(), next: 1 }));
    let listener = TcpListener::bind(format!("127.0.0.1:{port}")).expect("bind");
    eprintln!("stub listening on 127.0.0.1:{port}");
    for stream in listener.incoming().flatten() {
        let st = state.clone();
        std::thread::spawn(move || {
            if let Err(e) = handle(stream, &st) {
                eprintln!("connection error: {e}");
            }
        });
    }
}

fn handle(mut stream: TcpStream, st: &Mutex<State>) -> std::io::Result<()> {
    let req = match read_req(&stream)? {
        Some(r) => r,
        None => return Ok(()),
    };
    let resp = route(&req, st)?;
    let (code, ctype, extra, body) = resp;
    let reason = match code {
        200 => "OK",
        302 => "Found",
        400 => "Bad Request",
        409 => "Conflict",
        _ => "Not Found",
    };
    let mut head = format!("HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nConnection: close\r\n", body.len());
    for (k, v) in extra {
        head.push_str(&format!("{k}: {v}\r\n"));
    }
    head.push_str("\r\n");
    stream.write_all(head.as_bytes())?;
    stream.write_all(&body)?;
    stream.flush()
}

fn route(req: &Req, st: &Mutex<State>) -> std::io::Result<Resp> {
    let token = req.headers.get("cf-access-token").cloned().unwrap_or_default();
    eprintln!("{} {} token={} body={}B", req.method, req.path, if token.is_empty() { "-" } else { &token }, req.body.len());
    let signed_in = token == "test-token" || token == "offline-token";
    let p = req.path.as_str();
    let resp = if p == "/api/app-signin" {
        let state = req.query.get("state").cloned().unwrap_or_default();
        let ok = (16..=128).contains(&state.len())
            && state.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
        if ok {
            let loc = format!("dev.de0ch.yycnametags://signed-in?state={state}#token=test-token");
            (302, "text/plain", vec![("Location".to_string(), loc)], b"signed in".to_vec())
        } else {
            json(400, r#"{"error":"bad state"}"#)
        }
    } else if p == "/cdn-cgi/access/login" {
        (200, "text/html; charset=utf-8", vec![], b"<html><body>Sign in with Cloudflare Access</body></html>".to_vec())
    } else if p.starts_with("/api/") && !signed_in {
        (302, "text/plain", vec![("Location".to_string(), "/cdn-cgi/access/login".to_string())], vec![])
    } else if p == "/api/guests" && req.method == "GET" {
        // Without `event`: the default event, named in the response (`event`), as old builds expect the same shape.
        match req.query.get("event").map(String::as_str).unwrap_or("surgical-futures-forum") {
            "surgical-futures-forum" => json(200, &with_event(GUESTS, EVENT_FORUM)),
            "yyc-health-summit" => json(200, &with_event(GUESTS_SUMMIT, EVENT_SUMMIT)),
            _ => json(404, r#"{"error":"找不到此活動"}"#),
        }
    } else if p == "/api/events" && req.method == "GET" {
        json(200, &events())
    } else if p == "/api/checkin" && req.method == "GET" {
        match req.query.get("code").map(String::as_str).unwrap_or("") {
            CODE_ACCEPTED => json(200, &format!(r#"{{"guest":{CHECKIN_GUEST},"event":{EVENT_FORUM}}}"#)),
            CODE_PENDING => json(409, r#"{"error":"此報名未確認"}"#),
            _ => json(404, r#"{"error":"找不到此二維碼"}"#),
        }
    } else if p == "/api/printer" {
        if token == "offline-token" {
            json(200, r#"{"online":false,"lastSeen":"2026-10-29T07:00:00Z","printer":"M3","error":null}"#)
        } else {
            json(200, r#"{"online":true,"lastSeen":"2026-10-29T08:00:00Z","printer":"M3","error":null}"#)
        }
    } else if p == "/api/print" && req.method == "POST" {
        let guest = req.query.get("guest").cloned().unwrap_or_default();
        match check_png(&req.body) {
            Err(e) => {
                eprintln!("rejected print for {guest}: {e}");
                json(400, &format!(r#"{{"error":"{e}"}}"#))
            }
            Ok(()) => {
                let mut s = st.lock().unwrap();
                let id = format!("job{}", s.next);
                s.next += 1;
                let file = format!("{}/print-{}-{}.png", s.out, id, guest);
                std::fs::write(&file, &req.body)?;
                eprintln!("saved {file}");
                s.jobs.insert(id.clone(), Job { guest, polls: 0 });
                json(200, &format!(r#"{{"id":"{id}"}}"#))
            }
        }
    } else if let Some(id) = p.strip_prefix("/api/print/") {
        let mut s = st.lock().unwrap();
        match s.jobs.get_mut(id) {
            None => json(404, r#"{"error":"no such job"}"#),
            Some(j) => {
                j.polls += 1;
                let (status, err) = match j.polls {
                    0..=2 => ("queued", "null"),
                    3..=4 => ("printing", "null"),
                    _ if j.guest == "g5" => ("failed", "\"ribbon\""),
                    _ => ("done", "null"),
                };
                json(200, &format!(r#"{{"id":"{id}","status":"{status}","error":{err}}}"#))
            }
        }
    } else {
        json(404, r#"{"error":"not found"}"#)
    };
    Ok(resp)
}

/// `{"updated":…,"guests":[…]}` with the event (an Event object, as the Worker sends it) added.
fn with_event(list: &str, event: &str) -> String {
    format!(r#"{{"event":{event},{}"#, &list[1..])
}

fn json(code: u16, body: &str) -> Resp {
    (code, "application/json", vec![], body.as_bytes().to_vec())
}

fn read_req(stream: &TcpStream) -> std::io::Result<Option<Req>> {
    let mut r = BufReader::new(stream);
    let mut line = String::new();
    if r.read_line(&mut line)? == 0 {
        return Ok(None);
    }
    let mut parts = line.split_whitespace();
    let method = parts.next().unwrap_or("").to_string();
    let target = parts.next().unwrap_or("/").to_string();
    let mut headers = HashMap::new();
    loop {
        let mut h = String::new();
        if r.read_line(&mut h)? == 0 || h == "\r\n" || h == "\n" {
            break;
        }
        if let Some((k, v)) = h.split_once(':') {
            headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
        }
    }
    let len: usize = headers.get("content-length").and_then(|v| v.parse().ok()).unwrap_or(0);
    let mut body = vec![0u8; len];
    r.read_exact(&mut body)?;
    let (path, qs) = target.split_once('?').unwrap_or((target.as_str(), ""));
    let query = qs
        .split('&')
        .filter_map(|kv| kv.split_once('='))
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
    Ok(Some(Req { method, path: path.to_string(), query, headers, body }))
}

/// A PNG whose header says 848x590, bit depth 1, greyscale; every chunk's length in bounds and ending in IEND.
fn check_png(b: &[u8]) -> Result<(), String> {
    if b.len() < 33 || &b[..8] != b"\x89PNG\r\n\x1a\n" {
        return Err("not a PNG".into());
    }
    let be = |i: usize| u32::from_be_bytes([b[i], b[i + 1], b[i + 2], b[i + 3]]) as usize;
    if &b[12..16] != b"IHDR" {
        return Err("no IHDR".into());
    }
    let (w, h, depth, color) = (be(16), be(20), b[24], b[25]);
    if (w, h) != (848, 590) {
        return Err(format!("size {w}x{h}, want 848x590"));
    }
    if (depth, color) != (1, 0) {
        return Err(format!("bit depth {depth} colour type {color}, want 1-bit greyscale"));
    }
    let mut i = 8;
    while i + 12 <= b.len() {
        let n = be(i);
        let kind = &b[i + 4..i + 8];
        if i + 12 + n > b.len() {
            return Err("truncated chunk".into());
        }
        if kind == b"IEND" {
            return Ok(());
        }
        i += 12 + n;
    }
    Err("no IEND".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn get(path_and_query: &str, token: &str) -> (u16, String) {
        let (path, qs) = path_and_query.split_once('?').unwrap_or((path_and_query, ""));
        let req = Req {
            method: "GET".into(),
            path: path.into(),
            query: qs.split('&').filter_map(|kv| kv.split_once('=')).map(|(k, v)| (k.into(), v.into())).collect(),
            headers: HashMap::from([("cf-access-token".to_string(), token.to_string())]),
            body: vec![],
        };
        let st = Mutex::new(State { out: std::env::temp_dir().display().to_string(), jobs: HashMap::new(), next: 1 });
        let (code, _, _, body) = route(&req, &st).unwrap();
        (code, String::from_utf8(body).unwrap())
    }

    fn ids(body: &str) -> Vec<String> {
        body.split(r#""id":""#).skip(1).map(|s| s.split('"').next().unwrap().to_string()).collect()
    }

    #[test]
    fn guests_without_event_is_the_default_event() {
        let (code, body) = get("/api/guests", "test-token");
        assert_eq!(code, 200);
        assert!(body.starts_with(r#"{"event":{"id":"surgical-futures-forum","#), "{body}");
        assert_eq!(ids(&body), ["surgical-futures-forum", "g1", "g2", "g3", "g4", "g5", "g6"]);
        assert_eq!(get("/api/guests?event=surgical-futures-forum", "test-token").1, body);
    }

    #[test]
    fn guests_of_another_event() {
        let (code, body) = get("/api/guests?event=yyc-health-summit", "test-token");
        assert_eq!(code, 200);
        assert_eq!(ids(&body), ["yyc-health-summit", "g7", "g8"]);
        assert_eq!(get("/api/guests?event=nope", "test-token").0, 404);
    }

    #[test]
    fn events() {
        let (code, body) = get("/api/events", "offline-token");
        assert_eq!(code, 200);
        assert_eq!(ids(&body), ["surgical-futures-forum", "yyc-health-summit"]);
        assert!(body.contains(r#""venue":null,"mode":"auto","open":true,"created":"2026-10-30T07:00:00Z","counts":{"pending":0"#));
    }

    #[test]
    fn checkin() {
        let (code, body) = get(&format!("/api/checkin?code={CODE_ACCEPTED}"), "test-token");
        assert_eq!(code, 200);
        assert_eq!(ids(&body), ["g2", "surgical-futures-forum"]);
        assert_eq!(get(&format!("/api/checkin?code={CODE_PENDING}"), "test-token"), (409, r#"{"error":"此報名未確認"}"#.into()));
        assert_eq!(get("/api/checkin?code=Zz0000000000000000000a", "test-token"), (404, r#"{"error":"找不到此二維碼"}"#.into()));
        assert_eq!(get("/api/checkin", "test-token").0, 404);
    }

    #[test]
    fn signed_out_goes_to_the_login_page() {
        assert_eq!(get("/api/checkin?code=x", "expired-token").0, 302);
        assert_eq!(get("/api/events", "").0, 302);
    }
}
