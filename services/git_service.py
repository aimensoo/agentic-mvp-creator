from pathlib import Path

import git
import github as github_lib
from github.GithubException import GithubException, UnknownObjectException
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

from utils.logger import get_logger
from utils.redaction import redact_secrets
from services.quality_gate_service import ensure_mvp_gitignore

logger = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))
DEFAULT_COMMIT_AUTHOR_NAME = "Agentic MVP Creator"
DEFAULT_COMMIT_AUTHOR_EMAIL = "agentic-mvp-creator@users.noreply.github.com"


class NoChangesError(RuntimeError):
    """Raised by force_push when the workspace has no changes to commit."""


class GitResult(BaseModel):
    repo: str
    branch: str
    commit_sha: str
    pr_url: str


class GitService:
    def __init__(
        self,
        github_token: str,
        repo_owner: str,
        repo_name: str,
        repo_private: bool = True,
        commit_author_name: str = DEFAULT_COMMIT_AUTHOR_NAME,
        commit_author_email: str = DEFAULT_COMMIT_AUTHOR_EMAIL,
    ):
        self._github_token = github_token
        self._repo_owner = repo_owner
        self._repo_name_prefix = repo_name
        self._repo_private = repo_private
        self._commit_author_name = commit_author_name.strip() or DEFAULT_COMMIT_AUTHOR_NAME
        self._commit_author_email = commit_author_email.strip() or DEFAULT_COMMIT_AUTHOR_EMAIL

    def push_and_create_pr(
        self,
        job_id: str,
        workspace_path: Path,
        spec_text: str = "",
    ) -> GitResult:
        ensure_mvp_gitignore(workspace_path)
        prepare_ci_yaml(workspace_path)

        gh = github_lib.Github(self._github_token)
        gh_repo = self._get_or_create_job_repo(gh, job_id)
        repo = _get_or_init_repo(workspace_path)
        _configure_commit_identity(repo, self._commit_author_name, self._commit_author_email)

        branch_name = f"job_{job_id}"
        remote_url = f"https://{self._github_token}@github.com/{gh_repo.full_name}.git"
        origin = _configure_origin(repo, remote_url)
        branch_created = _checkout_job_branch(repo, branch_name)
        repo.git.add(".")
        _commit_if_needed(repo, f"feat: generated MVP for job {job_id}")
        if branch_created:
            base_ref = _fetch_default_branch(origin, gh_repo.default_branch)
            if base_ref:
                _merge_default_branch_ours(repo, base_ref)

        push_info = origin.push(f"{branch_name}:refs/heads/{branch_name}")
        _assert_push_ok(push_info)
        logger.info("git_service.pushed", branch=branch_name)

        pr = gh_repo.create_pull(
            title=f"MVP: job {job_id}",
            body=spec_text[:500],
            head=branch_name,
            base=gh_repo.default_branch,
        )

        commit_sha = repo.head.commit.hexsha
        full_repo = gh_repo.full_name

        logger.info(
            "git_service.pr_created",
            pr_url=pr.html_url,
            commit=commit_sha,
        )

        return GitResult(
            repo=full_repo,
            branch=branch_name,
            commit_sha=commit_sha,
            pr_url=pr.html_url,
        )

    def force_push(self, job_id: str, workspace_path: Path) -> str:
        ensure_mvp_gitignore(workspace_path)
        prepare_ci_yaml(workspace_path)
        gh = github_lib.Github(self._github_token)
        gh_repo = self._get_or_create_job_repo(gh, job_id)
        repo = _get_or_init_repo(workspace_path)
        _configure_commit_identity(repo, self._commit_author_name, self._commit_author_email)
        remote_url = f"https://{self._github_token}@github.com/{gh_repo.full_name}.git"
        origin = _configure_origin(repo, remote_url)

        repo.git.add(".")
        if not repo.is_dirty(index=True, working_tree=False, untracked_files=False):
            raise NoChangesError(
                f"force_push: workspace has no staged changes for job {job_id}; OpenCode fix session produced no diff"
            )
        repo.git.commit("--amend", "--no-edit")

        branch_name = f"job_{job_id}"
        _push_force_with_lease(origin, branch_name)
        commit_sha = repo.head.commit.hexsha

        logger.info("git_service.force_pushed", branch=branch_name, commit=commit_sha)
        return commit_sha

    def current_commit(self, workspace_path: Path) -> str:
        repo = _get_or_init_repo(workspace_path)
        commit_sha = repo.head.commit.hexsha
        logger.info("git_service.current_commit_loaded", commit=commit_sha)
        return commit_sha

    def get_pr_diff(self, repo: str, branch: str) -> str:
        gh = github_lib.Github(self._github_token)
        gh_repo = gh.get_repo(repo)
        comparison = gh_repo.compare(gh_repo.default_branch, branch)

        parts = []
        for file in comparison.files:
            patch = file.patch or ""
            parts.append(f"diff -- {file.filename}\n{patch}")

        diff = "\n\n".join(parts)
        logger.info("git_service.pr_diff_loaded", repo=repo, branch=branch, diff_len=len(diff))
        return diff

    def _get_or_create_job_repo(self, gh: github_lib.Github, job_id: str):
        repo_name = self._build_repo_name(job_id)
        full_name = f"{self._repo_owner}/{repo_name}"
        try:
            return gh.get_repo(full_name)
        except UnknownObjectException:
            owner = self._get_owner_handle(gh)
            repo = owner.create_repo(repo_name, private=self._repo_private, auto_init=True)
            logger.info("git_service.repo_created", repo=repo.full_name)
            return repo

    def _get_owner_handle(self, gh: github_lib.Github):
        auth_user = gh.get_user()
        if auth_user.login == self._repo_owner:
            return auth_user

        try:
            return gh.get_organization(self._repo_owner)
        except GithubException as exc:
            raise ValueError(
                f"Repository owner '{self._repo_owner}' must match the authenticated user "
                "or be an accessible organization."
            ) from exc

    def _build_repo_name(self, job_id: str) -> str:
        prefix = _slugify_repo_part(self._repo_name_prefix) or "mvp"
        return f"{prefix}-{_slugify_repo_part(job_id)}"


