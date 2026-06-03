from pathlib import Path

from app.config import settings
from services.git_service import GitService


def main():
    service = GitService(
        github_token=settings.GITHUB_TOKEN.get_secret_value(),
        repo_owner=settings.GITHUB_REPO_OWNER,
        repo_name=settings.GITHUB_REPO_NAME,
        repo_private=settings.GITHUB_REPO_PRIVATE,
    )

    workspace = Path("workspaces/test-manual")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "main.py").write_text("print('hello')\n")
    (workspace / "requirements.txt").write_text("")

    result = service.push_and_create_pr(
        job_id="manual-test",
        workspace_path=workspace,
        spec_text="Test spec for manual verification",
    )
    print(f"Branch: {result.branch}")
    print(f"PR: {result.pr_url}")
    print(f"Commit: {result.commit_sha}")


if __name__ == "__main__":
    main()
