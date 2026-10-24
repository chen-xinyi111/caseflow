CREATE TABLE IF NOT EXISTS cf_projects (
 id uuid PRIMARY KEY, name text NOT NULL, state jsonb NOT NULL,
 revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cf_tasks (
 id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES cf_projects(id), request_id uuid NOT NULL UNIQUE,
 kind text NOT NULL, input jsonb NOT NULL, status text NOT NULL DEFAULT 'queued', result jsonb, error text,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE TABLE IF NOT EXISTS cf_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, task_id uuid NOT NULL REFERENCES cf_tasks(id),
 kind text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cf_audit (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, project_id uuid NOT NULL REFERENCES cf_projects(id),
 action text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cf_requests (
 id uuid PRIMARY KEY, task_id uuid NOT NULL REFERENCES cf_tasks(id), stage text NOT NULL,
 status text NOT NULL DEFAULT 'issued', usage jsonb, error text, created_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz, UNIQUE(task_id,stage)
);
CREATE INDEX IF NOT EXISTS cf_task_project ON cf_tasks(project_id,created_at);

CREATE TABLE IF NOT EXISTS cf_document_versions (
 document_id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES cf_projects(id),
 source_kind text NOT NULL CHECK(source_kind IN ('file','paste','legacy')),
 format text NOT NULL CHECK(format IN ('pdf','docx','md','txt','legacy_text')),
 original_bytes bytea, original_sha256 text, snapshot jsonb NOT NULL,
 snapshot_sha256 text NOT NULL, parser_signature text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(project_id,document_id,snapshot_sha256),
 CHECK((original_bytes IS NULL) = (original_sha256 IS NULL))
);
CREATE TABLE IF NOT EXISTS cf_evidence (
 id uuid PRIMARY KEY, project_id uuid NOT NULL, document_id uuid NOT NULL,
 snapshot_sha256 text NOT NULL, segments jsonb NOT NULL CHECK(jsonb_typeof(segments)='array'),
 quote text NOT NULL, quote_sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(project_id,document_id,snapshot_sha256)
 REFERENCES cf_document_versions(project_id,document_id,snapshot_sha256)
);
CREATE INDEX IF NOT EXISTS cf_evidence_document ON cf_evidence(project_id,document_id);
CREATE OR REPLACE FUNCTION cf_immutable_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Document versions and evidence are immutable; append a new version instead';
END $$;
DROP TRIGGER IF EXISTS cf_version_immutable ON cf_document_versions;
CREATE TRIGGER cf_version_immutable BEFORE UPDATE OR DELETE ON cf_document_versions
 FOR EACH ROW EXECUTE FUNCTION cf_immutable_source();
DROP TRIGGER IF EXISTS cf_evidence_immutable ON cf_evidence;
CREATE TRIGGER cf_evidence_immutable BEFORE UPDATE OR DELETE ON cf_evidence
 FOR EACH ROW EXECUTE FUNCTION cf_immutable_source();
