import subprocess

import pytest
import git
import yaml
from unittest.mock import MagicMock, call, patch
from pathlib import Path
from github.GithubException import UnknownObjectException

from services.git_service import (
    DEFAULT_COMMIT_AUTHOR_EMAIL,
    DEFAULT_COMMIT_AUTHOR_NAME,
    GitResult,
    GitService,
    NoChangesError,
    prepare_ci_yaml,
)


@pytest.fixture
def service():
    return GitService(
        github_token="ghp_test123",
        repo_owner="testowner",
        repo_name="testrepo",
    )


def _make_mock_repo():
    repo = MagicMock()
    repo.git = MagicMock()
    repo.head.commit.hexsha = "abc123def"
    repo.heads = []
    repo.is_dirty.return_value = True
    repo.active_branch.name = "main"
    repo.remotes = MagicMock()
    repo.remotes.__iter__ = MagicMock(return_value=iter([]))
    origin = MagicMock()
    repo.create_remote.return_value = origin
    repo.remotes.origin = origin
    return repo, origin


def _make_mock_github(full_name: str = "testowner/testrepo-j1", default_branch: str = "main"):
    gh_repo = MagicMock()
    gh_repo.html_url = f"https://github.com/{full_name}"
    gh_repo.full_name = full_name
    gh_repo.default_branch = default_branch
    gh_repo.create_pull.return_value.html_url = f"https://github.com/{full_name}/pull/1"

    gh = MagicMock()
    gh.get_repo.return_value = gh_repo
    gh.get_user.return_value.login = "testowner"
    return gh, gh_repo


def _assert_identity_configured_before_commit(mock_repo, commit_call):
    calls = mock_repo.git.method_calls
    name_call = call.config("user.name", DEFAULT_COMMIT_AUTHOR_NAME)
    email_call = call.config("user.email", DEFAULT_COMMIT_AUTHOR_EMAIL)

    assert name_call in calls
    assert email_call in calls
    assert calls.index(name_call) < calls.index(commit_call)
    assert calls.index(email_call) < calls.index(commit_call)


def test_push_and_create_pr(service, tmp_path):
    mock_repo, mock_origin = _make_mock_repo()
    mock_gh, mock_gh_repo = _make_mock_github(full_name="testowner/testrepo-job-456")

    with (
        patch("services.git_service.git.Repo", return_value=mock_repo),
        patch("services.git_service.prepare_ci_yaml"),
        patch("services.git_service.github_lib.Github", return_value=mock_gh),
    ):
        result = service.push_and_create_pr(
            job_id="job-456",
            workspace_path=tmp_path,
            spec_text="This is the spec text for the MVP",
        )

    assert isinstance(result, GitResult)
    assert result.branch == "job_job-456"
    assert result.commit_sha == "abc123def"
    assert result.pr_url == "https://github.com/testowner/testrepo-job-456/pull/1"
    assert result.repo == "testowner/testrepo-job-456"

    mock_repo.git.checkout.assert_called_once_with("-b", "job_job-456")
    mock_repo.git.add.assert_called_once_with(".")
    mock_repo.git.commit.assert_called_once_with("-m", "feat: generated MVP for job job-456")
    _assert_identity_configured_before_commit(
        mock_repo,
        call.commit("-m", "feat: generated MVP for job job-456"),
    )
    mock_origin.fetch.assert_called_once_with("main")
    mock_repo.git.merge.assert_called_once_with(
        "--allow-unrelated-histories",
        "-s",
        "ours",
        "--no-edit",
        "origin/main",
    )
    mock_origin.push.assert_called_once()

    pr_call = mock_gh_repo.create_pull.call_args
    assert pr_call.kwargs["title"] == "MVP: job job-456"
    assert "This is the spec text" in pr_call.kwargs["body"]
    assert pr_call.kwargs["head"] == "job_job-456"
    assert pr_call.kwargs["base"] == "main"
    gitignore = (tmp_path / ".gitignore").read_text()
    assert "node_modules/" in gitignore
    assert ".venv/" in gitignore


