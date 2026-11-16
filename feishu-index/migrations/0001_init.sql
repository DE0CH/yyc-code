-- The extension `vector` is created by the Database object (CloudNativePG, spec.extensions), as the role cannot.
CREATE EXTENSION IF NOT EXISTS vector;

-- Every item of the shared folder tree seen in the last complete walk.
CREATE TABLE files (
    token          text PRIMARY KEY,
    kind           text NOT NULL,              -- docx, doc, sheet, bitable, file, folder, mindnote, slides, shortcut
    name           text NOT NULL,
    parent_token   text NOT NULL,
    path           text NOT NULL,              -- folder names from the shared folder down, then the name
    url            text NOT NULL,
    owner_id       text NOT NULL,
    modified_time  bigint NOT NULL,            -- Feishu's, seconds
    indexed_mtime  bigint,                     -- modified_time of the indexed content (NULL: not indexed yet)
    indexed_path   text,                       -- path when indexed (a rename or move re-indexes: the path is in the chunks)
    index_version  integer,                    -- crate::INDEX_VERSION it was indexed with
    status         text NOT NULL DEFAULT 'pending', -- pending | ok | skipped | error
    error          text,
    error_at       timestamptz,
    error_mtime    bigint,                     -- modified_time when it failed: an edit retries at once
    chars          integer,
    indexed_at     timestamptz,
    seen_at        timestamptz NOT NULL DEFAULT now()
);

-- Embeddings by the hash of (model, input text): an edit re-embeds only the chunks whose text changed.
CREATE TABLE embeddings (
    sha        text PRIMARY KEY,
    model      text NOT NULL,
    embedding  halfvec(3072) NOT NULL
);
CREATE INDEX embeddings_hnsw ON embeddings USING hnsw (embedding halfvec_cosine_ops);

CREATE TABLE chunks (
    file_token  text NOT NULL REFERENCES files(token) ON DELETE CASCADE,
    ord         integer NOT NULL,
    heading     text NOT NULL,
    body        text NOT NULL,
    words       tsvector NOT NULL,             -- crate::text::tsvector(heading + body)
    emb_sha     text NOT NULL,
    PRIMARY KEY (file_token, ord)
);
CREATE INDEX chunks_words ON chunks USING gin (words);
CREATE INDEX chunks_emb ON chunks (emb_sha);

CREATE TABLE scans (
    id           bigserial PRIMARY KEY,
    holder       text NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    seen         integer,
    indexed      integer,
    failed       integer,
    removed      integer,
    error        text
);

-- Which replica holds the indexing lock (pg_advisory_lock), for /status.
CREATE TABLE leader (
    id      integer PRIMARY KEY CHECK (id = 1),
    holder  text NOT NULL,
    since   timestamptz NOT NULL
);
