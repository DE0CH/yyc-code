//! Feishu Open Platform client (open.feishu.cn), as the custom app itself (tenant_access_token): it reaches what is
//! shared with the app's bot (the one folder) and every file shared with the whole organisation (README).
//!
//! Every call: paced (`PACE` between requests), retried with back-off on rate limits (HTTP 429, code 99991400) and
//! server errors, and the token refreshed once when Feishu calls it invalid.

use crate::chunk::Section;
use crate::extract::value_text;
use anyhow::{Context, Result, anyhow, bail};
use serde::Deserialize;
use serde_json::{Value, json};
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

const PACE: Duration = Duration::from_millis(220);
const RATE_LIMITED: i64 = 99991400;
const BAD_TOKEN: [i64; 4] = [99991661, 99991663, 99991664, 99991668];
/// Rows read per Sheets values request.
const SHEET_ROWS: u32 = 2000;

pub struct Feishu {
    http: reqwest::Client,
    /// For forwarded requests: no bound on the whole transfer (a file takes as long as it takes), only on a silence.
    passing: reqwest::Client,
    base: String,
    app_id: String,
    app_secret: String,
    token: Mutex<Option<(String, Instant)>>,
    last: Mutex<Instant>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FileMeta {
    pub token: String,
    pub name: String,
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(default)]
    pub parent_token: String,
    #[serde(default)]
    pub url: String,
    #[serde(default, deserialize_with = "secs")]
    pub modified_time: i64,
    #[serde(default)]
    pub owner_id: String,
}

fn secs<'de, D: serde::Deserializer<'de>>(d: D) -> std::result::Result<i64, D::Error> {
    let v = Value::deserialize(d)?;
    Ok(match v {
        Value::String(s) => s.parse().unwrap_or(0),
        Value::Number(n) => n.as_i64().unwrap_or(0),
        _ => 0,
    })
}

#[derive(Debug)]
pub struct ApiError {
    pub code: i64,
    pub msg: String,
}
impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Feishu error {}: {}", self.code, self.msg)
    }
}
impl std::error::Error for ApiError {}

/// Feishu's answer to a forwarded request.
pub struct Forwarded {
    pub status: u16,
    pub content_type: Option<String>,
    pub content_disposition: Option<String>,
    pub content_length: Option<u64>,
    pub body: ForwardedBody,
}

/// A JSON answer is small and read whole (the token check reads it). Anything else is a file of any size: it stays
/// on the wire and is passed on piece by piece, never held (a video held whole ended the service, 2026-11-16).
pub enum ForwardedBody {
    Whole(Vec<u8>),
    Stream(reqwest::Response),
}

/// Blocks per insert call (Feishu's limit is 1000).
const INSERT_BLOCKS: usize = 900;

/// Top-level blocks with their descendants, split into inserts of at most `INSERT_BLOCKS` blocks, in order.
fn insert_groups(first: &[String], blocks: &[Value]) -> Vec<(Vec<String>, Vec<Value>)> {
    let by_id: std::collections::HashMap<&str, &Value> = blocks
        .iter()
        .filter_map(|b| b["block_id"].as_str().map(|id| (id, b)))
        .collect();
    let mut out: Vec<(Vec<String>, Vec<Value>)> = Vec::new();
    for id in first {
        let mut tree = Vec::new();
        let mut stack = vec![id.as_str()];
        while let Some(i) = stack.pop() {
            if let Some(b) = by_id.get(i) {
                tree.push((*b).clone());
                for c in b["children"].as_array().into_iter().flatten() {
                    if let Some(c) = c.as_str() {
                        stack.push(c);
                    }
                }
            }
        }
        match out.last_mut() {
            Some((ids, group)) if group.len() + tree.len() <= INSERT_BLOCKS => {
                ids.push(id.clone());
                group.extend(tree);
            }
            _ => out.push((vec![id.clone()], tree)),
        }
    }
    out
}

enum Body {
    None,
    Json(Value),
}

