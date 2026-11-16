//! End to end against a real Postgres with pgvector (FEISHU_INDEX_TEST_PG = an admin URL; each test makes its own
//! database) and a mock Feishu + OpenRouter on a local port, speaking the documented request and response shapes.

use axum::extract::{Path, Query, State};
use axum::routing::{get, post};
use axum::{Json, Router};
use feishu_index::embed::{DIM, Embedder};
use feishu_index::feishu::Feishu;
use feishu_index::indexer::{Indexer, ScanStats};
use feishu_index::{MIGRATOR, api};
use serde_json::{Value, json};
use sqlx::PgPool;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[derive(Clone)]
struct F {
    token: &'static str,
    name: String,
    kind: &'static str,
    parent: &'static str,
    mtime: i64,
}

#[derive(Default)]
struct Mock {
    files: Vec<F>,
    docx: HashMap<String, String>,
    blobs: HashMap<String, Vec<u8>>,
    /// folder token -> remaining failures of its listing
    fail_list: HashMap<String, u32>,
    /// the next n authenticated calls answer "token invalid"
    bad_token: u32,
    /// the next n authenticated calls answer "rate limited"
    rate_limited: u32,
    tokens_issued: u32,
    embedded_inputs: usize,
}
type S = Arc<Mutex<Mock>>;

fn ok(data: Value) -> Json<Value> {
    Json(json!({"code": 0, "msg": "success", "data": data}))
}

fn gate(s: &S) -> Option<Json<Value>> {
    let mut m = s.lock().unwrap();
    if m.bad_token > 0 {
        m.bad_token -= 1;
        return Some(Json(
            json!({"code": 99991663, "msg": "Invalid access token for authorization."}),
        ));
    }
    if m.rate_limited > 0 {
        m.rate_limited -= 1;
        return Some(Json(
            json!({"code": 99991400, "msg": "request trigger frequency limit"}),
        ));
    }
    None
}

/// A deterministic "embedding": character bigrams hashed into DIM buckets, normalised. Texts sharing words are near.
fn fake_embedding(s: &str) -> Vec<f32> {
    let n = feishu_index::text::norm(s);
    let cs: Vec<char> = n.chars().filter(|c| !c.is_whitespace()).collect();
    let mut v = vec![0f32; DIM];
    for w in cs.windows(2) {
        let h = feishu_index::indexer::sha(&[&w.iter().collect::<String>()]);
        let i = usize::from_str_radix(&h[..8], 16).unwrap() % DIM;
        v[i] += 1.0;
    }
    let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt().max(1e-6);
    v.iter().map(|x| x / norm).collect()
}

