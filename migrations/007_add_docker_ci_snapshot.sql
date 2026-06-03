ALTER TABLE pipeline_jobs
ADD COLUMN IF NOT EXISTS docker_ci_script_path TEXT,
ADD COLUMN IF NOT EXISTS docker_ci_script_sha256 TEXT,
ADD COLUMN IF NOT EXISTS docker_ci_script_content TEXT;
