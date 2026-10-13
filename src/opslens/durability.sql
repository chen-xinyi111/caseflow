CREATE TABLE IF NOT EXISTS durable_tasks (
 family text NOT NULL,task_id uuid NOT NULL,attempt integer NOT NULL DEFAULT 0,
 lease_until timestamptz,heartbeat_at timestamptz,elapsed_seconds double precision NOT NULL DEFAULT 0,
 checkpoint jsonb NOT NULL DEFAULT '{}',stop_reason text,
 PRIMARY KEY(family,task_id));
CREATE TABLE IF NOT EXISTS durable_operations (
 family text NOT NULL,task_id uuid NOT NULL,operation_key text NOT NULL,
 status text NOT NULL,paid boolean NOT NULL DEFAULT false,result jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz,
 PRIMARY KEY(family,task_id,operation_key));