async fn mock_server(s: S) -> String {
    let app = Router::new()
        .route(
            "/open-apis/auth/v3/tenant_access_token/internal",
            post(|State(s): State<S>, Json(b): Json<Value>| async move {
                assert_eq!(b["app_id"], "cli_test");
                let mut m = s.lock().unwrap();
                m.tokens_issued += 1;
                Json(json!({"code": 0, "msg": "ok", "tenant_access_token": format!("t-{}", m.tokens_issued), "expire": 7200}))
            }),
        )
        .route(
            "/open-apis/drive/v1/files",
            get(|State(s): State<S>, Query(q): Query<HashMap<String, String>>| async move {
                if let Some(e) = gate(&s) {
                    return e;
                }
                let mut m = s.lock().unwrap();
                let folder = q["folder_token"].clone();
                if let Some(n) = m.fail_list.get_mut(&folder)
                    && *n > 0
                {
                    *n -= 1;
                    return Json(json!({"code": 1061004, "msg": "forbidden"}));
                }
                // two pages, to exercise paging
                let all: Vec<Value> = m
                    .files
                    .iter()
                    .filter(|f| f.parent == folder)
                    .map(|f| {
                        json!({"token": f.token, "name": f.name, "type": f.kind, "parent_token": f.parent,
                          "url": format!("https://x.feishu.cn/{}/{}", f.kind, f.token), "modified_time": f.mtime.to_string(),
                          "created_time": "1", "owner_id": "ou_1"})
                    })
                    .collect();
                let start: usize = q.get("page_token").map(|p| p.parse().unwrap()).unwrap_or(0);
                let page: Vec<Value> = all.iter().skip(start).take(2).cloned().collect();
                let more = start + 2 < all.len();
                ok(json!({"files": page, "has_more": more, "next_page_token": if more { (start + 2).to_string() } else { String::new() }}))
            }),
        )
        .route(
            "/open-apis/docx/v1/documents/{id}/raw_content",
            get(|State(s): State<S>, Path(id): Path<String>| async move {
                if let Some(e) = gate(&s) {
                    return e;
                }
                let m = s.lock().unwrap();
                ok(json!({"content": m.docx.get(&id).cloned().unwrap_or_default()}))
            }),
        )
        .route(
            "/open-apis/sheets/v3/spreadsheets/{t}/sheets/query",
            get(|| async {
                ok(json!({"sheets": [
                    {"sheet_id": "s1", "title": "名單", "index": 0, "resource_type": "sheet", "grid_properties": {"row_count": 3, "column_count": 2}},
                    {"sheet_id": "b1", "title": "embedded", "index": 1, "resource_type": "bitable"}
                ]}))
            }),
        )
        .route(
            "/open-apis/sheets/v2/spreadsheets/{t}/values/{range}",
            get(|Path((_t, range)): Path<(String, String)>| async move {
                assert_eq!(range, "s1!A1:B3");
                ok(json!({"valueRange": {"range": range, "values": [["姓名", "職位"], ["陳大文", [{"type": "text", "text": "總監"}]], [null, null]]}}))
            }),
        )
        .route(
            "/open-apis/bitable/v1/apps/{a}/tables",
            get(|| async { ok(json!({"items": [{"table_id": "tbl1", "name": "客戶"}], "has_more": false})) }),
        )
        .route(
            "/open-apis/bitable/v1/apps/{a}/tables/{t}/fields",
            get(|| async { ok(json!({"items": [{"field_name": "公司"}, {"field_name": "聯絡人"}], "has_more": false})) }),
        )
        .route(
            "/open-apis/bitable/v1/apps/{a}/tables/{t}/records/search",
            post(|| async {
                ok(json!({"items": [{"record_id": "r1", "fields": {"公司": [{"type": "text", "text": "長江實業"}], "聯絡人": [{"name": "李先生", "id": "ou_2"}]}}],
                          "has_more": false, "total": 1}))
            }),
        )
        .route(
            "/open-apis/drive/v1/files/{t}/download",
            get(|State(s): State<S>, Path(t): Path<String>| async move {
                let m = s.lock().unwrap();
                ([("content-type", "application/octet-stream")], m.blobs.get(&t).cloned().unwrap_or_default())
            }),
        )
        .route(
            "/embeddings",
            post(|State(s): State<S>, Json(b): Json<Value>| async move {
                assert_eq!(b["model"], "test/embed");
                assert_eq!(b["dimensions"], DIM, "the request names the vector size");
                let inputs: Vec<String> = b["input"].as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect();
                s.lock().unwrap().embedded_inputs += inputs.len();
                let data: Vec<Value> = inputs.iter().enumerate().map(|(i, t)| json!({"object": "embedding", "index": i, "embedding": fake_embedding(t)})).collect();
                Json(json!({"object": "list", "data": data, "model": "test/embed"}))
            }),
        )
        .with_state(s);
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
    format!("http://{addr}")
}

async fn fresh_db(name: &str) -> (PgPool, String) {
    let admin = std::env::var("FEISHU_INDEX_TEST_PG")
        .expect("FEISHU_INDEX_TEST_PG (an admin Postgres URL) is required");
    let a = PgPool::connect(&admin).await.unwrap();
    let db = format!("fi_test_{name}_{}", std::process::id());
    sqlx::query(&format!("DROP DATABASE IF EXISTS {db} WITH (FORCE)"))
        .execute(&a)
        .await
        .unwrap();
    sqlx::query(&format!("CREATE DATABASE {db}"))
        .execute(&a)
        .await
        .unwrap();
    let (base, _) = admin.rsplit_once('/').unwrap();
    let url = format!("{base}/{db}");
    let pool = PgPool::connect(&url).await.unwrap();
    MIGRATOR.run(&pool).await.unwrap();
    (pool, url)
}

fn tree() -> Mock {
    let f = |token, name: &str, kind, parent, mtime| F {
        token,
        name: name.into(),
        kind,
        parent,
        mtime,
    };
    let mut m = Mock {
        files: vec![
            f("dx1", "政策文件", "docx", "root", 100),
            f("sh1", "員工", "sheet", "root", 100),
            f("bt1", "客戶表", "bitable", "root", 100),
            f("fd1", "子資料夾", "folder", "root", 100),
            f("mn1", "腦圖", "mindnote", "root", 100),
            f("tx1", "notes.txt", "file", "fd1", 100),
            f("jp1", "photo.jpg", "file", "fd1", 100),
        ],
        ..Default::default()
    };
    m.docx.insert(
        "dx1".into(),
        "新資本投資者入境計劃\n申請人須投資三千萬港元。".into(),
    );
    m.blobs
        .insert("tx1".into(), "會議紀錄：討論年度預算。".as_bytes().to_vec());
    m.blobs.insert("jp1".into(), vec![0xff, 0xd8]);
    m
}

