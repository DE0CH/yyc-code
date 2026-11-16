//! The /v1 API's write side against a mock Feishu: the bearer token, the scoped passthrough, Docs from markdown and
//! uploads. No database is touched (a lazy pool).

use axum::body::Bytes;
use axum::extract::{Multipart, Path, RawQuery, State};
use axum::http::{HeaderMap, Method, StatusCode};
use axum::routing::{any, post};
use axum::{Json, Router};
use feishu_index::api;
use feishu_index::embed::Embedder;
use feishu_index::feishu::Feishu;
use serde_json::{Value, json};
use std::sync::{Arc, Mutex};

#[derive(Default)]
struct Mock {
    /// (method, path, query, authorization, body) of every call but the token's
    calls: Vec<(String, String, String, String, Value)>,
    /// the last upload: its text fields, its file part
    upload: Option<Upload>,
    children: usize,
    /// holds back the second piece of the big file until a test lets it go
    gate: Arc<tokio::sync::Notify>,
}
type Upload = (Vec<(String, String)>, Vec<u8>);
type S = Arc<Mutex<Mock>>;

fn ok(data: Value) -> Json<Value> {
    Json(json!({"code": 0, "msg": "success", "data": data}))
}

async fn record(
    State(s): State<S>,
    method: Method,
    Path(path): Path<String>,
    RawQuery(q): RawQuery,
    headers: HeaderMap,
    body: Bytes,
) -> (StatusCode, Json<Value>) {
    let auth = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    let b: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
    let path = format!("/open-apis/{path}");
    let mut m = s.lock().unwrap();
    m.calls.push((
        method.to_string(),
        path.clone(),
        q.unwrap_or_default(),
        auth,
        b.clone(),
    ));
    let data = if path == "/open-apis/docx/v1/documents/blocks/convert" {
        assert_eq!(b["content_type"], "markdown");
        json!({
            "first_level_block_ids": ["a", "t"],
            "blocks": [
                {"block_id": "a", "block_type": 2, "text": {"elements": [{"text_run": {"content": b["content"]}}]}},
                {"block_id": "t", "block_type": 31, "children": ["c"],
                 "table": {"cells": ["c"], "property": {"row_size": 1, "column_size": 1, "merge_info": [{"row_span": 1, "col_span": 1}]}}},
                {"block_id": "c", "block_type": 32, "table_cell": {}},
            ],
        })
    } else if path == "/open-apis/docx/v1/documents" {
        json!({"document": {"document_id": "doxNEW", "title": b["title"]}})
    } else if path.ends_with("/descendant") {
        m.children += b["children_id"].as_array().map_or(0, |a| a.len());
        json!({})
    } else if path == "/open-apis/docx/v1/documents/doxNEW/blocks/doxNEW" {
        json!({"block": {"block_id": "doxNEW", "children": (0..m.children).map(|i| format!("b{i}")).collect::<Vec<_>>()}})
    } else if path.ends_with("/children/batch_delete") {
        m.children = 0;
        json!({})
    } else if path == "/open-apis/sheets/v2/spreadsheets/sh1/values" {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"code": 90202, "msg": "wrong range"})),
        );
    } else {
        json!({"echo": path})
    };
    (StatusCode::OK, ok(data))
}

/// One piece of the mock's big file (two pieces).
const PIECE: usize = 256 * 1024;

