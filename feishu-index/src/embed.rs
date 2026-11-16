//! Embeddings through OpenRouter's embeddings API (key on YYC's OpenRouter account, used only by this service).

use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::time::Duration;

/// The vector size, asked of the model (`dimensions`: Qwen3 Embedding truncates and renormalises, Matryoshka); the
/// `embeddings.embedding` column is halfvec(DIM) (HNSW indexes halfvec up to 4000). A model that can't return DIM is a
/// migration. YYC's OpenRouter account gets 403 "violation of provider Terms Of Service" from Google's and OpenAI's
/// embedding models (2026-11-11), so the default is qwen/qwen3-embedding-8b.
pub const DIM: usize = 3072;
const BATCH: usize = 64;

pub struct Embedder {
    http: reqwest::Client,
    url: String,
    key: String,
    pub model: String,
}

impl Embedder {
    pub fn new(base: &str, key: &str, model: &str) -> Self {
        Embedder {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(120))
                .build()
                .unwrap(),
            url: format!("{}/embeddings", base.trim_end_matches('/')),
            key: key.into(),
            model: model.into(),
        }
    }

    /// One vector per input, in order.
    pub async fn embed(&self, inputs: &[String]) -> Result<Vec<Vec<f32>>> {
        let mut out = Vec::with_capacity(inputs.len());
        for batch in inputs.chunks(BATCH) {
            out.extend(self.batch(batch).await?);
        }
        Ok(out)
    }

    async fn batch(&self, inputs: &[String]) -> Result<Vec<Vec<f32>>> {
        let mut attempt = 0u32;
        loop {
            attempt += 1;
            let r = self
                .http
                .post(&self.url)
                .bearer_auth(&self.key)
                .header("X-Title", "YYC feishu-index")
                .json(&json!({"model": self.model, "input": inputs, "dimensions": DIM}))
                .send()
                .await;
            let (status, body) = match r {
                Ok(r) => {
                    let s = r.status();
                    (s, r.text().await.unwrap_or_default())
                }
                Err(e) if attempt < 6 => {
                    tracing::warn!(error = %e, "embeddings request failed, retrying");
                    tokio::time::sleep(Duration::from_secs(2u64.pow(attempt))).await;
                    continue;
                }
                Err(e) => return Err(e.into()),
            };
            if (status.as_u16() == 429 || status.is_server_error()) && attempt < 6 {
                tokio::time::sleep(Duration::from_secs(2u64.pow(attempt))).await;
                continue;
            }
            if !status.is_success() {
                bail!(
                    "embeddings HTTP {status}: {}",
                    body.chars().take(500).collect::<String>()
                );
            }
            let v: Value = serde_json::from_str(&body).context("embeddings response")?;
            let mut data = v["data"]
                .as_array()
                .cloned()
                .context("no data in the embeddings response")?;
            data.sort_by_key(|d| d["index"].as_u64().unwrap_or(0));
            if data.len() != inputs.len() {
                bail!(
                    "embeddings: {} vectors for {} inputs",
                    data.len(),
                    inputs.len()
                );
            }
            return data
                .iter()
                .map(|d| {
                    let e: Vec<f32> = d["embedding"]
                        .as_array()
                        .context("no embedding")?
                        .iter()
                        .map(|x| x.as_f64().unwrap_or(0.0) as f32)
                        .collect();
                    if e.len() != DIM {
                        bail!("embedding of {} dimensions, expected {DIM}", e.len());
                    }
                    Ok(e)
                })
                .collect();
        }
    }
}

/// A vector as pgvector's text form, cast with `::halfvec`.
pub fn literal(v: &[f32]) -> String {
    let parts: Vec<String> = v.iter().map(|x| format!("{x}")).collect();
    format!("[{}]", parts.join(","))
}
