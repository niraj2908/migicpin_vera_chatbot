def exceeds_byte_limit(raw_body: bytes, max_bytes: int) -> bool:
    """Enforce the challenge contract's documented /v1/context payload cap (500 KB) against
    the actual wire bytes, not the parsed Python object size (which can differ significantly
    from the serialized form)."""
    return len(raw_body) > max_bytes
