//! The indexing loop. One replica at a time holds a Postgres advisory lock on its own connection and scans; the
//! others wait for the lock (a deploy or a crash hands it over). A scan walks the whole shared folder tree, re-indexes
//! every file whose modified_time (or the index version) changed, and removes what the walk no longer finds. A walk
//! that fails part-way removes nothing.

use crate::chunk::{Section, chunks};
use crate::embed::{Embedder, literal};
use crate::feishu::{Feishu, FileMeta};
use crate::{INDEX_VERSION, extract, text};
use anyhow::{Context, Result};
use sha2::{Digest, Sha256};
use sqlx::{Connection, PgConnection, PgPool};
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;

const LOCK_KEY: i64 = 0x4645_4953_4855; // "FEISHU"
/// An errored file is tried again after this long, not on every scan.
const RETRY_ERRORS: Duration = Duration::from_secs(30 * 60);
/// Uploaded files over this size are listed but not downloaded.
pub const MAX_FILE_BYTES: i64 = 200 * 1024 * 1024;

pub struct Indexer {
    pub pool: PgPool,
    pub database_url: String,
    pub feishu: Arc<Feishu>,
    pub embedder: Arc<Embedder>,
    pub folder: String,
    pub holder: String,
    pub interval: Duration,
}

#[derive(Debug, Default, PartialEq)]
pub struct ScanStats {
    pub seen: i32,
    pub indexed: i32,
    pub failed: i32,
    pub removed: i32,
}

pub fn sha(parts: &[&str]) -> String {
    let mut h = Sha256::new();
    for p in parts {
        h.update(p.as_bytes());
        h.update([0u8]);
    }
    hex::encode(h.finalize())
}

impl Indexer {
    /// Runs forever: take the lock, scan every `interval`, re-take it if its connection is lost.
    pub async fn run(self) {
        loop {
            let mut conn = match self.acquire().await {
                Ok(c) => c,
                Err(e) => {
                    tracing::warn!(error = %e, "waiting for the indexing lock failed");
                    tokio::time::sleep(Duration::from_secs(10)).await;
                    continue;
                }
            };
            tracing::info!(holder = %self.holder, "holding the indexing lock");
            loop {
                match self.scan().await {
                    Ok(s) => tracing::info!(?s, "scan done"),
                    Err(e) => tracing::error!(error = %format!("{e:#}"), "scan failed"),
                }
                tokio::time::sleep(self.interval).await;
                if sqlx::query("SELECT 1").execute(&mut conn).await.is_err() {
                    tracing::warn!("the lock's connection is gone; taking the lock again");
                    break;
                }
            }
        }
    }

