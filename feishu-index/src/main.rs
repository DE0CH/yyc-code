//! feishu-index: the API server and (on the replica holding the lock) the indexing loop. Configuration: README.md.

use anyhow::{Context, Result};
use feishu_index::{MIGRATOR, api, embed::Embedder, feishu::Feishu, indexer::Indexer};
use sqlx::postgres::PgPoolOptions;
use std::sync::Arc;
use std::time::Duration;

fn env(k: &str) -> Result<String> {
    std::env::var(k)
        .ok()
        .filter(|v| !v.is_empty())
        .with_context(|| format!("{k} is not set"))
}
fn env_or(k: &str, d: &str) -> String {
    std::env::var(k)
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| d.to_string())
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(tracing_subscriber::EnvFilter::new(env_or(
            "RUST_LOG", "info",
        )))
        .init();

    let database_url = env("DATABASE_URL")?;
    let pool = PgPoolOptions::new()
        .max_connections(8)
        .acquire_timeout(Duration::from_secs(30))
        .connect(&database_url)
        .await?;
    MIGRATOR.run(&pool).await.context("migrations")?;

    let embedder = Arc::new(Embedder::new(
        &env_or("OPENROUTER_BASE", "https://openrouter.ai/api/v1"),
        &env("OPENROUTER_API_KEY")?,
        &env_or("EMBED_MODEL", "qwen/qwen3-embedding-8b"),
    ));
    let feishu = Arc::new(Feishu::new(
        &env_or("FEISHU_BASE", "https://open.feishu.cn"),
        &env("FEISHU_APP_ID")?,
        &env("FEISHU_APP_SECRET")?,
    ));
    let folder = env("FEISHU_FOLDER_TOKEN")?;
    let indexer = Indexer {
        pool: pool.clone(),
        database_url,
        feishu: feishu.clone(),
        embedder: embedder.clone(),
        folder: folder.clone(),
        holder: env_or("POD_NAME", "local"),
        interval: Duration::from_secs(env_or("SCAN_SECONDS", "60").parse()?),
    };
    tokio::spawn(indexer.run());

    let listen = env_or("LISTEN", "0.0.0.0:8080");
    let app = api::router(api::AppState {
        pool,
        embedder,
        feishu,
        folder,
        api_token: env("API_TOKEN")?,
    });
    let l = tokio::net::TcpListener::bind(&listen).await?;
    tracing::info!(%listen, "serving");
    axum::serve(l, app)
        .with_graceful_shutdown(async {
            let mut term =
                tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).unwrap();
            term.recv().await;
        })
        .await?;
    Ok(())
}
