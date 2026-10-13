CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS kb_documents (
    id uuid PRIMARY KEY, title text NOT NULL, source text NOT NULL, doc_version text NOT NULL,
    applicable_versions jsonb NOT NULL, filename text NOT NULL, format text NOT NULL,
    original_bytes bytea NOT NULL, pages jsonb NOT NULL, sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(source,doc_version)
);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES kb_documents(id),
    ordinal integer NOT NULL, page integer NOT NULL, start_char integer NOT NULL, end_char integer NOT NULL,
    line_start integer NOT NULL, line_end integer NOT NULL, heading text NOT NULL,
    content text NOT NULL, retrieval_text text NOT NULL, sha256 text NOT NULL, embedding_key text NOT NULL,
    UNIQUE(document_id,ordinal)
);
CREATE TABLE IF NOT EXISTS kb_indexes (
    id uuid PRIMARY KEY, mode text NOT NULL CHECK(mode IN ('keyword','semantic')),
    status text NOT NULL CHECK(status IN ('building','ready','failed')),
    document_ids jsonb NOT NULL, parameters jsonb NOT NULL, manifest_sha256 text NOT NULL,
    embedding_model text, dimensions integer, active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz, error text
);
CREATE TABLE IF NOT EXISTS kb_index_chunks (
    index_id uuid NOT NULL REFERENCES kb_indexes(id), chunk_id uuid NOT NULL REFERENCES kb_chunks(id),
    PRIMARY KEY(index_id,chunk_id)
);
CREATE TABLE IF NOT EXISTS kb_embeddings (
    content_sha256 text NOT NULL, model text NOT NULL, dimension integer NOT NULL,
    embedding vector(1024) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(content_sha256,model,dimension)
);
CREATE TABLE IF NOT EXISTS kb_tasks (
    id uuid PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('index','search')),
    status text NOT NULL CHECK(status IN ('queued','running','completed','blocked','failed')),
    input jsonb NOT NULL, result jsonb, error text,
    created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE TABLE IF NOT EXISTS kb_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES kb_tasks(id), kind text NOT NULL, payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_events_task_idx ON kb_events(task_id,id);
CREATE TABLE IF NOT EXISTS kb_model_calls (
    id uuid PRIMARY KEY, model text NOT NULL, status text NOT NULL,
    estimated_tokens integer NOT NULL, actual_tokens integer, input_payload jsonb NOT NULL,
    response_payload jsonb, error_code text, created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

ALTER TABLE kb_tasks ADD COLUMN IF NOT EXISTS request_id uuid UNIQUE;
ALTER TABLE kb_tasks ADD COLUMN IF NOT EXISTS request_input jsonb;
