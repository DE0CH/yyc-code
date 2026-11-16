//! The HTTP API (ClusterIP only, inside yyc-talos). `/healthz` and `/readyz` are open; everything under `/v1` needs
//! the bearer token `API_TOKEN` (a caller is admitted by name: README "Callers").
//!
//! Read: search, files, status (from the index). Write: Docs from markdown, uploads, and `/v1/feishu/open-apis/…`,
//! which passes a request on to Feishu as the app for the file APIs only (Drive, Docs, Sheets, Base), never sharing,
//! messaging or anything else.

use crate::embed::Embedder;
use crate::feishu::{Feishu, ForwardedBody};
use crate::search::{Mode, search};
use axum::body::Bytes;
use axum::extract::{DefaultBodyLimit, OriginalUri, Path, Query, Request, State};
use axum::http::{HeaderMap, Method, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{any, get, post, put};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{Value, json};
use sqlx::PgPool;
use std::sync::Arc;

/// Feishu's `upload_all` takes at most 20 MB.
const MAX_BODY: usize = 20 * 1024 * 1024;
/// The Feishu API families the passthrough serves.
const FAMILIES: [&str; 5] = ["drive", "docx", "doc", "sheets", "bitable"];
/// Never passed on, whatever the family: sharing and ownership, event subscriptions.
const REFUSED: [&str; 4] = ["permission", "subscribe", "public", "transfer"];

#[derive(Clone)]
pub struct AppState {
    pub pool: PgPool,
    pub embedder: Arc<Embedder>,
    pub feishu: Arc<Feishu>,
    /// The shared folder's token: where new files go unless the caller names a folder.
    pub folder: String,
    pub api_token: String,
}

pub fn router(state: AppState) -> Router {
    let v1 = Router::new()
        .route("/search", get(search_h))
        .route("/files", get(files))
        .route("/files/{token}", get(file))
        .route("/status", get(status))
        .route("/docs", post(doc_create))
        .route("/docs/{id}", put(doc_replace))
        .route("/docs/{id}/append", post(doc_append))
        .route("/upload", post(upload))
        .route("/feishu/{*path}", any(passthrough))
        .layer(DefaultBodyLimit::max(MAX_BODY))
        .layer(middleware::from_fn_with_state(state.clone(), bearer));
    Router::new()
        .route("/healthz", get(|| async { "ok" }))
        .route("/readyz", get(readyz))
        .nest("/v1", v1)
        .with_state(state)
}

fn refuse(status: StatusCode, msg: impl Into<String>) -> Response {
    (status, Json(json!({"error": msg.into()}))).into_response()
}

async fn bearer(State(s): State<AppState>, req: Request, next: Next) -> Response {
    let given = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "));
    if given != Some(s.api_token.as_str()) {
        return refuse(StatusCode::UNAUTHORIZED, "a bearer token is required");
    }
    next.run(req).await
}

/// Who asked, for the log (egress sets it from the conversation; never trusted for access).
fn conversation(h: &HeaderMap) -> &str {
    h.get("x-jarvis-conversation")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
}

/// Whether a path (percent-decoded, no leading slash) is a file API of Feishu's that may be passed on.
pub fn allowed(path: &str) -> bool {
    let mut seg = path.split('/');
    if seg.next() != Some("open-apis") || !seg.next().is_some_and(|f| FAMILIES.contains(&f)) {
        return false;
    }
    let lower = path.to_lowercase();
    !path
        .split('/')
        .any(|s| s.is_empty() || s == "." || s == "..")
        && !REFUSED.iter().any(|w| lower.contains(w))
        && !path.contains(['%', '\\', '?', '#'])
}

async fn passthrough(
    State(s): State<AppState>,
    method: Method,
    Path(path): Path<String>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if !allowed(&path) {
        return refuse(
            StatusCode::FORBIDDEN,
            "not allowed: only Feishu's Drive, Docs, Sheets and Base APIs (open-apis/drive|docx|doc|sheets|bitable/…), and never sharing, ownership or subscriptions",
        );
    }
    // the path as it was sent (still encoded) after /v1/feishu, with the query
    let raw = uri.path_and_query().map_or(uri.path(), |p| p.as_str());
    let Some(i) = raw.find("/open-apis/") else {
        return refuse(StatusCode::FORBIDDEN, "not allowed");
    };
    let ct = headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok());
    if method != Method::GET {
        tracing::info!(conversation = conversation(&headers), %method, path, "feishu write");
    }
    match s.feishu.forward(method, &raw[i..], ct, body.to_vec()).await {
        Ok(f) => {
            let mut r = Response::builder().status(f.status);
            if let Some(ct) = f.content_type {
                r = r.header(header::CONTENT_TYPE, ct);
            }
            if let Some(cd) = f.content_disposition {
                r = r.header(header::CONTENT_DISPOSITION, cd);
            }
            if let Some(n) = f.content_length {
                r = r.header(header::CONTENT_LENGTH, n);
            }
            r.body(match f.body {
                ForwardedBody::Whole(b) => axum::body::Body::from(b),
                ForwardedBody::Stream(resp) => axum::body::Body::from_stream(resp.bytes_stream()),
            })
            .unwrap()
        }
        Err(e) => refuse(StatusCode::BAD_GATEWAY, format!("{e:#}")),
    }
}

