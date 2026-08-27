import os

_SECRET_ENV_VARS = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY")


def redact_secrets(text: str) -> str:
    """Defense in depth: even though nothing in this codebase ever puts a key into an exception
    message or log field on purpose, strip any configured secret's literal value out of a string
    before it's logged or reported, in case a provider SDK ever echoes one back in an error."""
    for var in _SECRET_ENV_VARS:
        value = os.environ.get(var)
        if value:
            text = text.replace(value, "[REDACTED]")
    return text
