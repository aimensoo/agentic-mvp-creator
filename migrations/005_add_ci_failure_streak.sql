ALTER TABLE pipeline_jobs
ADD COLUMN IF NOT EXISTS ci_failure_signature TEXT,
ADD COLUMN IF NOT EXISTS ci_failure_streak INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS ci_failure_summary JSONB;

UPDATE pipeline_jobs
SET ci_failure_streak = 0
WHERE ci_failure_streak IS NULL;
