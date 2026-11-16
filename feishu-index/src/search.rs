//! Hybrid search: a keyword leg (the chunks' tsvector against the query's phrase tsquery, crate::text) and a semantic
//! leg (cosine distance of the query's embedding, HNSW), fused by reciprocal rank (k = 60).

use crate::embed::{Embedder, literal};
use crate::text;
use anyhow::Result;
use serde::Serialize;
use sqlx::PgPool;
use std::collections::HashMap;

const LEG: i64 = 50; // candidates per leg
const RRF_K: f64 = 60.0;

#[derive(Debug, Clone, Copy, PartialEq, serde::Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    #[default]
    Hybrid,
    Keyword,
    Semantic,
}

#[derive(Debug, Serialize, Clone)]
pub struct Hit {
    pub file_token: String,
    pub name: String,
    pub kind: String,
    pub path: String,
    pub url: String,
    pub heading: String,
    pub ord: i32,
    pub text: String,
    pub score: f64,
    pub keyword_rank: Option<usize>,
    pub semantic_rank: Option<usize>,
}

type Row = (String, i32, String, String, String, String, String, String);
const COLS: &str = "c.file_token, c.ord, c.heading, c.body, f.name, f.kind, f.path, f.url";

pub async fn search(
    pool: &PgPool,
    embedder: &Embedder,
    q: &str,
    k: usize,
    mode: Mode,
) -> Result<Vec<Hit>> {
    let mut kw: Vec<Row> = Vec::new();
    let mut sem: Vec<Row> = Vec::new();
    if mode != Mode::Semantic
        && let Some(tq) = text::tsquery(q)
    {
        kw = sqlx::query_as(&format!(
            "SELECT {COLS} FROM chunks c JOIN files f ON f.token = c.file_token
             WHERE c.words @@ $1::tsquery ORDER BY ts_rank_cd(c.words, $1::tsquery) DESC, c.file_token, c.ord LIMIT $2"
        ))
        .bind(tq)
        .bind(LEG)
        .fetch_all(pool)
        .await?;
    }
    if mode != Mode::Keyword && !q.trim().is_empty() {
        let v = embedder.embed(&[q.to_string()]).await?;
        sem = sqlx::query_as(&format!(
            "WITH near AS (SELECT sha, embedding <=> $1::halfvec AS d FROM embeddings ORDER BY embedding <=> $1::halfvec LIMIT $2)
             SELECT {COLS} FROM near JOIN chunks c ON c.emb_sha = near.sha JOIN files f ON f.token = c.file_token
             ORDER BY near.d, c.file_token, c.ord LIMIT $2"
        ))
        .bind(literal(&v[0]))
        .bind(LEG)
        .fetch_all(pool)
        .await?;
    }
    Ok(fuse(kw, sem, k))
}

fn fuse(kw: Vec<Row>, sem: Vec<Row>, k: usize) -> Vec<Hit> {
    let mut hits: HashMap<(String, i32), Hit> = HashMap::new();
    for (leg, rows) in [(0, kw), (1, sem)] {
        for (i, r) in rows.into_iter().enumerate() {
            let rank = i + 1;
            let h = hits.entry((r.0.clone(), r.1)).or_insert_with(|| Hit {
                file_token: r.0.clone(),
                ord: r.1,
                heading: r.2.clone(),
                text: r.3.clone(),
                name: r.4.clone(),
                kind: r.5.clone(),
                path: r.6.clone(),
                url: r.7.clone(),
                score: 0.0,
                keyword_rank: None,
                semantic_rank: None,
            });
            h.score += 1.0 / (RRF_K + rank as f64);
            if leg == 0 {
                h.keyword_rank = Some(rank);
            } else {
                h.semantic_rank = Some(rank);
            }
        }
    }
    let mut v: Vec<Hit> = hits.into_values().collect();
    v.sort_by(|a, b| {
        b.score
            .total_cmp(&a.score)
            .then(a.file_token.cmp(&b.file_token))
            .then(a.ord.cmp(&b.ord))
    });
    v.truncate(k);
    v
}

#[cfg(test)]
mod tests {
    use super::*;
    fn row(t: &str, o: i32) -> Row {
        (
            t.into(),
            o,
            String::new(),
            String::new(),
            String::new(),
            String::new(),
            String::new(),
            String::new(),
        )
    }
    #[test]
    fn a_hit_in_both_legs_ranks_first() {
        let kw = vec![row("a", 0), row("b", 0)];
        let sem = vec![row("c", 0), row("b", 0)];
        let h = fuse(kw, sem, 10);
        assert_eq!(h[0].file_token, "b");
        assert_eq!(h[0].keyword_rank, Some(2));
        assert_eq!(h[0].semantic_rank, Some(2));
        assert_eq!(h.len(), 3);
        assert_eq!(fuse(vec![row("a", 0)], vec![], 0).len(), 0);
    }
}
