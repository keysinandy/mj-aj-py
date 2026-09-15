"""Credential-safe formatting for platform diagnostics."""

from collections.abc import Mapping


_SECRET_KEYS = frozenset({
    "authorization", "token", "access_token", "bearer", "credential",
    "registration_token", "match_token", "tournament_token",
})


def _secrets(values):
    if values is None:
        return ()
    if isinstance(values, str):
        values = (values,)
    return tuple(sorted({str(value) for value in values if value},
                        key=len, reverse=True))


def redact_text(value, secrets=()):
    """Redact supplied credentials and conventional Bearer header values."""
    text = "" if value is None else str(value)
    for secret in _secrets(secrets):
        text = text.replace(secret, "[REDACTED]")
    original_parts = text.split()
    parts = list(original_parts)
    for index, part in enumerate(original_parts[:-1]):
        if part.lower() in ("bearer", "authorization:", "authorization"):
            parts[index + 1] = "[REDACTED]"
    return " ".join(parts) if parts != original_parts else text


def redact_value(value, secrets=()):
    """Recursively redact secret fields and credential occurrences."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            result[str(key)] = ("[REDACTED]" if normalized in _SECRET_KEYS
                                else redact_value(item, secrets))
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_value(item, secrets) for item in value]
    if isinstance(value, bytes):
        return redact_text(value.decode(errors="replace"), secrets)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value, secrets)
    for method in ("as_json", "to_json"):
        converter = getattr(value, method, None)
        if converter is None:
            continue
        try:
            return redact_value(converter(), secrets)
        except Exception:
            break
    # Recorder/trace payloads occasionally receive an exception or another
    # non-JSON diagnostic object.  The JSON encoder would eventually call
    # str(value); do that at the redaction boundary so that conversion cannot
    # reintroduce a credential after the structured branches above.
    return redact_text(value, secrets)


def redact_exception(exc, secrets=()):
    """Return only safe, useful exception fields."""
    return redact_value({
        "type": type(exc).__name__,
        "message": str(exc),
        "status": getattr(exc, "status", None),
        "code": getattr(exc, "code", ""),
        "uncertain": bool(getattr(exc, "uncertain", False)),
        "timed_out": bool(getattr(exc, "timed_out", False)),
        "attempts": getattr(exc, "attempts", None),
        "body": getattr(exc, "body", None),
    }, secrets)
