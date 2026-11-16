//! The write API against the real Feishu tenant. Runs only with FEISHU_LIVE_APP_ID, FEISHU_LIVE_APP_SECRET and
//! FEISHU_LIVE_FOLDER (a folder shared with the app; the test makes and deletes its own subfolder).

use feishu_index::api;
use feishu_index::embed::Embedder;
use feishu_index::feishu::Feishu;
use serde_json::{Value, json};
use std::sync::Arc;

#[tokio::test]
async fn docs_sheets_base_uploads_move_and_delete() {
    let (Ok(id), Ok(secret), Ok(folder)) = (
        std::env::var("FEISHU_LIVE_APP_ID"),
        std::env::var("FEISHU_LIVE_APP_SECRET"),
        std::env::var("FEISHU_LIVE_FOLDER"),
    ) else {
        eprintln!("skipped: FEISHU_LIVE_* not set");
        return;
    };
    let state = api::AppState {
        pool: sqlx::postgres::PgPoolOptions::new()
            .connect_lazy("postgres://nobody@127.0.0.1:1/none")
            .unwrap(),
        embedder: Arc::new(Embedder::new("http://127.0.0.1:1", "k", "none")),
        feishu: Arc::new(Feishu::new("https://open.feishu.cn", &id, &secret)),
        folder: folder.clone(),
        api_token: "pair".into(),
    };
    let l = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let api = format!("http://{}/v1", l.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(l, api::router(state)).await.unwrap() });
    let c = reqwest::Client::new();
    let fs = |m: reqwest::Method, path: &str, body: Option<Value>| {
        let mut r = c
            .request(m, format!("{api}/feishu/open-apis/{path}"))
            .bearer_auth("pair");
        if let Some(b) = body {
            r = r.json(&b);
        }
        async move {
            let v: Value = r.send().await.unwrap().json().await.unwrap();
            assert_eq!(v["code"], 0, "{v}");
            v["data"].clone()
        }
    };
    use reqwest::Method as M;

    // a subfolder of our own
    let d = fs(
        M::POST,
        "drive/v1/files/create_folder",
        Some(json!({"name": "_live-test", "folder_token": folder})),
    )
    .await;
    let sub = d["token"].as_str().unwrap().to_string();

    // a Doc from markdown with a nested list and a table, appended, then replaced
    let md = "# 標題\n\n一段**粗體**文字。\n\n- 一\n  - 一之一\n- 二\n\n| 甲 | 乙 |\n|---|---|\n| 1 | 2 |\n";
    let v: Value = c
        .post(format!("{api}/docs"))
        .bearer_auth("pair")
        .json(&json!({"title": "live 測試", "markdown": md, "folder": sub}))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let doc = v["document_id"]
        .as_str()
        .unwrap_or_else(|| panic!("{v}"))
        .to_string();
    let text = |doc: String| {
        let fs = &fs;
        async move {
            fs(
                M::GET,
                &format!("docx/v1/documents/{doc}/raw_content"),
                None,
            )
            .await["content"]
                .as_str()
                .unwrap()
                .to_string()
        }
    };
    let t = text(doc.clone()).await;
    assert!(
        t.contains("標題") && t.contains("一之一") && t.contains("甲"),
        "{t}"
    );
    let v: Value = c
        .post(format!("{api}/docs/{doc}/append"))
        .bearer_auth("pair")
        .json(&json!({"markdown": "附加的一段。"}))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(v["blocks_added"], 1, "{v}");
    assert!(text(doc.clone()).await.contains("附加的一段"));
    let v: Value = c
        .put(format!("{api}/docs/{doc}"))
        .bearer_auth("pair")
        .json(&json!({"markdown": "全新內容。"}))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(v["blocks"], 1, "{v}");
    let t = text(doc.clone()).await;
    assert!(t.contains("全新內容") && !t.contains("附加的一段"), "{t}");

    // a Sheet: create, write, append, read back
    let d = fs(
        M::POST,
        "sheets/v3/spreadsheets",
        Some(json!({"title": "live 表", "folder_token": sub})),
    )
    .await;
    let sp = d["spreadsheet"]["spreadsheet_token"]
        .as_str()
        .unwrap()
        .to_string();
    let d = fs(
        M::GET,
        &format!("sheets/v3/spreadsheets/{sp}/sheets/query"),
        None,
    )
    .await;
    let sid = d["sheets"][0]["sheet_id"].as_str().unwrap().to_string();
    fs(M::PUT, &format!("sheets/v2/spreadsheets/{sp}/values"),
       Some(json!({"valueRange": {"range": format!("{sid}!A1:B2"), "values": [["名稱", "金額"], ["甲", 12]]}}))).await;
    fs(
        M::POST,
        &format!("sheets/v2/spreadsheets/{sp}/values_append"),
        Some(json!({"valueRange": {"range": format!("{sid}!A1:B1"), "values": [["乙", 34]]}})),
    )
    .await;
    let d = fs(
        M::GET,
        &format!("sheets/v2/spreadsheets/{sp}/values/{sid}!A1:B3"),
        None,
    )
    .await;
    assert_eq!(d["valueRange"]["values"][2][0], "乙", "{d}");

    // a Base: create, a table, a record, an update, a delete
    let d = fs(
        M::POST,
        "bitable/v1/apps",
        Some(json!({"name": "live Base", "folder_token": sub})),
    )
    .await;
    let app = d["app"]["app_token"].as_str().unwrap().to_string();
    let d = fs(M::POST, &format!("bitable/v1/apps/{app}/tables"),
        Some(json!({"table": {"name": "客戶", "default_view_name": "表格", "fields": [{"field_name": "名稱", "type": 1}, {"field_name": "金額", "type": 2}]}}))).await;
    let tb = d["table_id"].as_str().unwrap().to_string();
    let d = fs(
        M::POST,
        &format!("bitable/v1/apps/{app}/tables/{tb}/records/batch_create"),
        Some(json!({"records": [{"fields": {"名稱": "甲", "金額": 5}}]})),
    )
    .await;
    let rid = d["records"][0]["record_id"].as_str().unwrap().to_string();
    fs(
        M::PUT,
        &format!("bitable/v1/apps/{app}/tables/{tb}/records/{rid}"),
        Some(json!({"fields": {"金額": 6}})),
    )
    .await;
    fs(
        M::DELETE,
        &format!("bitable/v1/apps/{app}/tables/{tb}/records/{rid}"),
        None,
    )
    .await;

    // an upload, read back, moved, deleted
    let v: Value = c
        .post(format!("{api}/upload?name=備忘.txt&folder={sub}"))
        .bearer_auth("pair")
        .body("上載的內容")
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let ft = v["file_token"]
        .as_str()
        .unwrap_or_else(|| panic!("{v}"))
        .to_string();
    let b = c
        .get(format!(
            "{api}/feishu/open-apis/drive/v1/files/{ft}/download"
        ))
        .bearer_auth("pair")
        .send()
        .await
        .unwrap()
        .bytes()
        .await
        .unwrap();
    assert_eq!(&b[..], "上載的內容".as_bytes());
    let d = fs(
        M::POST,
        "drive/v1/files/create_folder",
        Some(json!({"name": "內層", "folder_token": sub})),
    )
    .await;
    let inner = d["token"].as_str().unwrap().to_string();
    fs(
        M::POST,
        &format!("drive/v1/files/{ft}/move"),
        Some(json!({"type": "file", "folder_token": inner})),
    )
    .await;
    let d = fs(
        M::GET,
        &format!("drive/v1/files?folder_token={inner}"),
        None,
    )
    .await;
    assert_eq!(d["files"][0]["token"], ft.as_str(), "{d}");
    fs(M::DELETE, &format!("drive/v1/files/{ft}?type=file"), None).await;

    // sharing stays refused, live too
    let r = c
        .post(format!(
            "{api}/feishu/open-apis/drive/v1/permissions/{doc}/members?type=docx"
        ))
        .bearer_auth("pair")
        .json(&json!({}))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 403);

    // clean up: the subfolder and everything in it
    fs(
        M::DELETE,
        &format!("drive/v1/files/{sub}?type=folder"),
        None,
    )
    .await;
}
