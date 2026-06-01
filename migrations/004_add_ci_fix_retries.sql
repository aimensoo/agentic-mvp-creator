ALTER TABLE pipeline_jobs
ADD COLUMN IF NOT EXISTS ci_fix_retries INTEGER DEFAULT 0;

UPDATE pipeline_jobs
SET ci_fix_retries = 0
WHERE ci_fix_retries IS NULL;
