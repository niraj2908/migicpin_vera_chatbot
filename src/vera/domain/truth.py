from enum import Enum


class TruthLevel(str, Enum):
    """Provenance of a piece of context, so the pipeline never treats a guess as a fact."""

    FACT = "FACT"
    DERIVED = "DERIVED"
    INFERRED = "INFERRED"
    UNTRUSTED = "UNTRUSTED"
    UNKNOWN = "UNKNOWN"