    async fn acquire(&self) -> Result<PgConnection> {
        loop {
            let mut conn = PgConnection::connect(&self.database_url).await?;
            let got: bool = sqlx::query_scalar("SELECT pg_try_advisory_lock($1)")
                .bind(LOCK_KEY)
                .fetch_one(&mut conn)
                .await?;
            if got {
                sqlx::query("INSERT INTO leader (id, holder, since) VALUES (1, $1, now()) ON CONFLICT (id) DO UPDATE SET holder = $1, since = now()")
                    .bind(&self.holder)
                    .execute(&mut conn)
                    .await?;
                return Ok(conn);
            }
            conn.close().await.ok();
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    }

    /// Every item below the shared folder with its path, or an error if any listing failed.
    pub async fn walk(&self) -> Result<Vec<(FileMeta, String)>> {
        let mut out = Vec::new();
        let mut stack = vec![(self.folder.clone(), String::new())];
        let mut visited = HashSet::new();
        while let Some((folder, prefix)) = stack.pop() {
            if !visited.insert(folder.clone()) {
                continue;
            }
            for f in self
                .feishu
                .list_folder(&folder)
                .await
                .with_context(|| format!("listing folder {prefix:?}"))?
            {
                let path = if prefix.is_empty() {
                    f.name.clone()
                } else {
                    format!("{prefix}/{}", f.name)
                };
                if f.kind == "folder" {
                    stack.push((f.token.clone(), path.clone()));
                }
                out.push((f, path));
            }
        }
        Ok(out)
    }

    pub async fn scan(&self) -> Result<ScanStats> {
        let scan_id: i64 =
            sqlx::query_scalar("INSERT INTO scans (holder) VALUES ($1) RETURNING id")
                .bind(&self.holder)
                .fetch_one(&self.pool)
                .await?;
        let r = self.scan_inner().await;
        let (stats, err) = match &r {
            Ok(s) => (s, None),
            Err(e) => (&ScanStats::default(), Some(format!("{e:#}"))),
        };
        sqlx::query("UPDATE scans SET finished_at = now(), seen = $2, indexed = $3, failed = $4, removed = $5, error = $6 WHERE id = $1")
            .bind(scan_id)
            .bind(stats.seen)
            .bind(stats.indexed)
            .bind(stats.failed)
            .bind(stats.removed)
            .bind(err)
            .execute(&self.pool)
            .await?;
        sqlx::query("DELETE FROM scans WHERE started_at < now() - interval '14 days'")
            .execute(&self.pool)
            .await?;
        r
    }

    async fn scan_inner(&self) -> Result<ScanStats> {
        let items = self.walk().await?;
        let mut stats = ScanStats {
            seen: items.len() as i32,
            ..Default::default()
        };

        type Row = (
            String,
            Option<i64>,
            Option<String>,
            Option<i32>,
            String,
            Option<i64>,
            Option<chrono::DateTime<chrono::Utc>>,
        );
        let known: HashMap<String, Row> = sqlx::query_as::<_, Row>(
            "SELECT token, indexed_mtime, indexed_path, index_version, status, error_mtime, error_at FROM files",
        )
        .fetch_all(&self.pool)
        .await?
        .into_iter()
        .map(|r| (r.0.clone(), r))
        .collect();

        for (f, path) in &items {
            sqlx::query(
                "INSERT INTO files (token, kind, name, parent_token, path, url, owner_id, modified_time, seen_at)
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,now())
                 ON CONFLICT (token) DO UPDATE SET kind=$2, name=$3, parent_token=$4, path=$5, url=$6, owner_id=$7,
                   modified_time=$8, seen_at=now()",
            )
            .bind(&f.token)
            .bind(&f.kind)
            .bind(&f.name)
            .bind(&f.parent_token)
            .bind(path)
            .bind(&f.url)
            .bind(&f.owner_id)
            .bind(f.modified_time)
            .execute(&self.pool)
            .await?;

            let due = match known.get(&f.token) {
                None => true,
                Some((_, mtime, ipath, ver, status, err_mtime, err_at)) => {
                    let changed = *mtime != Some(f.modified_time)
                        || ipath.as_deref() != Some(path.as_str())
                        || *ver != Some(INDEX_VERSION);
                    let waited = err_at.is_none_or(|t| {
                        (chrono::Utc::now() - t).to_std().unwrap_or_default() > RETRY_ERRORS
                    });
                    changed && (status != "error" || *err_mtime != Some(f.modified_time) || waited)
                }
            };
            if !due {
                continue;
            }
            match self.index_file(f, path).await {
                Ok(()) => stats.indexed += 1,
                Err(e) => {
                    stats.failed += 1;
                    let msg = format!("{e:#}");
                    tracing::warn!(token = %f.token, path = %path, error = %msg, "indexing a file failed");
                    sqlx::query("UPDATE files SET status = 'error', error = $2, error_at = now(), error_mtime = $3 WHERE token = $1")
                        .bind(&f.token)
                        .bind(msg)
                        .bind(f.modified_time)
                        .execute(&self.pool)
                        .await?;
                }
            }
        }

        let seen: Vec<String> = items.iter().map(|(f, _)| f.token.clone()).collect();
        stats.removed = sqlx::query("DELETE FROM files WHERE NOT (token = ANY($1))")
            .bind(&seen)
            .execute(&self.pool)
            .await?
            .rows_affected() as i32;
        sqlx::query("DELETE FROM embeddings e WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.emb_sha = e.sha)").execute(&self.pool).await?;
        Ok(stats)
    }