async fn setup(name: &str) -> (S, Indexer, PgPool, Arc<Embedder>) {
    let s: S = Arc::new(Mutex::new(tree()));
    let base = mock_server(s.clone()).await;
    let (pool, url) = fresh_db(name).await;
    let embedder = Arc::new(Embedder::new(&base, "k", "test/embed"));
    let ix = Indexer {
        pool: pool.clone(),
        database_url: url,
        feishu: Arc::new(Feishu::new(&base, "cli_test", "secret")),
        embedder: embedder.clone(),
        folder: "root".into(),
        holder: "test".into(),
        interval: Duration::from_secs(60),
    };
    (s, ix, pool, embedder)
}

async fn count(pool: &PgPool, sql: &str) -> i64 {
    sqlx::query_scalar(sql).fetch_one(pool).await.unwrap()
}

#[tokio::test]
async fn scan_indexes_every_kind_then_follows_changes() {
    let (s, ix, pool, embedder) = setup("scan").await;

    let st = ix.scan().await.unwrap();
    assert_eq!(
        st,
        ScanStats {
            seen: 7,
            indexed: 7,
            failed: 0,
            removed: 0
        }
    );
    let status: Vec<(String, String, String)> =
        sqlx::query_as("SELECT token, status, path FROM files ORDER BY token")
            .fetch_all(&pool)
            .await
            .unwrap();
    let by: HashMap<_, _> = status
        .iter()
        .map(|(t, s, p)| (t.as_str(), (s.as_str(), p.as_str())))
        .collect();
    assert_eq!(by["dx1"].0, "ok");
    assert_eq!(by["sh1"].0, "ok");
    assert_eq!(by["bt1"].0, "ok");
    assert_eq!(by["tx1"], ("ok", "子資料夾/notes.txt"));
    assert_eq!(by["fd1"].0, "skipped");
    assert_eq!(by["mn1"].0, "skipped");
    assert_eq!(by["jp1"].0, "skipped");

    // sheet: the header repeated in the chunk; rich text read; the empty row dropped; the embedded bitable skipped
    let sheet: String =
        sqlx::query_scalar("SELECT heading || '|' || body FROM chunks WHERE file_token='sh1'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(sheet, "名單|姓名\t職位\n陳大文\t總監");
    let bt: String = sqlx::query_scalar("SELECT body FROM chunks WHERE file_token='bt1'")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(bt, "公司: 長江實業 | 聯絡人: 李先生");

    // the API: keyword search in Simplified finds the Traditional document; a lone word; semantic search
    let app = api::router(api::AppState {
        pool: pool.clone(),
        embedder: embedder.clone(),
        feishu: ix.feishu.clone(),
        folder: "root".into(),
        api_token: "pair".into(),
    });
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = l.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(l, app).await.unwrap() });
    let get = |path: String| async move {
        reqwest::Client::new()
            .get(format!("http://{addr}/v1{path}"))
            .bearer_auth("pair")
            .send()
            .await
            .unwrap()
            .json::<Value>()
            .await
            .unwrap()
    };

    let r = get("/search?q=入境计划&mode=keyword".into()).await;
    assert_eq!(r["hits"][0]["file_token"], "dx1", "{r}");
    assert_eq!(r["hits"].as_array().unwrap().len(), 1);
    let r = get("/search?q=會議紀錄%20預算&mode=keyword".into()).await;
    assert_eq!(r["hits"][0]["path"], "子資料夾/notes.txt", "{r}");
    let r = get("/search?q=長江實業的聯絡人是誰".into()).await;
    assert_eq!(r["hits"][0]["file_token"], "bt1", "{r}");
    assert!(r["hits"][0]["semantic_rank"].is_number());
    let r = get("/files/dx1".into()).await;
    assert!(r["chunks"][0]["text"].as_str().unwrap().contains("三千萬"));
    let r = get("/status".into()).await;
    assert_eq!(r["files"]["ok"], 4, "{r}");
    assert_eq!(r["scans"][0]["indexed"], 7);
    assert_eq!(r["folder"], "root");

    // nothing changed: nothing re-indexed, nothing embedded
    let before = s.lock().unwrap().embedded_inputs;
    assert_eq!(ix.scan().await.unwrap().indexed, 0);
    assert_eq!(s.lock().unwrap().embedded_inputs, before);

    // an edit re-indexes that file only
    {
        let mut m = s.lock().unwrap();
        m.docx.insert(
            "dx1".into(),
            "新資本投資者入境計劃\n投資額改為五千萬港元。".into(),
        );
        m.files.iter_mut().find(|f| f.token == "dx1").unwrap().mtime = 200;
    }
    assert_eq!(ix.scan().await.unwrap().indexed, 1);
    let body: String =
        sqlx::query_scalar("SELECT string_agg(body, ' ') FROM chunks WHERE file_token='dx1'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(body.contains("五千萬") && !body.contains("三千萬"));

    // a rename re-indexes (the path is in the chunks) even with the same modified_time
    s.lock()
        .unwrap()
        .files
        .iter_mut()
        .find(|f| f.token == "fd1")
        .unwrap()
        .name = "會議".into();
    let st = ix.scan().await.unwrap();
    assert_eq!(st.indexed, 3, "{st:?}"); // the folder, and notes.txt and photo.jpg moved with it

    // a removed file goes, with its chunks and orphaned embeddings
    s.lock().unwrap().files.retain(|f| f.token != "sh1");
    assert_eq!(ix.scan().await.unwrap().removed, 1);
    assert_eq!(
        count(&pool, "SELECT count(*) FROM chunks WHERE file_token='sh1'").await,
        0
    );
    assert_eq!(count(&pool, "SELECT count(*) FROM embeddings e WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.emb_sha=e.sha)").await, 0);

    // a listing that fails part-way removes nothing and records the error
    s.lock().unwrap().fail_list.insert("fd1".into(), 1);
    let n = count(&pool, "SELECT count(*) FROM files").await;
    assert!(ix.scan().await.is_err());
    assert_eq!(count(&pool, "SELECT count(*) FROM files").await, n);
    let err: Option<String> =
        sqlx::query_scalar("SELECT error FROM scans ORDER BY id DESC LIMIT 1")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert!(err.unwrap().contains("1061004"));
}