/// A Feishu refusal as 502 with Feishu's code and message; anything else as 500.
fn feishu_err(e: anyhow::Error) -> Response {
    match e.downcast_ref::<crate::feishu::ApiError>() {
        Some(a) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({"error": format!("{e:#}"), "feishu_code": a.code, "feishu_msg": a.msg})),
        )
            .into_response(),
        None => err(e),
    }
}

#[derive(Deserialize)]
struct DocNew {
    title: String,
    #[serde(default)]
    markdown: String,
    folder: Option<String>,
}
#[derive(Deserialize)]
struct DocBody {
    markdown: String,
}

async fn doc_create(
    State(s): State<AppState>,
    headers: HeaderMap,
    Json(b): Json<DocNew>,
) -> Response {
    let folder = b.folder.as_deref().unwrap_or(&s.folder);
    tracing::info!(conversation = conversation(&headers), folder, "doc create");
    match s.feishu.create_doc(folder, &b.title, &b.markdown).await {
        Ok(id) => Json(json!({"document_id": id, "folder": folder})).into_response(),
        Err(e) => feishu_err(e),
    }
}

async fn doc_append(
    State(s): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(b): Json<DocBody>,
) -> Response {
    tracing::info!(
        conversation = conversation(&headers),
        doc = id,
        "doc append"
    );
    match s.feishu.append_markdown(&id, &b.markdown).await {
        Ok(n) => Json(json!({"document_id": id, "blocks_added": n})).into_response(),
        Err(e) => feishu_err(e),
    }
}

async fn doc_replace(
    State(s): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(b): Json<DocBody>,
) -> Response {
    tracing::info!(
        conversation = conversation(&headers),
        doc = id,
        "doc replace"
    );
    match s.feishu.replace_markdown(&id, &b.markdown).await {
        Ok(n) => Json(json!({"document_id": id, "blocks": n})).into_response(),
        Err(e) => feishu_err(e),
    }
}

#[derive(Deserialize)]
struct UploadQ {
    name: String,
    folder: Option<String>,
}

async fn upload(
    State(s): State<AppState>,
    Query(q): Query<UploadQ>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let folder = q.folder.as_deref().unwrap_or(&s.folder);
    tracing::info!(
        conversation = conversation(&headers),
        folder,
        bytes = body.len(),
        "upload"
    );
    match s.feishu.upload(folder, &q.name, body.to_vec()).await {
        Ok(t) => Json(json!({"file_token": t, "folder": folder})).into_response(),
        Err(e) => feishu_err(e),
    }
}

