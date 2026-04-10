"""
analyzer/ — Motor de análisis técnico

Exporta las clases principales para uso desde otros módulos.
"""

from .analyzer import TechnicalAnalyzer, AnalysisResult, TradingSignal
from .indicators import TechnicalIndicatorCalculator, TechnicalIndicators
from .levels import SupportResistanceDetector, SupportResistanceResult
from .scorer import SignalScorer, ScoreBreakdown

__all__ = [
    "TechnicalAnalyzer",
    "AnalysisResult",
    "TradingSignal",
    "TechnicalIndicatorCalculator",
    "TechnicalIndicators",
    "SupportResistanceDetector",
    "SupportResistanceResult",
    "SignalScorer",
    "ScoreBreakdown",
]
