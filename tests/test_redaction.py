from utils.redaction import redact_secrets


def test_redacts_github_token_patterns():
    text = "github_pat_SECRET123 and ghp_SECRET456"

    redacted = redact_secrets(text)

    assert "github_pat_SECRET123" not in redacted
    assert "ghp_SECRET456" not in redacted
    assert redacted == "*** and ***"


def test_redacts_telegram_bot_token_patterns():
    text = "8684579329:AAF4oPjhITah80P7IAK4oetrQX2CJih2Ymk"

    redacted = redact_secrets(text)

    assert "AAF4oPjh" not in redacted
    assert redacted == "***"


def test_redacts_url_credentials():
    text = "https://github_pat_SECRET123@github.com/owner/repo.git"

    redacted = redact_secrets(text)

    assert redacted == "https://***@github.com/owner/repo.git"


def test_redacts_telegram_bot_url():
    text = "https://api.telegram.org/bot8684579329:AAF4oPjhITah80P7IAK4oetrQX2CJih2Ymk/sendMessage"

    redacted = redact_secrets(text)

    assert "AAF4oPjh" not in redacted
    assert redacted == "https://api.telegram.org/bot***/sendMessage"


def test_formats_empty_exception_message_with_type():
    class EmptyError(Exception):
        pass

    redacted = redact_secrets(EmptyError())

    assert redacted == "EmptyError"
