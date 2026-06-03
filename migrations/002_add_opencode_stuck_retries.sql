ALTER TABLE pipeline_jobs
ADD COLUMN IF NOT EXISTS opencode_stuck_retries INTEGER DEFAULT 0;
