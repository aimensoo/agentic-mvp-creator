import asyncio
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://127.0.0.1:18000/api/v1")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

PENDING_REJECT_FEEDBACK: dict[tuple[int, int], str] = {}
TASK_ACCEPTED_MESSAGE = "Принял, скоро отправлю архитектуру на согласование"


async def handle_start(update, context) -> None:
    if update.message:
        await update.message.reply_text("Send a product request and I will start a pipeline job for it.")


async def handle_callback(update, context) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    telegram_user_id = query.from_user.id

    if data.startswith(("pipeline_approve:", "pipeline_reject:", "pipeline_kill:")):
        await handle_pipeline_action_callback(
            query=query,
            callback_data=data,
            telegram_user_id=telegram_user_id,
        )
        return

    await query.edit_message_text(text=_append_callback_result(query, f"Unknown callback: {data}"))


async def handle_pipeline_action_callback(query, callback_data: str, telegram_user_id: int) -> None:
    action, job_id = _parse_pipeline_callback(callback_data)
    try:
        result = await send_pipeline_action(
            job_id=job_id,
            action=action,
            telegram_user_id=telegram_user_id,
        )
    except Exception as exc:
        await query.edit_message_text(
            text=_append_callback_result(
                query,
                f"Pipeline {action} failed: {exc}",
            )
        )
        return

    status = result.get("status", "ok")
    if action == "reject" and status == "feedback_requested":
        PENDING_REJECT_FEEDBACK[(query.message.chat.id, telegram_user_id)] = job_id
        await query.edit_message_text(
            text=_append_callback_result(
                query,
                "Pipeline reject: feedback_requested\nPlease send what should be changed.",
            )
        )
        return

    await query.edit_message_text(text=_append_callback_result(query, f"Pipeline {action}: {status}"))


async def handle_text_message(update, context) -> None:
    message = update.message
    if not message or not message.text or not message.from_user:
        return

    key = (message.chat.id, message.from_user.id)
    job_id = PENDING_REJECT_FEEDBACK.get(key)
    if job_id:
        await _handle_pending_feedback(message, job_id, key)
        return

    user_request = message.text.strip()
    if not user_request:
        return

    try:
        await trigger_pipeline(
            user_request=user_request,
            chat_id=message.chat.id,
            telegram_user_id=message.from_user.id,
        )
    except Exception as exc:
        await message.reply_text(f"Could not start pipeline: {exc}")
        return

    await message.reply_text(TASK_ACCEPTED_MESSAGE)


async def _handle_pending_feedback(message, job_id: str, key: tuple[int, int]) -> None:
    feedback = message.text.strip()
    if not feedback:
        await message.reply_text("Please send what should be changed.")
        return

    try:
        result = await send_pipeline_feedback(
            job_id=job_id,
            feedback=feedback,
            telegram_user_id=message.from_user.id,
        )
    except Exception as exc:
        await message.reply_text(f"Could not send feedback to pipeline: {exc}")
        return

    PENDING_REJECT_FEEDBACK.pop(key, None)
    await message.reply_text(f"Feedback accepted. Pipeline status: {result.get('status', 'ok')}")


async def trigger_pipeline(
    user_request: str,
    chat_id: int,
    telegram_user_id: int,
) -> str:
    if not WEBHOOK_SECRET:
        raise RuntimeError("WEBHOOK_SECRET is not configured")

    payload = {
        "user_request": user_request,
        "input_source": "telegram",
        "chat_id": chat_id,
        "telegram_user_id": telegram_user_id,
    }
    headers = {"X-Webhook-Secret": WEBHOOK_SECRET}

    async with httpx.AsyncClient(base_url=BACKEND_API_URL, timeout=30) as client:
        response = await client.post("/webhook/trigger", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data["job_id"]


async def send_pipeline_action(
    job_id: str,
    action: str,
    telegram_user_id: int,
) -> dict:
    if action not in {"approve", "reject", "kill"}:
        raise RuntimeError(f"Unsupported pipeline action: {action}")

    payload = {"telegram_user_id": telegram_user_id}

    async with httpx.AsyncClient(base_url=BACKEND_API_URL, timeout=30) as client:
        response = await client.post(f"/jobs/{job_id}/{action}", json=payload)
        response.raise_for_status()
        return response.json()


async def send_pipeline_feedback(
    job_id: str,
    feedback: str,
    telegram_user_id: int,
) -> dict:
    payload = {"telegram_user_id": telegram_user_id, "feedback": feedback}

    async with httpx.AsyncClient(base_url=BACKEND_API_URL, timeout=30) as client:
        response = await client.post(f"/jobs/{job_id}/feedback", json=payload)
        response.raise_for_status()
        return response.json()


def _parse_pipeline_callback(callback_data: str) -> tuple[str, str]:
    if callback_data.startswith("pipeline_approve:"):
        return "approve", callback_data.split(":", maxsplit=1)[1]
    if callback_data.startswith("pipeline_reject:"):
        return "reject", callback_data.split(":", maxsplit=1)[1]
    if callback_data.startswith("pipeline_kill:"):
        return "kill", callback_data.split(":", maxsplit=1)[1]
    raise RuntimeError(f"Unsupported pipeline callback: {callback_data}")


def _append_callback_result(query, result_text: str) -> str:
    text = query.message.text or ""
    return f"{text}\n========\n{result_text}"


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    print("Telegram bot started")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
