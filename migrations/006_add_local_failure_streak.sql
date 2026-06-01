ALTER TABLE pipeline_jobs
ADD COLUMN IF NOT EXISTS local_fix_retries INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS local_failure_signature TEXT,
ADD COLUMN IF NOT EXISTS local_failure_streak INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS local_failure_summary JSONB;

UPDATE pipeline_jobs
SET local_fix_retries = 0
WHERE local_fix_retries IS NULL;

UPDATE pipeline_jobs
SET local_failure_streak = 0
WHERE local_failure_streak IS NULL;