    /// The sections of one item, or None for kinds with no text (folders, shortcuts, mind notes, unsupported uploads).
    async fn sections(&self, f: &FileMeta) -> Result<Option<Vec<Section>>> {
        Ok(Some(match f.kind.as_str() {
            "docx" => vec![Section::body(extract::lines(
                &self.feishu.docx_text(&f.token).await?,
            ))],
            "doc" => vec![Section::body(extract::lines(
                &self.feishu.doc_text(&f.token).await?,
            ))],
            "sheet" => self.feishu.sheet_sections(&f.token).await?,
            "bitable" => self.feishu.bitable_sections(&f.token).await?,
            "file" if extract::supported(&f.name) => {
                let bytes = self
                    .feishu
                    .download(&f.token, MAX_FILE_BYTES as u64)
                    .await?;
                extract::file(&f.name, bytes).await?
            }
            _ => return Ok(None),
        }))
    }

    pub async fn index_file(&self, f: &FileMeta, path: &str) -> Result<()> {
        let Some(sections) = self.sections(f).await? else {
            sqlx::query("UPDATE files SET status='skipped', error=NULL, error_at=NULL, error_mtime=NULL, indexed_mtime=$2, index_version=$3, indexed_path=$4, chars=0, indexed_at=now() WHERE token=$1")
                .bind(&f.token)
                .bind(f.modified_time)
                .bind(INDEX_VERSION)
                .bind(path)
                .execute(&self.pool)
                .await?;
            sqlx::query("DELETE FROM chunks WHERE file_token = $1")
                .bind(&f.token)
                .execute(&self.pool)
                .await?;
            return Ok(());
        };
        let cs = chunks(&sections);
        let chars: usize = cs.iter().map(|c| c.body.chars().count()).sum();

        // what is embedded: the path and section heading give a chunk its context
        let inputs: Vec<String> = cs
            .iter()
            .map(|c| {
                if c.heading.is_empty() {
                    format!("{path}\n{}", c.body)
                } else {
                    format!("{path} · {}\n{}", c.heading, c.body)
                }
            })
            .collect();
        let shas: Vec<String> = inputs
            .iter()
            .map(|i| sha(&[&self.embedder.model, i]))
            .collect();
        let have: HashSet<String> =
            sqlx::query_scalar::<_, String>("SELECT sha FROM embeddings WHERE sha = ANY($1)")
                .bind(&shas)
                .fetch_all(&self.pool)
                .await?
                .into_iter()
                .collect();
        let mut todo: Vec<(String, String)> = Vec::new();
        let mut queued = HashSet::new();
        for (s, i) in shas.iter().zip(&inputs) {
            if !have.contains(s) && queued.insert(s.clone()) {
                todo.push((s.clone(), i.clone()));
            }
        }
        let vectors = self
            .embedder
            .embed(&todo.iter().map(|t| t.1.clone()).collect::<Vec<_>>())
            .await?;

        let mut tx = self.pool.begin().await?;
        for ((s, _), v) in todo.iter().zip(&vectors) {
            sqlx::query("INSERT INTO embeddings (sha, model, embedding) VALUES ($1, $2, $3::halfvec) ON CONFLICT (sha) DO NOTHING")
                .bind(s)
                .bind(&self.embedder.model)
                .bind(literal(v))
                .execute(&mut *tx)
                .await?;
        }
        sqlx::query("DELETE FROM chunks WHERE file_token = $1")
            .bind(&f.token)
            .execute(&mut *tx)
            .await?;
        for (i, (c, s)) in cs.iter().zip(&shas).enumerate() {
            let words = text::tsvector(&format!("{} {}\n{}", f.name, c.heading, c.body));
            sqlx::query("INSERT INTO chunks (file_token, ord, heading, body, words, emb_sha) VALUES ($1,$2,$3,$4,$5::tsvector,$6)")
                .bind(&f.token)
                .bind(i as i32)
                .bind(&c.heading)
                .bind(c.body.replace('\0', "\u{fffd}"))
                .bind(words)
                .bind(s)
                .execute(&mut *tx)
                .await?;
        }
        sqlx::query("UPDATE files SET status='ok', error=NULL, error_at=NULL, error_mtime=NULL, indexed_mtime=$2, index_version=$3, chars=$4, indexed_path=$5, indexed_at=now() WHERE token=$1")
            .bind(&f.token)
            .bind(f.modified_time)
            .bind(INDEX_VERSION)
            .bind(chars as i32)
            .bind(path)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        Ok(())
    }
}
