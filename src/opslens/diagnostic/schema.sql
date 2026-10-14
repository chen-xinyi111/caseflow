CREATE TABLE IF NOT EXISTS diagnostic_sessions (
 id uuid PRIMARY KEY,request_id uuid NOT NULL UNIQUE,input_sha256 text NOT NULL,
 problem text NOT NULL,project text NOT NULL DEFAULT '',environment text NOT NULL DEFAULT '',
 status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','solved','paused')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS diagnostic_sources (
 id uuid PRIMARY KEY,session_id uuid NOT NULL REFERENCES diagnostic_sessions(id),
 request_id uuid NOT NULL UNIQUE,name text NOT NULL,kind text NOT NULL,
 version integer NOT NULL,content text NOT NULL,sha256 text NOT NULL,
 summary jsonb NOT NULL,provenance jsonb NOT NULL DEFAULT '{}',created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS diagnostic_configs (
 id uuid PRIMARY KEY,version integer NOT NULL UNIQUE,settings jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS diagnostic_previews (
 id uuid PRIMARY KEY,request_id uuid NOT NULL UNIQUE,session_id uuid NOT NULL REFERENCES diagnostic_sessions(id),
 config_id uuid NOT NULL REFERENCES diagnostic_configs(id),input_sha256 text NOT NULL,digest text NOT NULL,
 context jsonb NOT NULL,segments jsonb NOT NULL,coverage jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS diagnostic_turns (
 id uuid PRIMARY KEY,session_id uuid NOT NULL REFERENCES diagnostic_sessions(id),request_id uuid NOT NULL UNIQUE,
 preview_id uuid NOT NULL REFERENCES diagnostic_previews(id),status text NOT NULL DEFAULT 'queued',
 report jsonb,metrics jsonb NOT NULL DEFAULT '{}',error text,
 created_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz
);
CREATE TABLE IF NOT EXISTS diagnostic_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,turn_id uuid NOT NULL REFERENCES diagnostic_turns(id),
 kind text NOT NULL,payload jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS diagnostic_requests (
 id uuid PRIMARY KEY,turn_id uuid NOT NULL REFERENCES diagnostic_turns(id),stage text NOT NULL,
 status text NOT NULL DEFAULT 'reserved',usage jsonb,error text,
 created_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz,UNIQUE(turn_id,stage)
);
CREATE TABLE IF NOT EXISTS diagnostic_feedback (
 id uuid PRIMARY KEY,request_id uuid NOT NULL UNIQUE,session_id uuid NOT NULL REFERENCES diagnostic_sessions(id),
 status text NOT NULL,note text NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS diagnostic_sources_session ON diagnostic_sources(session_id,created_at);
CREATE INDEX IF NOT EXISTS diagnostic_turns_session ON diagnostic_turns(session_id,created_at);
