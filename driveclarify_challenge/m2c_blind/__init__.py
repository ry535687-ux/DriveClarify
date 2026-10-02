"""M2C blind integrated challenge authoring package.

This package is authoring-only.  It intentionally exposes no policy evaluator.
"""

from .challenge_contracts import CHALLENGE_SEED_SHA256, SCHEMA_VERSION

__all__ = ["CHALLENGE_SEED_SHA256", "SCHEMA_VERSION"]