impl Feishu {
    pub fn new(base: &str, app_id: &str, app_secret: &str) -> Self {
        Feishu {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(120))
                .build()
                .unwrap(),
            passing: reqwest::Client::builder()
                .connect_timeout(Duration::from_secs(10))
                .read_timeout(Duration::from_secs(120))
                .build()
                .unwrap(),
            base: base.trim_end_matches('/').to_string(),
            app_id: app_id.into(),
            app_secret: app_secret.into(),
            token: Mutex::new(None),
            last: Mutex::new(Instant::now() - PACE),
        }
    }

    async fn pace(&self) {
        let mut last = self.last.lock().await;
        let wait = PACE.saturating_sub(last.elapsed());
        if !wait.is_zero() {
            tokio::time::sleep(wait).await;
        }
        *last = Instant::now();
    }

    async fn token(&self, refresh: bool) -> Result<String> {
        let mut t = self.token.lock().await;
        if !refresh
            && let Some((tok, until)) = t.as_ref()
            && Instant::now() < *until
        {
            return Ok(tok.clone());
        }
        self.pace().await;
        let r: Value = self
            .http
            .post(format!(
                "{}/open-apis/auth/v3/tenant_access_token/internal",
                self.base
            ))
            .json(&json!({"app_id": self.app_id, "app_secret": self.app_secret}))
            .send()
            .await?
            .json()
            .await?;
        if r["code"].as_i64() != Some(0) {
            bail!("tenant_access_token: {} {}", r["code"], r["msg"]);
        }
        let tok = r["tenant_access_token"]
            .as_str()
            .context("no tenant_access_token")?
            .to_string();
        let expire = r["expire"].as_u64().unwrap_or(7200);
        *t = Some((
            tok.clone(),
            Instant::now() + Duration::from_secs(expire.saturating_sub(300)),
        ));
        Ok(tok)
    }

    /// One call, retried; returns the response's `data`, or with `raw` the file's bytes, at most that many (a file
    /// over it is refused by its declared length, or as soon as that many have arrived: never read whole first).
    async fn call(
        &self,
        method: reqwest::Method,
        path: &str,
        query: &[(&str, String)],
        body: Body,
        raw: Option<u64>,
    ) -> Result<Result<Value, Vec<u8>>> {
        let mut refreshed = false;
        let mut attempt = 0u32;
        loop {
            attempt += 1;
            let tok = self.token(false).await?;
            self.pace().await;
            let mut req = self
                .http
                .request(method.clone(), format!("{}{}", self.base, path))
                .bearer_auth(&tok)
                .query(query);
            if let Body::Json(b) = &body {
                req = req.json(b);
            }
            let resp = match req.send().await {
                Ok(r) => r,
                Err(e) if attempt < 6 => {
                    tracing::warn!(path, error = %e, "Feishu request failed, retrying");
                    tokio::time::sleep(Duration::from_secs(2u64.pow(attempt))).await;
                    continue;
                }
                Err(e) => return Err(e.into()),
            };
            let status = resp.status();
            let is_json = resp
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .is_some_and(|v| v.contains("json"));
            if let Some(max) = raw
                && status.is_success()
                && !is_json
            {
                let over = |n: u64| anyhow!("{n} bytes, over the {max}-byte limit");
                if let Some(n) = resp.content_length().filter(|n| *n > max) {
                    return Err(over(n));
                }
                let mut resp = resp;
                let mut bytes = Vec::new();
                while let Some(piece) = resp.chunk().await? {
                    if (bytes.len() + piece.len()) as u64 > max {
                        return Err(over((bytes.len() + piece.len()) as u64));
                    }
                    bytes.extend_from_slice(&piece);
                }
                return Ok(Err(bytes));
            }
            let bytes = resp.bytes().await?.to_vec();
            let v: Value = serde_json::from_slice(&bytes)
                .unwrap_or_else(|_| json!({"code": -1, "msg": format!("HTTP {status}")}));
            let code = v["code"].as_i64().unwrap_or(-1);
            if code == 0 && status.is_success() {
                return Ok(Ok(v.get("data").cloned().unwrap_or(Value::Null)));
            }
            if BAD_TOKEN.contains(&code) && !refreshed {
                refreshed = true;
                self.token(true).await?;
                continue;
            }
            if (status.as_u16() == 429 || code == RATE_LIMITED || status.is_server_error())
                && attempt < 6
            {
                tokio::time::sleep(Duration::from_secs(2u64.pow(attempt))).await;
                continue;
            }
            return Err(ApiError {
                code,
                msg: v["msg"].as_str().unwrap_or("").to_string(),
            })
            .with_context(|| format!("{method} {path}"));
        }
    }

    pub async fn get(&self, path: &str, query: &[(&str, String)]) -> Result<Value> {
        self.call(reqwest::Method::GET, path, query, Body::None, None)
            .await?
            .map_err(|_| anyhow!("unexpected bytes"))
    }

    pub async fn post(&self, path: &str, query: &[(&str, String)], body: Value) -> Result<Value> {
        self.call(reqwest::Method::POST, path, query, Body::Json(body), None)
            .await?
            .map_err(|_| anyhow!("unexpected bytes"))
    }

    /// One call with a JSON body or none, any method; returns the response's `data`.
    pub async fn send(
        &self,
        method: reqwest::Method,
        path: &str,
        query: &[(&str, String)],
        body: Option<Value>,
    ) -> Result<Value> {
        let body = body.map_or(Body::None, Body::Json);
        self.call(method, path, query, body, None)
            .await?
            .map_err(|_| anyhow!("unexpected bytes"))
    }

    /// A caller's request passed on as the app: Feishu's answer comes back as it is (status, type, body), errors
    /// included. Only an invalid token is handled here (refreshed once).
    pub async fn forward(
        &self,
        method: reqwest::Method,
        path_and_query: &str,
        content_type: Option<&str>,
        body: Vec<u8>,
    ) -> Result<Forwarded> {
        let mut refreshed = false;
        loop {
            let tok = self.token(false).await?;
            self.pace().await;
            let mut req = self
                .passing
                .request(method.clone(), format!("{}{}", self.base, path_and_query))
                .bearer_auth(&tok);
            if let Some(ct) = content_type {
                req = req.header(reqwest::header::CONTENT_TYPE, ct);
            }
            if !body.is_empty() {
                req = req.body(body.clone());
            }
            let resp = req.send().await?;
            let status = resp.status().as_u16();
            let header = |n: reqwest::header::HeaderName| {
                resp.headers()
                    .get(n)
                    .and_then(|v| v.to_str().ok())
                    .map(str::to_string)
            };
            let content_type = header(reqwest::header::CONTENT_TYPE);
            let content_disposition = header(reqwest::header::CONTENT_DISPOSITION);
            if !content_type.as_deref().is_some_and(|c| c.contains("json")) {
                return Ok(Forwarded {
                    status,
                    content_type,
                    content_disposition,
                    content_length: resp.content_length(),
                    body: ForwardedBody::Stream(resp),
                });
            }
            let bytes = resp.bytes().await?.to_vec();
            if !refreshed
                && let Ok(v) = serde_json::from_slice::<Value>(&bytes)
                && v["code"].as_i64().is_some_and(|c| BAD_TOKEN.contains(&c))
            {
                refreshed = true;
                self.token(true).await?;
                continue;
            }
            return Ok(Forwarded {
                status,
                content_type,
                content_disposition,
                content_length: None,
                body: ForwardedBody::Whole(bytes),
            });
        }
    }

    /// A new Doc in `folder` with the markdown as its body; returns the document id.
    pub async fn create_doc(&self, folder: &str, title: &str, markdown: &str) -> Result<String> {
        let d = self
            .post(
                "/open-apis/docx/v1/documents",
                &[],
                json!({"folder_token": folder, "title": title}),
            )
            .await?;
        let id = d["document"]["document_id"]
            .as_str()
            .context("no document_id")?
            .to_string();
        self.append_markdown(&id, markdown).await?;
        Ok(id)
    }

    /// Markdown converted to blocks by Feishu and added at the end of the Doc; returns the top-level block count.
    pub async fn append_markdown(&self, doc: &str, markdown: &str) -> Result<usize> {
        if markdown.trim().is_empty() {
            return Ok(0);
        }
        let d = self
            .post(
                "/open-apis/docx/v1/documents/blocks/convert",
                &[],
                json!({"content_type": "markdown", "content": markdown}),
            )
            .await?;
        let first: Vec<String> = d["first_level_block_ids"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        let mut blocks = d["blocks"].as_array().cloned().unwrap_or_default();
        for b in &mut blocks {
            // a converted table carries merge_info, which the insert refuses (1770001 invalid param)
            if let Some(p) = b
                .pointer_mut("/table/property")
                .and_then(Value::as_object_mut)
            {
                p.remove("merge_info");
            }
        }
        for (ids, group) in insert_groups(&first, &blocks) {
            self.post(
                &format!("/open-apis/docx/v1/documents/{doc}/blocks/{doc}/descendant"),
                &[("document_revision_id", "-1".into())],
                json!({"children_id": ids, "index": -1, "descendants": group}),
            )
            .await?;
        }
        Ok(first.len())
    }

    /// The Doc's body replaced by the markdown (the title stays).
    pub async fn replace_markdown(&self, doc: &str, markdown: &str) -> Result<usize> {
        let d = self
            .get(
                &format!("/open-apis/docx/v1/documents/{doc}/blocks/{doc}"),
                &[("document_revision_id", "-1".into())],
            )
            .await?;
        let n = d["block"]["children"].as_array().map_or(0, |a| a.len());
        if n > 0 {
            self.send(
                reqwest::Method::DELETE,
                &format!("/open-apis/docx/v1/documents/{doc}/blocks/{doc}/children/batch_delete"),
                &[("document_revision_id", "-1".into())],
                Some(json!({"start_index": 0, "end_index": n})),
            )
            .await?;
        }
        self.append_markdown(doc, markdown).await
    }

    /// A file of at most 20 MB into `folder`; returns its token.
    pub async fn upload(&self, folder: &str, name: &str, bytes: Vec<u8>) -> Result<String> {
        let mut refreshed = false;
        loop {
            let tok = self.token(false).await?;
            self.pace().await;
            let form = reqwest::multipart::Form::new()
                .text("file_name", name.to_string())
                .text("parent_type", "explorer")
                .text("parent_node", folder.to_string())
                .text("size", bytes.len().to_string())
                .part(
                    "file",
                    reqwest::multipart::Part::bytes(bytes.clone()).file_name(name.to_string()),
                );
            let v: Value = self
                .http
                .post(format!("{}/open-apis/drive/v1/files/upload_all", self.base))
                .bearer_auth(&tok)
                .multipart(form)
                .send()
                .await?
                .json()
                .await?;
            let code = v["code"].as_i64().unwrap_or(-1);
            if BAD_TOKEN.contains(&code) && !refreshed {
                refreshed = true;
                self.token(true).await?;
                continue;
            }
            if code != 0 {
                return Err(ApiError {
                    code,
                    msg: v["msg"].as_str().unwrap_or("").to_string(),
                })
                .context("upload_all");
            }
            return Ok(v["data"]["file_token"]
                .as_str()
                .context("no file_token")?
                .to_string());
        }
    }

    /// The items of a folder (one level), every page.
    pub async fn list_folder(&self, folder: &str) -> Result<Vec<FileMeta>> {
        let mut out = Vec::new();
        let mut page = String::new();
        loop {
            let mut q = vec![
                ("folder_token", folder.to_string()),
                ("page_size", "200".to_string()),
            ];
            if !page.is_empty() {
                q.push(("page_token", page.clone()));
            }
            let d = self.get("/open-apis/drive/v1/files", &q).await?;
            for f in d["files"].as_array().cloned().unwrap_or_default() {
                out.push(serde_json::from_value(f).context("a file entry")?);
            }
            match (d["has_more"].as_bool(), d["next_page_token"].as_str()) {
                (Some(true), Some(t)) if !t.is_empty() => page = t.to_string(),
                _ => break,
            }
        }
        Ok(out)
    }

    pub async fn docx_text(&self, id: &str) -> Result<String> {
        let d = self
            .get(
                &format!("/open-apis/docx/v1/documents/{id}/raw_content"),
                &[("lang", "0".into())],
            )
            .await?;
        Ok(d["content"].as_str().unwrap_or("").to_string())
    }

    /// Old-style documents (type "doc").
    pub async fn doc_text(&self, token: &str) -> Result<String> {
        let d = self
            .get(&format!("/open-apis/doc/v2/{token}/raw_content"), &[])
            .await?;
        Ok(d["content"].as_str().unwrap_or("").to_string())
    }

    /// One section per worksheet: the first row as the header, every other non-empty row tab-separated.
    pub async fn sheet_sections(&self, token: &str) -> Result<Vec<Section>> {
        let d = self
            .get(
                &format!("/open-apis/sheets/v3/spreadsheets/{token}/sheets/query"),
                &[],
            )
            .await?;
        let mut out = Vec::new();
        for s in d["sheets"].as_array().cloned().unwrap_or_default() {
            if s["resource_type"].as_str().is_some_and(|t| t != "sheet") {
                continue;
            }
            let id = s["sheet_id"].as_str().unwrap_or_default().to_string();
            let title = s["title"].as_str().unwrap_or_default().to_string();
            let rows = s["grid_properties"]["row_count"].as_u64().unwrap_or(0) as u32;
            let cols = (s["grid_properties"]["column_count"].as_u64().unwrap_or(0) as u32).max(1);
            let mut all: Vec<String> = Vec::new();
            let mut r1 = 1;
            while r1 <= rows {
                let r2 = (r1 + SHEET_ROWS - 1).min(rows);
                let range = format!("{id}!A{r1}:{}{r2}", col_letters(cols));
                let v = self
                    .get(
                        &format!("/open-apis/sheets/v2/spreadsheets/{token}/values/{range}"),
                        &[
                            ("valueRenderOption", "ToString".into()),
                            ("dateTimeRenderOption", "FormattedString".into()),
                        ],
                    )
                    .await?;
                for row in v["valueRange"]["values"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default()
                {
                    let cells: Vec<String> = row
                        .as_array()
                        .cloned()
                        .unwrap_or_default()
                        .iter()
                        .map(|c| value_text(c).replace(['\t', '\n'], " "))
                        .collect();
                    all.push(cells.join("\t").trim_end().to_string());
                }
                r1 = r2 + 1;
            }
            let mut it = all.into_iter();
            let header = it.next().filter(|h| !h.is_empty());
            let lines: Vec<String> = it.filter(|l| !l.is_empty()).collect();
            if header.is_some() || !lines.is_empty() {
                out.push(Section {
                    heading: title,
                    header,
                    lines,
                });
            }
        }
        Ok(out)
    }

    /// One section per table: each record as "field: value" pairs on one line, fields in the table's order.
    pub async fn bitable_sections(&self, app: &str) -> Result<Vec<Section>> {
        let mut out = Vec::new();
        for t in self
            .paged(&format!("/open-apis/bitable/v1/apps/{app}/tables"), 100)
            .await?
        {
            let tid = t["table_id"].as_str().unwrap_or_default().to_string();
            let name = t["name"].as_str().unwrap_or_default().to_string();
            let fields: Vec<String> = self
                .paged(
                    &format!("/open-apis/bitable/v1/apps/{app}/tables/{tid}/fields"),
                    100,
                )
                .await?
                .iter()
                .filter_map(|f| f["field_name"].as_str().map(String::from))
                .collect();
            let mut lines = Vec::new();
            let mut page = String::new();
            loop {
                let mut q = vec![("page_size", "500".to_string())];
                if !page.is_empty() {
                    q.push(("page_token", page.clone()));
                }
                let d = self
                    .post(
                        &format!("/open-apis/bitable/v1/apps/{app}/tables/{tid}/records/search"),
                        &q,
                        json!({}),
                    )
                    .await?;
                for rec in d["items"].as_array().cloned().unwrap_or_default() {
                    let f = &rec["fields"];
                    let mut parts = Vec::new();
                    let mut names: Vec<String> = fields.clone();
                    if let Some(o) = f.as_object() {
                        for k in o.keys() {
                            if !names.contains(k) {
                                names.push(k.clone());
                            }
                        }
                    }
                    for n in &names {
                        let v = value_text(&f[n.as_str()]).replace('\n', " ");
                        if !v.is_empty() {
                            parts.push(format!("{n}: {v}"));
                        }
                    }
                    if !parts.is_empty() {
                        lines.push(parts.join(" | "));
                    }
                }
                match (d["has_more"].as_bool(), d["page_token"].as_str()) {
                    (Some(true), Some(p)) if !p.is_empty() => page = p.to_string(),
                    _ => break,
                }
            }
            if !lines.is_empty() {
                out.push(Section {
                    heading: name,
                    header: None,
                    lines,
                });
            }
        }
        Ok(out)
    }

    async fn paged(&self, path: &str, size: u32) -> Result<Vec<Value>> {
        let mut out = Vec::new();
        let mut page = String::new();
        loop {
            let mut q = vec![("page_size", size.to_string())];
            if !page.is_empty() {
                q.push(("page_token", page.clone()));
            }
            let d = self.get(path, &q).await?;
            out.extend(d["items"].as_array().cloned().unwrap_or_default());
            match (d["has_more"].as_bool(), d["page_token"].as_str()) {
                (Some(true), Some(p)) if !p.is_empty() => page = p.to_string(),
                _ => break,
            }
        }
        Ok(out)
    }

    /// An uploaded file's bytes, refused when it is larger than `max`.
    pub async fn download(&self, token: &str, max: u64) -> Result<Vec<u8>> {
        match self
            .call(
                reqwest::Method::GET,
                &format!("/open-apis/drive/v1/files/{token}/download"),
                &[],
                Body::None,
                Some(max),
            )
            .await?
        {
            Err(bytes) => Ok(bytes),
            Ok(_) => bail!("download returned JSON, not the file"),
        }
    }
}

/// 1 -> A, 26 -> Z, 27 -> AA.
pub fn col_letters(mut n: u32) -> String {
    let mut s = Vec::new();
    while n > 0 {
        n -= 1;
        s.push(b'A' + (n % 26) as u8);
        n /= 26;
    }
    s.reverse();
    String::from_utf8(s).unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn columns() {
        assert_eq!(col_letters(1), "A");
        assert_eq!(col_letters(26), "Z");
        assert_eq!(col_letters(27), "AA");
        assert_eq!(col_letters(702), "ZZ");
        assert_eq!(col_letters(703), "AAA");
    }
}
