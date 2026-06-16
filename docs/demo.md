# Demo Recording Guide

The strongest demo for agentic-mvp-creator is the end-to-end Telegram flow:

```text
Telegram message -> architecture approval -> OpenCode build -> quality checks -> GitHub PR -> CI/review result
```

Record the demo from a real local or hosted run. Do not use mock secrets, private customer data, or generated repositories that expose tokens.

## Suggested Shot List

1. Telegram bot receives a concise MVP request.
2. Bot replies that the architecture will be sent for approval.
3. Approval message shows the generated request/spec/plan artifacts.
4. GitHub repository receives the generated PR.
5. CI and quality checks are visible.
6. Final Telegram message links to the PR or reports the required manual action.

## Before Recording

- Use a throwaway Telegram bot token.
- Use a throwaway GitHub repository owner or organization.
- Verify `.env`, OpenCode auth files, and provider keys are not visible.
- Keep browser tabs focused on Telegram, GitHub PR, and Actions only.
- Use a short request that completes in a predictable time.

## Recommended Output

- Short GIF for README and launch posts: 20-40 seconds.
- Longer video for articles: 2-4 minutes.
- Store public assets outside the repository if they are large, and link them from README.
