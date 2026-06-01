CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE pipeline_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    input_text TEXT NOT NULL,
    input_source TEXT NOT NULL DEFAULT 'api',
    chat_id BIGINT,
    telegram_user_id BIGINT,
    status TEXT NOT NULL DEFAULT 'queued',
    error_message TEXT,
    error_step TEXT,
    request_snapshot TEXT,
    spec_text TEXT,
    plan_text TEXT,
    human_approved BOOLEAN DEFAULT FALSE,
    opencode_session_id TEXT,
    opencode_session_history JSONB DEFAULT '[]'::jsonb,
    review_result JSONB,
    github_repo TEXT,
    github_branch TEXT,
    github_commit TEXT,
    github_pr_url TEXT,
    review_retries INTEGER DEFAULT 0,
    local_fix_retries INTEGER DEFAULT 0,
    local_failure_signature TEXT,
    local_failure_streak INTEGER DEFAULT 0,
    local_failure_summary JSONB,
    ci_fix_retries INTEGER DEFAULT 0,
    ci_failure_signature TEXT,
    ci_failure_streak INTEGER DEFAULT 0,
    ci_failure_summary JSONB,
    opencode_stuck_retries INTEGER DEFAULT 0,
    last_activity_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_pipeline_jobs_status ON pipeline_jobs (status);
CREATE INDEX idx_pipeline_jobs_last_activity ON pipeline_jobs (last_activity_at);
