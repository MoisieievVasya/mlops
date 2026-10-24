-- newsflow warehouse — the three data layers of design 6.1, diagram 2.
--
-- Layer 1  raw.articles        as received, byline in clear, link still present
-- Layer 2  dwh.articles        typed, deduped, mapped to the 17, LINK DROPPED
-- Layer 3  features.article_features   one row per article, carrying feature_version
--
-- Every statement is idempotent: the file is applied on every `make up`.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS dwh;
CREATE SCHEMA IF NOT EXISTS features;

-- ---------------------------------------------------------------- layer 1: raw ---
-- The archive as it arrived, with the two things that make a row traceable:
-- data_version (the DVC hash of the file it was read from) and the batch_month that
-- owns it. `authors` is in clear here and nowhere downstream (design 6.5).
CREATE TABLE IF NOT EXISTS raw.articles (
    article_id        text        NOT NULL,
    batch_month       date        NOT NULL,
    published_at      date        NOT NULL,
    headline          text,
    short_description text,
    authors           text,
    link              text,
    raw_category      text,
    source_file       text        NOT NULL,
    data_version      text        NOT NULL,
    ingested_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (batch_month, article_id)
);
CREATE INDEX IF NOT EXISTS articles_raw_month_idx ON raw.articles (batch_month);

-- -------------------------------------------------------------- layer 2: clean ---
-- Typed rows. There is deliberately no `link` column: the leakage field is dropped
-- at the layer boundary rather than "not used" by a modelling convention
-- (design 5.2, conf/features.yaml:dropped).
CREATE TABLE IF NOT EXISTS dwh.articles (
    article_id      text        NOT NULL PRIMARY KEY,
    batch_month     date        NOT NULL,
    event_ts        date        NOT NULL,
    headline_norm   text        NOT NULL,
    dek_norm        text        NOT NULL,
    has_dek         boolean     NOT NULL,
    text_len_words  integer     NOT NULL,
    author_hash     text        NOT NULL,
    raw_category    text        NOT NULL,
    label_category  text        NOT NULL,
    source_feed_id  text        NOT NULL,
    data_version    text        NOT NULL,
    git_commit      text        NOT NULL,
    loaded_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS articles_dwh_month_idx  ON dwh.articles (batch_month);
CREATE INDEX IF NOT EXISTS articles_dwh_author_idx ON dwh.articles (author_hash, event_ts);
CREATE INDEX IF NOT EXISTS articles_dwh_ts_idx     ON dwh.articles (event_ts);

-- ----------------------------------------------------------- layer 3: features ---
-- One row per article per feature definition. author_desk_prior is a 17-vector built
-- by an as-of join over strictly-earlier rows; feature_version names the definition
-- that produced the row.
CREATE TABLE IF NOT EXISTS features.article_features (
    article_id        text        NOT NULL PRIMARY KEY,
    batch_month       date        NOT NULL,
    event_ts          timestamptz NOT NULL,
    headline_norm     text        NOT NULL,
    dek_norm          text        NOT NULL,
    text_len_words    integer     NOT NULL,
    has_dek           boolean     NOT NULL,
    publish_dow       smallint    NOT NULL,
    publish_month     smallint    NOT NULL,
    author_hash       text        NOT NULL,
    author_seen_count integer     NOT NULL,
    author_desk_prior real[]      NOT NULL,
    source_feed_id    text        NOT NULL,
    label_category    text        NOT NULL,
    feature_version   text        NOT NULL,
    computed_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS article_features_month_idx  ON features.article_features (batch_month);
CREATE INDEX IF NOT EXISTS article_features_author_idx ON features.article_features (author_hash, event_ts);

-- ------------------------------------------------------------- the run ledger ---
-- Append-only: one row every time the gate runs, never an update. This is where the
-- idempotency evidence lives — two executions of the same interval are two rows with
-- the same counts, and a reviewer can read them without trusting a pasted number.
-- Airflow reuses the run_id when a backfill reprocesses a date, so the run id alone
-- would overwrite the first attempt and destroy exactly the evidence we need.
CREATE TABLE IF NOT EXISTS dwh.batch_runs (
    run_seq             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_month         date        NOT NULL,
    dag_run_id          text        NOT NULL,
    task_attempt        timestamptz NOT NULL DEFAULT now(),
    data_interval_start timestamptz NOT NULL,
    data_interval_end   timestamptz NOT NULL,
    rows_extracted      integer,
    rows_loaded         integer,
    rows_features       integer,
    rows_dropped_dup    integer,
    rows_dropped_empty  integer,
    data_version        text,
    git_commit          text,
    feature_version     text,
    fault_injected      text,
    status              text        NOT NULL,
    detail              jsonb
);
CREATE INDEX IF NOT EXISTS batch_runs_month_idx ON dwh.batch_runs (batch_month, task_attempt);
