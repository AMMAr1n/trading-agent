"""
brain/ — Cerebro IA del agente de trading

Exporta las clases principales para uso desde otros módulos.
"""

from .claude_brain import ClaudeBrain
from .decision import TradeDecision
from .prompt_builder import PromptBuilder

__all__ = [
    "ClaudeBrain",
    "TradeDecision",
    "PromptBuilder",
]
