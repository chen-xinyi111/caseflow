CREATE TABLE IF NOT EXISTS investigations(
 id uuid PRIMARY KEY,request_id uuid UNIQUE NOT NULL,
 status text NOT NULL CHECK(status IN ('queued','running','completed','failed','blocked')),
 input jsonb NOT NULL,report jsonb,reference_items jsonb NOT NULL DEFAULT '[]',metrics jsonb NOT NULL DEFAULT '{}',
 error text,created_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz);
CREATE TABLE IF NOT EXISTS investigation_events(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,investigation_id uuid NOT NULL REFERENCES investigations(id),
 kind text NOT NULL,payload jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS investigation_events_idx ON investigation_events(investigation_id,id);
CREATE TABLE IF NOT EXISTS investigation_steps(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,investigation_id uuid NOT NULL REFERENCES investigations(id),
 kind text NOT NULL,payload jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS collaboration jsonb NOT NULL DEFAULT '{}';
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS verification jsonb NOT NULL DEFAULT '{}';
