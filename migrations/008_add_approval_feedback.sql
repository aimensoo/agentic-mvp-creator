ALTER TABLE pipeline_jobs
ADD COLUMN IF NOT EXISTS approval_feedback TEXT;
