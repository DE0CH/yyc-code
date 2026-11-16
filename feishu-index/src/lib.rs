//! feishu-index (README.md): keeps a searchable index of one Feishu folder in Postgres.

pub mod api;
pub mod chunk;
pub mod embed;
pub mod extract;
pub mod feishu;
pub mod indexer;
pub mod search;
pub mod text;

/// Bump to re-index every file (extraction or chunking changed). Includes the search-text rules.
pub const INDEX_VERSION: i32 = 1 + 1000 * text::RULES;

pub static MIGRATOR: sqlx::migrate::Migrator = sqlx::migrate!("./migrations");