def test_push_and_create_pr_initializes_repo_when_missing(service, tmp_path):
    mock_repo, _ = _make_mock_repo()
    mock_gh, _ = _make_mock_github()

    with (
        patch(
            "services.git_service.git.Repo",
            side_effect=git.exc.InvalidGitRepositoryError(str(tmp_path)),
        ),
        patch(
            "services.git_service.git.Repo.init",
            return_value=mock_repo,
        ) as mock_init,
        patch(
            "services.git_service.prepare_ci_yaml",
        ),
        patch(
            "services.git_service.github_lib.Github",
            return_value=mock_gh,
        ),
    ):
        result = service.push_and_create_pr("j1", tmp_path, "spec")

    mock_init.assert_called_once_with(tmp_path)
    assert result.branch == "job_j1"


def test_push_uses_existing_remote(service, tmp_path):
    mock_repo = MagicMock()
    mock_repo.git = MagicMock()
    mock_repo.head.commit.hexsha = "sha1"
    mock_repo.heads = []
    mock_repo.is_dirty.return_value = True
    mock_repo.active_branch.name = "main"

    existing_origin = MagicMock()
    existing_origin.name = "origin"
    mock_repo.remotes.__iter__ = MagicMock(return_value=iter([existing_origin]))
    mock_repo.remotes.origin = existing_origin

    mock_gh, _ = _make_mock_github(full_name="testowner/testrepo-j1")

    with (
        patch("services.git_service.git.Repo", return_value=mock_repo),
        patch("services.git_service.prepare_ci_yaml"),
        patch("services.git_service.github_lib.Github", return_value=mock_gh),
    ):
        result = service.push_and_create_pr("j1", tmp_path, "spec")

    existing_origin.set_url.assert_called_once()
    existing_origin.push.assert_called_once()
    assert result.branch == "job_j1"


def test_force_push(service, tmp_path):
    mock_repo = MagicMock()
    mock_repo.git = MagicMock()
    mock_repo.heads = []
    mock_repo.head.commit.hexsha = "abc123def"
    mock_repo.git.ls_remote.return_value = "remote123\trefs/heads/job_job-789\n"
    mock_origin = MagicMock()
    mock_origin.name = "origin"
    mock_origin.repo = mock_repo
    mock_repo.remotes.__iter__ = MagicMock(return_value=iter([mock_origin]))
    mock_repo.remotes.origin = mock_origin
    mock_gh, _ = _make_mock_github(full_name="testowner/testrepo-job-789")

    with (
        patch("services.git_service.git.Repo", return_value=mock_repo),
        patch("services.git_service.github_lib.Github", return_value=mock_gh),
    ):
        commit_sha = service.force_push(job_id="job-789", workspace_path=tmp_path)

    mock_repo.git.add.assert_called_once_with(".")
    mock_repo.git.commit.assert_called_once_with("--amend", "--no-edit")
    _assert_identity_configured_before_commit(mock_repo, call.commit("--amend", "--no-edit"))
    mock_repo.git.ls_remote.assert_called_once_with("--heads", "origin", "job_job-789")
    mock_origin.push.assert_called_once()
    call_args = mock_origin.push.call_args
    assert "job_job-789" in call_args[0][0]
    assert call_args[1].get("force_with_lease") == "refs/heads/job_job-789:remote123"
    assert commit_sha == mock_repo.head.commit.hexsha
    ci_yaml = (tmp_path / ".github" / "workflows" / "ci.yml").read_text()
    assert "Run project Docker CI" in ci_yaml
    assert "./.pipeline/docker-ci.sh" in ci_yaml


def test_force_push_retries_after_stale_lease(service, tmp_path):
    mock_repo = MagicMock()
    mock_repo.git = MagicMock()
    mock_repo.heads = []
    mock_repo.head.commit.hexsha = "retrysha"
    mock_repo.git.ls_remote.side_effect = [
        "oldsha\trefs/heads/job_job-789\n",
        "newsha\trefs/heads/job_job-789\n",
    ]
    mock_origin = MagicMock()
    mock_origin.name = "origin"
    mock_origin.repo = mock_repo
    mock_repo.remotes.__iter__ = MagicMock(return_value=iter([mock_origin]))
    mock_repo.remotes.origin = mock_origin
    mock_gh, _ = _make_mock_github(full_name="testowner/testrepo-job-789")

    rejected_push = MagicMock()
    rejected_push.flags = git.remote.PushInfo.REJECTED
    rejected_push.summary = "[rejected] (stale info)"
    ok_push = MagicMock()
    ok_push.flags = 0
    ok_push.summary = ""
    mock_origin.push.side_effect = [[rejected_push], [ok_push]]

    with (
        patch("services.git_service.git.Repo", return_value=mock_repo),
        patch("services.git_service.github_lib.Github", return_value=mock_gh),
    ):
        commit_sha = service.force_push(job_id="job-789", workspace_path=tmp_path)

    assert mock_repo.git.ls_remote.call_count == 2
    mock_repo.git.ls_remote.assert_called_with("--heads", "origin", "job_job-789")
    assert mock_origin.push.call_count == 2
    assert mock_origin.push.call_args_list[0].kwargs["force_with_lease"] == "refs/heads/job_job-789:oldsha"
    assert mock_origin.push.call_args_list[1].kwargs["force_with_lease"] == "refs/heads/job_job-789:newsha"
    assert commit_sha == "retrysha"