#[tokio::test]
async fn token_refresh_and_rate_limit_are_retried() {
    let (s, ix, _pool, _e) = setup("retry").await;
    ix.scan().await.unwrap();
    {
        let mut m = s.lock().unwrap();
        m.bad_token = 1;
        m.rate_limited = 1;
    }
    let issued = s.lock().unwrap().tokens_issued;
    let st = ix.scan().await.unwrap();
    assert_eq!(st.seen, 7);
    assert_eq!(s.lock().unwrap().tokens_issued, issued + 1);
}

#[tokio::test]
async fn a_failing_file_is_recorded_and_retried_after_an_edit() {
    let (s, ix, pool, _e) = setup("fail").await;
    // a corrupt .txt is fine, so break a docx-style upload instead: a .docx that is not a zip
    {
        let mut m = s.lock().unwrap();
        m.files.push(F {
            token: "bad1",
            name: "broken.docx".into(),
            kind: "file",
            parent: "root",
            mtime: 100,
        });
        m.blobs.insert("bad1".into(), b"not a zip".to_vec());
    }
    let st = ix.scan().await.unwrap();
    assert_eq!(st.failed, 1);
    let (status, err): (String, String) =
        sqlx::query_as("SELECT status, error FROM files WHERE token='bad1'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(status, "error");
    assert!(err.contains("zip"), "{err}");
    // not retried on the next scan
    assert_eq!(ix.scan().await.unwrap().failed, 0);
    // fixed and edited: retried at once
    {
        let mut m = s.lock().unwrap();
        let xml =
            "<w:document><w:body><w:p><w:r><w:t>修好了</w:t></w:r></w:p></w:body></w:document>";
        let mut buf = std::io::Cursor::new(Vec::new());
        {
            use std::io::Write;
            let mut z = zip::ZipWriter::new(&mut buf);
            z.start_file(
                "word/document.xml",
                zip::write::SimpleFileOptions::default(),
            )
            .unwrap();
            z.write_all(xml.as_bytes()).unwrap();
            z.finish().unwrap();
        }
        m.blobs.insert("bad1".into(), buf.into_inner());
        m.files
            .iter_mut()
            .find(|f| f.token == "bad1")
            .unwrap()
            .mtime = 300;
    }
    let st = ix.scan().await.unwrap();
    assert_eq!((st.indexed, st.failed), (1, 0));
    let body: String = sqlx::query_scalar("SELECT body FROM chunks WHERE file_token='bad1'")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(body, "修好了");
}
