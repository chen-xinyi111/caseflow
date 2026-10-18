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