async fn mock() -> (S, String) {
    let s: S = Arc::default();
    let app = Router::new()
        .route(
            "/open-apis/auth/v3/tenant_access_token/internal",
            post(|| async {
                Json(json!({"code": 0, "tenant_access_token": "t-1", "expire": 7200}))
            }),
        )
        .route(
            "/open-apis/drive/v1/files/upload_all",
            post(|State(s): State<S>, mut mp: Multipart| async move {
                let mut fields = Vec::new();
                let mut file = Vec::new();
                while let Some(f) = mp.next_field().await.unwrap() {
                    let name = f.name().unwrap().to_string();
                    if name == "file" {
                        file = f.bytes().await.unwrap().to_vec();
                    } else {
                        fields.push((name, f.text().await.unwrap()));
                    }
                }
                s.lock().unwrap().upload = Some((fields, file));
                ok(json!({"file_token": "boxNEW"}))
            }),
        )
        .route(
            "/open-apis/drive/v1/files/big/download",
            axum::routing::get(|State(s): State<S>| async move {
                // the first piece at once, the rest only when the test lets it go
                let gate = s.lock().unwrap().gate.clone();
                let first = futures_util::stream::once(async {
                    Ok::<_, std::io::Error>(Bytes::from(vec![7u8; PIECE]))
                });
                let rest = futures_util::stream::once(async move {
                    gate.notified().await;
                    Ok::<_, std::io::Error>(Bytes::from(vec![8u8; PIECE]))
                });
                (
                    [
                        ("content-type", "video/mp4".to_string()),
                        (
                            "content-disposition",
                            "attachment; filename=\"big.mp4\"".to_string(),
                        ),
                        ("content-length", (2 * PIECE).to_string()),
                    ],
                    axum::body::Body::from_stream(futures_util::StreamExt::chain(first, rest)),
                )
            }),
        )
        .route("/open-apis/{*path}", any(record))
        .with_state(s.clone());
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
    (s, format!("http://{addr}"))
}

/// The API on a local port, in front of the mock.
async fn serve() -> (S, String, reqwest::Client) {
    let (s, base) = mock().await;
    let state = api::AppState {
        pool: sqlx::postgres::PgPoolOptions::new()
            .connect_lazy("postgres://nobody@127.0.0.1:1/none")
            .unwrap(),
        embedder: Arc::new(Embedder::new(&base, "k", "test/embed")),
        feishu: Arc::new(Feishu::new(&base, "cli_test", "secret")),
        folder: "root".into(),
        api_token: "pair".into(),
    };
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(l, api::router(state)).await.unwrap() });
    (s, format!("http://{addr}"), reqwest::Client::new())
}

#[tokio::test]
async fn v1_needs_the_bearer_token_and_health_does_not() {
    let (s, api, c) = serve().await;
    let r = c.get(format!("{api}/healthz")).send().await.unwrap();
    assert_eq!(r.status(), 200);
    for auth in [None, Some("Bearer wrong")] {
        let mut req = c.get(format!("{api}/v1/feishu/open-apis/drive/v1/files"));
        if let Some(a) = auth {
            req = req.header("authorization", a);
        }
        assert_eq!(req.send().await.unwrap().status(), 401);
    }
    // the old unversioned routes are gone
    let r = c.get(format!("{api}/files")).send().await.unwrap();
    assert_eq!(r.status(), 404);
    assert!(s.lock().unwrap().calls.is_empty(), "nothing reached Feishu");
}

