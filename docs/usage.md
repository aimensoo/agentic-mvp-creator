# Usage

## Start The Backend

```bash
make run-backend
```

The API will be available at:

```text
http://127.0.0.1:18000
```

## Start The Telegram Adapter

```bash
make run-bot
```

Send the bot a product request:

```text
Build a small CRM where users can create leads, update statuses, add notes, and see a kanban board.
```

The bot creates a pipeline job and replies with the job id.

## Start A Job Through HTTP

```bash
curl -X POST http://127.0.0.1:18000/api/v1/webhook/trigger \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -H "Content-Type: application/json" \
  -d @examples/webhook-request.json
```

## Approval Flow

After spec and plan generation, the backend sends an approval message with:

- `REQUEST.md`
- `SPEC.md`
- `PLAN.md`
- `Approve`, `Reject`, and `KILL` buttons

Rejecting a job asks for feedback. The next Telegram message from the same user is saved as approval feedback and the plan is regenerated.

Approving a job starts workspace preparation and OpenCode execution.
