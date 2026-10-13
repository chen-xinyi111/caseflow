CREATE TABLE IF NOT EXISTS collections (
    id uuid PRIMARY KEY,
    lab text NOT NULL CHECK(lab IN ('healthy','fault')),
    status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','partial','failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    error text
);
CREATE TABLE IF NOT EXISTS collection_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    collection_id uuid NOT NULL REFERENCES collections(id),
    kind text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_collection_idx ON collection_events(collection_id,id);
CREATE TABLE IF NOT EXISTS evidence (
    id uuid PRIMARY KEY,
    collection_id uuid NOT NULL REFERENCES collections(id),
    lab text NOT NULL,
    source text NOT NULL,
    collected_at timestamptz NOT NULL,
    success boolean NOT NULL,
    content text NOT NULL,
    sha256 text NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_collection_idx ON evidence(collection_id);
CREATE TABLE IF NOT EXISTS fault_cases (
    id text PRIMARY KEY,
    title text NOT NULL,
    description text NOT NULL,
    collection_id uuid REFERENCES collections(id),
    sample_job_id uuid,
    observed_at timestamptz
);


ALTER TABLE collections ADD COLUMN IF NOT EXISTS request_id uuid UNIQUE;