def test_push_and_create_pr_truncates_spec_body(service, tmp_path):
    long_spec = "x" * 1000

    mock_repo, _ = _make_mock_repo()
    mock_gh, mock_gh_repo = _make_mock_github()

    with (
        patch("services.git_service.git.Repo", return_value=mock_repo),
        patch("services.git_service.prepare_ci_yaml"),
        patch("services.git_service.github_lib.Github", return_value=mock_gh),
    ):
        result = service.push_and_create_pr("j1", tmp_path, long_spec)

    body = mock_gh_repo.create_pull.call_args.kwargs["body"]
    assert len(body) <= 500


def test_push_and_create_pr_reuses_existing_branch_and_skips_empty_commit(service, tmp_path):
    mock_repo, mock_origin = _make_mock_repo()
    mock_repo.heads = [MagicMock(name="job_j1")]
    mock_repo.heads[0].name = "job_j1"
    mock_repo.is_dirty.return_value = False
    mock_repo.active_branch.name = "job_j1"

    mock_gh, _ = _make_mock_github()

    with (
        patch("services.git_service.git.Repo", return_value=mock_repo),
        patch("services.git_service.prepare_ci_yaml"),
        patch("services.git_service.github_lib.Github", return_value=mock_gh),
    ):
        result = service.push_and_create_pr("j1", tmp_path, "spec")

    mock_repo.git.checkout.assert_called_once_with("job_j1")
    mock_repo.git.commit.assert_not_called()
    mock_origin.fetch.assert_not_called()
    mock_repo.git.merge.assert_not_called()
    mock_origin.push.assert_called_once()
    assert result.branch == "job_j1"


def test_push_and_create_pr_raises_when_push_is_rejected(service, tmp_path):
    mock_repo, mock_origin = _make_mock_repo()
    mock_gh, mock_gh_repo = _make_mock_github()
    rejected_push = MagicMock()
    rejected_push.flags = git.remote.PushInfo.REMOTE_REJECTED
    rejected_push.summary = "remote rejected: missing workflow scope"
    mock_origin.push.return_value = [rejected_push]

    with (
        patch("services.git_service.git.Repo", return_value=mock_repo),
        patch("services.git_service.prepare_ci_yaml"),
        patch("services.git_service.github_lib.Github", return_value=mock_gh),
    ):
        with pytest.raises(RuntimeError, match="missing workflow scope"):
            service.push_and_create_pr("j1", tmp_path, "spec")

    mock_gh_repo.create_pull.assert_not_called()


def test_push_and_create_pr_creates_repo_for_job_when_missing(service, tmp_path):
    mock_repo, _ = _make_mock_repo()
    mock_gh, mock_gh_repo = _make_mock_github(full_name="testowner/testrepo-manual-test")
    mock_gh.get_repo.side_effect = UnknownObjectException(404, {"message": "Not Found"}, None)
    mock_gh.get_user.return_value.create_repo.return_value = mock_gh_repo

    with (
        patch("services.git_service.git.Repo", return_value=mock_repo),
        patch("services.git_service.prepare_ci_yaml"),
        patch("services.git_service.github_lib.Github", return_value=mock_gh),
    ):
        result = service.push_and_create_pr("manual-test", tmp_path, "spec")

    mock_gh.get_user.return_value.create_repo.assert_called_once_with(
        "testrepo-manual-test",
        private=True,
        auto_init=True,
    )
    assert result.repo == "testowner/testrepo-manual-test"