fn err(e: anyhow::Error) -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(json!({"error": format!("{e:#}")})),
    )
        .into_response()
}

async fn readyz(State(s): State<AppState>) -> Response {
    match sqlx::query("SELECT 1").execute(&s.pool).await {
        Ok(_) => "ok".into_response(),
        Err(e) => (StatusCode::SERVICE_UNAVAILABLE, format!("database: {e}")).into_response(),
    }
}

#[derive(Deserialize)]
struct SearchQ {
    q: String,
    #[serde(default = "ten")]
    k: usize,
    #[serde(default)]
    mode: Mode,
}
fn ten() -> usize {
    10
}

async fn search_h(State(s): State<AppState>, Query(p): Query<SearchQ>) -> Response {
    match search(&s.pool, &s.embedder, &p.q, p.k.clamp(1, 100), p.mode).await {
        Ok(hits) => Json(json!({"query": p.q, "hits": hits})).into_response(),
        Err(e) => err(e),
    }
}

async fn files(State(s): State<AppState>) -> Response {
    let r: Result<Vec<(Value,)>, _> = sqlx::query_as(
        "SELECT json_build_object('token', token, 'kind', kind, 'name', name, 'path', path, 'url', url,
           'modified_time', modified_time, 'status', status, 'error', error, 'chars', chars, 'indexed_at', indexed_at,
           'chunks', (SELECT count(*) FROM chunks c WHERE c.file_token = f.token))
         FROM files f ORDER BY path",
    )
    .fetch_all(&s.pool)
    .await;
    match r {
        Ok(rows) => Json(json!({"files": rows.into_iter().map(|r| r.0).collect::<Vec<_>>()}))
            .into_response(),
        Err(e) => err(e.into()),
    }
}

async fn file(State(s): State<AppState>, Path(token): Path<String>) -> Response {
    let meta: Result<Option<(Value,)>, _> = sqlx::query_as(
        "SELECT json_build_object('token', token, 'kind', kind, 'name', name, 'path', path, 'url', url,
           'modified_time', modified_time, 'status', status, 'error', error, 'indexed_at', indexed_at) FROM files WHERE token = $1",
    )
    .bind(&token)
    .fetch_optional(&s.pool)
    .await;
    let meta = match meta {
        Ok(Some(m)) => m.0,
        Ok(None) => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"error": "no such file"})),
            )
                .into_response();
        }
        Err(e) => return err(e.into()),
    };
    let chunks: Result<Vec<(String, String)>, _> =
        sqlx::query_as("SELECT heading, body FROM chunks WHERE file_token = $1 ORDER BY ord")
            .bind(&token)
            .fetch_all(&s.pool)
            .await;
    match chunks {
        Ok(cs) => Json(json!({"file": meta, "chunks": cs.into_iter().map(|(h, b)| json!({"heading": h, "text": b})).collect::<Vec<_>>()})).into_response(),
        Err(e) => err(e.into()),
    }
}

async fn status(State(s): State<AppState>) -> Response {
    let r: Result<(Value,), _> = sqlx::query_as(
        "SELECT json_build_object(
           'leader', (SELECT json_build_object('holder', holder, 'since', since) FROM leader),
           'files', (SELECT json_object_agg(status, n) FROM (SELECT status, count(*) n FROM files GROUP BY status) x),
           'chunks', (SELECT count(*) FROM chunks),
           'embeddings', (SELECT count(*) FROM embeddings),
           'scans', (SELECT json_agg(s ORDER BY s.id DESC) FROM (SELECT * FROM scans ORDER BY id DESC LIMIT 10) s))",
    )
    .fetch_one(&s.pool)
    .await;
    match r {
        Ok(mut v) => {
            v.0["folder"] = json!(s.folder);
            Json(v.0).into_response()
        }
        Err(e) => err(e.into()),
    }
}
