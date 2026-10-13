CREATE TABLE IF NOT EXISTS evaluation_reviews (
 item_id uuid PRIMARY KEY REFERENCES evaluation_items(id), reviewer text NOT NULL,
 review jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
