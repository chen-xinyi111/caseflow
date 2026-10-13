CREATE TABLE IF NOT EXISTS external_targets (
 id uuid PRIMARY KEY, name text NOT NULL, compose_project text NOT NULL UNIQUE,
 services jsonb NOT NULL, log_services jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS external_documents (
 id uuid PRIMARY KEY,target_id uuid NOT NULL REFERENCES external_targets(id),
 title text NOT NULL,content text NOT NULL,sha256 text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(target_id,title,sha256)
);
CREATE TABLE IF NOT EXISTS external_snapshots (
 id uuid PRIMARY KEY,request_id uuid NOT NULL UNIQUE,target_id uuid NOT NULL REFERENCES external_targets(id),
 captured_at timestamptz NOT NULL DEFAULT now(),items jsonb NOT NULL,documents jsonb NOT NULL,
 sha256 text NOT NULL,errors jsonb NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS external_consents (
 id uuid PRIMARY KEY,snapshot_id uuid NOT NULL REFERENCES external_snapshots(id),
 digest text NOT NULL,selected_ids jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(snapshot_id,digest)
);