def test_push_and_create_pr_creates_repo_in_org_when_owner_is_organization(service, tmp_path):
    service = GitService(
        github_token="ghp_test123",
        repo_owner="testorg",
        repo_name="testrepo",
    )
    mock_repo, _ = _make_mock_repo()
    mock_gh, mock_gh_repo = _make_mock_github(full_name="testorg/testrepo-manual-test")
    mock_gh.get_repo.side_effect = UnknownObjectException(404, {"message": "Not Found"}, None)
    mock_gh.get_user.return_value.login = "testowner"
    mock_org = MagicMock()
    mock_org.create_repo.return_value = mock_gh_repo
    mock_gh.get_organization.return_value = mock_org

    with (
        patch("services.git_service.git.Repo", return_value=mock_repo),
        patch("services.git_service.prepare_ci_yaml"),
        patch("services.git_service.github_lib.Github", return_value=mock_gh),
    ):
        result = service.push_and_create_pr("manual-test", tmp_path, "spec")

    mock_org.create_repo.assert_called_once_with("testrepo-manual-test", private=True, auto_init=True)
    assert result.repo == "testorg/testrepo-manual-test"


def test_prepare_ci_yaml_supports_python_and_node(tmp_path):
    prepare_ci_yaml(tmp_path, python_version="3.11", node_version="20")

    ci_yaml = (tmp_path / ".github" / "workflows" / "ci.yml").read_text()

    assert "actions/setup-python@v5" in ci_yaml
    assert 'python-version: "3.11"' in ci_yaml
    assert "actions/setup-node@v4" in ci_yaml
    assert 'node-version: "20"' in ci_yaml
    assert "npm test" in ci_yaml
    assert "python -m pytest --tb=short -q" in ci_yaml
    assert "postgres_services_for_compose" in ci_yaml
    assert 'start_postgres_for_node_tests "$TEST_COMPOSE_FILE"' in ci_yaml
    assert 'docker compose -f "$compose_file" up -d $postgres_services' in ci_yaml
    assert "pg_isready" in ci_yaml
    assert "Created $target_env from $template for CI test dependencies." in ci_yaml
    assert "docker-runtime:" in ci_yaml
    assert "Run project Docker CI" in ci_yaml
    assert "Project Docker CI script is required at .pipeline/docker-ci.sh" in ci_yaml
    assert "./.pipeline/docker-ci.sh" in ci_yaml
    assert "python -m playwright install --with-deps chromium" in ci_yaml
    assert "Run browser smoke flow" in ci_yaml
    assert "mvp.config.json must use contract v2" in ci_yaml


def test_prepare_ci_yaml_renders_parseable_workflow_and_bash(tmp_path):
    prepare_ci_yaml(tmp_path)

    ci_yaml = (tmp_path / ".github" / "workflows" / "ci.yml").read_text()
    workflow = yaml.safe_load(ci_yaml)

    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "run" not in step:
                continue
            result = subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True)
            assert result.returncode == 0, result.stderr


def test_force_push_raises_no_changes_error_when_workspace_unchanged(service, tmp_path):
    mock_repo = MagicMock()
    mock_repo.git = MagicMock()
    mock_repo.heads = []
    mock_repo.head.commit.hexsha = "abc123"
    mock_repo.is_dirty.return_value = False
    mock_origin = MagicMock()
    mock_origin.name = "origin"
    mock_origin.repo = mock_repo
    mock_repo.remotes.__iter__ = MagicMock(return_value=iter([mock_origin]))
    mock_repo.remotes.origin = mock_origin
    mock_gh, _ = _make_mock_github(full_name="testowner/testrepo-job-999")

    with (
        patch("services.git_service.git.Repo", return_value=mock_repo),
        patch("services.git_service.github_lib.Github", return_value=mock_gh),
    ):
        with pytest.raises(NoChangesError):
            service.force_push(job_id="job-999", workspace_path=tmp_path)

    mock_repo.git.add.assert_called_once_with(".")
    mock_repo.git.commit.assert_not_called()
    mock_origin.push.assert_not_called()


def test_current_commit_returns_workspace_head(service, tmp_path):
    mock_repo = MagicMock()
    mock_repo.head.commit.hexsha = "abc123"

    with patch("services.git_service.git.Repo", return_value=mock_repo):
        assert service.current_commit(tmp_path) == "abc123"
