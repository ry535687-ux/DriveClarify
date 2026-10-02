"""Grounded Language V1 Extension Experiment E1.

This package is additive.  It never imports or mutates the formal Stage6B-R0
ledger and its execution API rejects every split other than extension TRAIN.
"""

from .contracts import EXTENSION_METHODS, ExtensionContractError
from .population import build_population

__all__ = ["EXTENSION_METHODS", "ExtensionContractError", "build_population"]