def prepare_ci_yaml(workspace_path: Path, python_version: str = "3.11", node_version: str = "20") -> None:
    template = _jinja_env.get_template("ci.yml.j2")
    rendered = template.render(python_version=python_version, node_version=node_version)

    ci_dir = workspace_path / ".github" / "workflows"
    ci_dir.mkdir(parents=True, exist_ok=True)
    (ci_dir / "ci.yml").write_text(rendered)
    logger.info("git_service.ci_yaml_generated", path=str(ci_dir / "ci.yml"))


def _get_or_init_repo(workspace_path: Path) -> git.Repo:
    try:
        return git.Repo(workspace_path)
    except git.exc.InvalidGitRepositoryError:
        logger.info("git_service.repo_initialized", path=str(workspace_path))
        return git.Repo.init(workspace_path)


def _configure_origin(repo: git.Repo, remote_url: str):
    if "origin" not in [r.name for r in repo.remotes]:
        return repo.create_remote("origin", remote_url)

    origin = repo.remotes.origin
    origin.set_url(remote_url)
    return origin


def _configure_commit_identity(repo: git.Repo, author_name: str, author_email: str) -> None:
    repo.git.config("user.name", author_name)
    repo.git.config("user.email", author_email)


def _fetch_default_branch(origin, default_branch: str) -> str | None:
    try:
        origin.fetch(default_branch)
        return f"origin/{default_branch}"
    except git.exc.GitCommandError:
        logger.info("git_service.default_branch_fetch_skipped", branch=default_branch)
        return None


def _checkout_job_branch(repo: git.Repo, branch_name: str) -> bool:
    existing_branches = {head.name for head in repo.heads}
    if branch_name in existing_branches:
        repo.git.checkout(branch_name)
        logger.info("git_service.branch_reused", branch=branch_name)
        return False

    repo.git.checkout("-b", branch_name)
    logger.info("git_service.branch_created", branch=branch_name)
    return True


def _merge_default_branch_ours(repo: git.Repo, base_ref: str) -> None:
    try:
        repo.git.merge("--allow-unrelated-histories", "-s", "ours", "--no-edit", base_ref)
        logger.info("git_service.default_branch_merged", base_ref=base_ref)
    except git.exc.GitCommandError as exc:
        if "Already up to date" in str(exc):
            logger.info("git_service.default_branch_merge_skipped", base_ref=base_ref)
            return
        raise


def _push_force_with_lease(origin, branch_name: str) -> None:
    expected_sha = _remote_branch_sha(origin, branch_name)
    try:
        push_info = _push_with_expected_lease(origin, branch_name, expected_sha)
        _assert_push_ok(push_info)
        return
    except RuntimeError as exc:
        if not _is_stale_lease_error(str(exc)):
            raise

        logger.warning("git_service.force_push_stale_lease_retry", branch=branch_name)
        expected_sha = _remote_branch_sha(origin, branch_name)
        retry_push_info = _push_with_expected_lease(origin, branch_name, expected_sha)
        _assert_push_ok(retry_push_info)


def _push_with_expected_lease(origin, branch_name: str, expected_sha: str | None):
    refspec = f"HEAD:refs/heads/{branch_name}"
    if expected_sha:
        lease = f"refs/heads/{branch_name}:{expected_sha}"
        return origin.push(refspec, force_with_lease=lease)
    return origin.push(refspec, force_with_lease=True)


def _remote_branch_sha(origin, branch_name: str) -> str | None:
    try:
        output = origin.repo.git.ls_remote("--heads", origin.name, branch_name).strip()
    except git.exc.GitCommandError as exc:
        logger.warning(
            "git_service.branch_sha_lookup_failed",
            branch=branch_name,
            error=redact_secrets(str(exc)),
        )
        return None

    if not output:
        logger.warning("git_service.branch_sha_missing", branch=branch_name)
        return None

    sha = output.split()[0]
    logger.info("git_service.branch_sha_loaded", branch=branch_name, sha=sha)
    return sha


def _is_stale_lease_error(message: str) -> bool:
    normalized = message.lower()
    return "stale info" in normalized or "fetch first" in normalized


def _commit_if_needed(repo: git.Repo, commit_message: str) -> None:
    if repo.is_dirty(untracked_files=True):
        repo.git.commit("-m", commit_message)
    else:
        logger.info("git_service.no_changes_to_commit", branch=repo.active_branch.name)


def _assert_push_ok(push_info) -> None:
    failure_flags = (
        git.remote.PushInfo.ERROR
        | git.remote.PushInfo.REJECTED
        | git.remote.PushInfo.REMOTE_REJECTED
        | git.remote.PushInfo.REMOTE_FAILURE
    )
    for info in push_info or []:
        if info.flags & failure_flags:
            summary = redact_secrets((info.summary or "").strip())
            raise RuntimeError(f"Git push failed: {summary or info}")


def _slugify_repo_part(value: str) -> str:
    normalized = [char.lower() if char.isalnum() else "-" for char in value]
    slug = "".join(normalized).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug
