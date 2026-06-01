ALTER TABLE pipeline_jobs
ADD COLUMN IF NOT EXISTS opencode_session_history JSONB DEFAULT '[]'::jsonb;

UPDATE pipeline_jobs
SET opencode_session_history = '[]'::jsonb
WHERE opencode_session_history IS NULL;