#[tokio::test]
async fn passthrough_forwards_as_the_app_and_returns_feishus_answer() {
    let (s, api, c) = serve().await;
    let r = c
        .post(format!(
            "{api}/v1/feishu/open-apis/bitable/v1/apps/bt1/tables/tb1/records/batch_create?user_id_type=open_id"
        ))
        .bearer_auth("pair")
        .header("x-jarvis-conversation", "conv-1")
        .json(&json!({"records": [{"fields": {"名稱": "甲"}}]}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    let v: Value = r.json().await.unwrap();
    assert_eq!(v["code"], 0, "Feishu's own envelope comes back: {v}");
    {
        let m = s.lock().unwrap();
        let (method, path, query, auth, body) = m.calls.last().unwrap();
        assert_eq!(method, "POST");
        assert_eq!(
            path,
            "/open-apis/bitable/v1/apps/bt1/tables/tb1/records/batch_create"
        );
        assert_eq!(query, "user_id_type=open_id");
        assert_eq!(
            auth, "Bearer t-1",
            "the app's tenant token, not the caller's"
        );
        assert_eq!(body["records"][0]["fields"]["名稱"], "甲");
    }
    // Feishu's errors pass through with their status and body
    let r = c
        .put(format!(
            "{api}/v1/feishu/open-apis/sheets/v2/spreadsheets/sh1/values"
        ))
        .bearer_auth("pair")
        .json(&json!({"valueRange": {}}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 400);
    assert_eq!(r.json::<Value>().await.unwrap()["code"], 90202);
}

#[tokio::test]
async fn passthrough_refuses_what_is_not_file_work() {
    let (s, api, c) = serve().await;
    for path in [
        "open-apis/im/v1/messages",                       // messaging
        "open-apis/contact/v3/users",                     // the directory
        "open-apis/auth/v3/tenant_access_token/internal", // tokens
        "open-apis/drive/v1/permissions/dx1/members",     // sharing
        "open-apis/drive/v2/permissions/dx1/public",      // link sharing
        "open-apis/drive/v1/files/dx1/subscribe",         // event subscriptions
        "open-apis/drive/v1/../../im/v1/messages",        // no walking out
        "other/thing",
    ] {
        let r = c
            .post(format!("{api}/v1/feishu/{path}"))
            .bearer_auth("pair")
            .json(&json!({}))
            .send()
            .await
            .unwrap();
        assert_eq!(r.status(), 403, "{path}");
        let v: Value = r.json().await.unwrap();
        assert!(v["error"].as_str().unwrap().contains("not allowed"), "{v}");
    }
    assert!(s.lock().unwrap().calls.is_empty(), "nothing reached Feishu");
}

#[tokio::test]
async fn a_doc_is_made_from_markdown_then_appended_and_replaced() {
    let (s, api, c) = serve().await;
    let r = c
        .post(format!("{api}/v1/docs"))
        .bearer_auth("pair")
        .json(&json!({"title": "會議紀錄", "markdown": "# 標題"}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    let v: Value = r.json().await.unwrap();
    assert_eq!(v["document_id"], "doxNEW");
    {
        let m = s.lock().unwrap();
        let create = m
            .calls
            .iter()
            .find(|c| c.1 == "/open-apis/docx/v1/documents")
            .unwrap();
        assert_eq!(
            create.4["folder_token"], "root",
            "the shared folder by default"
        );
        assert_eq!(create.4["title"], "會議紀錄");
        let ins = m
            .calls
            .iter()
            .find(|c| c.1.ends_with("/descendant"))
            .unwrap();
        assert_eq!(
            ins.1,
            "/open-apis/docx/v1/documents/doxNEW/blocks/doxNEW/descendant"
        );
        assert_eq!(ins.4["children_id"], json!(["a", "t"]));
        assert_eq!(ins.4["descendants"].as_array().unwrap().len(), 3);
        let table = &ins.4["descendants"][1]["table"]["property"];
        assert!(
            table.get("merge_info").is_none(),
            "Feishu refuses a table's merge_info on insert"
        );
        assert_eq!(m.children, 2);
    }
    // a named folder
    c.post(format!("{api}/v1/docs"))
        .bearer_auth("pair")
        .json(&json!({"title": "x", "markdown": "y", "folder": "fd1"}))
        .send()
        .await
        .unwrap();
    assert_eq!(
        s.lock()
            .unwrap()
            .calls
            .iter()
            .rfind(|c| c.1 == "/open-apis/docx/v1/documents")
            .unwrap()
            .4["folder_token"],
        "fd1"
    );
    s.lock().unwrap().children = 2;
    // append
    let r = c
        .post(format!("{api}/v1/docs/doxNEW/append"))
        .bearer_auth("pair")
        .json(&json!({"markdown": "more"}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    assert_eq!(s.lock().unwrap().children, 4);
    // replace: the old body is deleted, then the new one inserted
    let r = c
        .put(format!("{api}/v1/docs/doxNEW"))
        .bearer_auth("pair")
        .json(&json!({"markdown": "new body"}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    let m = s.lock().unwrap();
    let del = m
        .calls
        .iter()
        .find(|c| c.1.ends_with("/children/batch_delete"))
        .unwrap();
    assert_eq!(del.0, "DELETE");
    assert_eq!(del.4, json!({"start_index": 0, "end_index": 4}));
    assert_eq!(m.children, 2);
}

#[tokio::test]
async fn an_upload_becomes_feishus_multipart() {
    let (s, api, c) = serve().await;
    let r = c
        .post(format!("{api}/v1/upload?name=報告.pdf&folder=fd1"))
        .bearer_auth("pair")
        .body(b"%PDF-1.7 bytes".to_vec())
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 200);
    assert_eq!(r.json::<Value>().await.unwrap()["file_token"], "boxNEW");
    let m = s.lock().unwrap();
    let (fields, file) = m.upload.as_ref().unwrap();
    let get = |k: &str| fields.iter().find(|f| f.0 == k).map(|f| f.1.as_str());
    assert_eq!(get("file_name"), Some("報告.pdf"));
    assert_eq!(get("parent_type"), Some("explorer"));
    assert_eq!(get("parent_node"), Some("fd1"));
    assert_eq!(get("size"), Some("14"));
    assert_eq!(file, b"%PDF-1.7 bytes");
}

/// A download is passed on piece by piece: the caller has the first piece while Feishu still holds the rest back
/// (a whole file held in memory ended the service on a video, 2026-11-16).
#[tokio::test]
async fn passthrough_streams_a_download_as_it_arrives() {
    let (s, api, c) = serve().await;
    let wait = std::time::Duration::from_secs(10);
    let mut r = tokio::time::timeout(
        wait,
        c.get(format!(
            "{api}/v1/feishu/open-apis/drive/v1/files/big/download"
        ))
        .bearer_auth("pair")
        .send(),
    )
    .await
    .expect("the answer starts before Feishu has sent the whole file")
    .unwrap();
    assert_eq!(r.status(), 200);
    assert_eq!(r.headers()["content-type"], "video/mp4");
    assert_eq!(
        r.headers()["content-disposition"],
        "attachment; filename=\"big.mp4\""
    );
    assert_eq!(
        r.headers()["content-length"],
        (2 * PIECE).to_string().as_str()
    );
    let mut got = 0;
    while got < PIECE {
        let chunk = tokio::time::timeout(wait, r.chunk())
            .await
            .expect("the first piece arrives while the rest is held back")
            .unwrap()
            .expect("more to come");
        assert!(chunk.iter().all(|b| *b == 7));
        got += chunk.len();
    }
    assert_eq!(got, PIECE);
    let gate = s.lock().unwrap().gate.clone();
    gate.notify_one();
    while let Some(chunk) = r.chunk().await.unwrap() {
        assert!(chunk.iter().all(|b| *b == 8));
        got += chunk.len();
    }
    assert_eq!(got, 2 * PIECE);
}

/// The indexer's download refuses a file over its limit by the declared length, before reading it (the limit used
/// to be checked on the whole file in memory).
#[tokio::test]
async fn a_download_over_the_limit_is_refused_before_it_is_read() {
    let (_s, base) = mock().await;
    let feishu = Feishu::new(&base, "cli_test", "secret");
    // the mock holds the second piece back for ever: only a refusal that reads nothing comes back
    let e = tokio::time::timeout(
        std::time::Duration::from_secs(10),
        feishu.download("big", PIECE as u64),
    )
    .await
    .expect("refused without waiting for the file")
    .unwrap_err();
    assert_eq!(
        e.to_string(),
        format!("{} bytes, over the {PIECE}-byte limit", 2 * PIECE)
    );
}
