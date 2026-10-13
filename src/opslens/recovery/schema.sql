CREATE TABLE IF NOT EXISTS recovery_plans(
 id uuid PRIMARY KEY, request_id uuid UNIQUE NOT NULL, investigation_id uuid NOT NULL REFERENCES investigations(id),
 version integer NOT NULL, action text NOT NULL, target text NOT NULL DEFAULT 'fault' CHECK(target='fault'),
 title text NOT NULL,explanation text NOT NULL,risk text NOT NULL,digest text NOT NULL,before jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('proposed','approved','executing','verifying','completed','failed','blocked','manual','superseded','cancelled')),
 approval jsonb,execution jsonb NOT NULL DEFAULT '{}',result jsonb,error text,
 created_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL,finished_at timestamptz,
 UNIQUE(investigation_id,version));
CREATE TABLE IF NOT EXISTS recovery_events(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,plan_id uuid NOT NULL REFERENCES recovery_plans(id),
 kind text NOT NULL,payload jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS recovery_actions(
 id uuid PRIMARY KEY,plan_id uuid UNIQUE NOT NULL REFERENCES recovery_plans(id),action text NOT NULL,
 status text NOT NULL,issued_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz,result jsonb);
