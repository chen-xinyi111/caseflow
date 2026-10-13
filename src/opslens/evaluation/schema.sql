CREATE TABLE IF NOT EXISTS evaluation_runs (
 id uuid PRIMARY KEY, request_id uuid UNIQUE NOT NULL, title text NOT NULL,
 manifest jsonb NOT NULL, status text NOT NULL DEFAULT 'running',
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE TABLE IF NOT EXISTS evaluation_snapshots (
 run_id uuid NOT NULL REFERENCES evaluation_runs(id), source_group text NOT NULL,
 payload jsonb NOT NULL, sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,source_group)
);
CREATE TABLE IF NOT EXISTS evaluation_items (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES evaluation_runs(id),
 case_id text NOT NULL, kind text NOT NULL, mode text NOT NULL, split text NOT NULL,
 source_group text NOT NULL, status text NOT NULL DEFAULT 'pending', input jsonb NOT NULL,
 result jsonb, reference_items jsonb NOT NULL DEFAULT '[]', score jsonb, metrics jsonb NOT NULL DEFAULT '{}',
 error text, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(run_id,case_id,mode)
);
CREATE TABLE IF NOT EXISTS evaluation_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 item_id uuid NOT NULL REFERENCES evaluation_items(id), kind text NOT NULL,
 payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evaluation_events_item_idx ON evaluation_events(item_id,id);
CREATE TABLE IF NOT EXISTS evaluation_calls (
 item_id uuid NOT NULL REFERENCES evaluation_items(id), call_id uuid NOT NULL REFERENCES kb_model_calls(id),
 PRIMARY KEY(item_id,call_id)
);
