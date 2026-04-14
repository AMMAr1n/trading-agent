"""
analyzer/ — Motor de análisis técnico
v0.7.0 — Incluye chart patterns, breakout, targets, MTF, regime, learning.
"""

from .analyzer import TechnicalAnalyzer, AnalysisResult, TradingSignal
from .indicators import TechnicalIndicatorCalculator, TechnicalIndicators
from .levels import SupportResistanceDetector, SupportResistanceResult
from .scorer import SignalScorer, ScoreBreakdown
from .patterns import PatternDetector, DetectedPattern
from .breakout import BreakoutValidator, BreakoutValidation
from .targets import TargetCalculator, PatternTargets
from .mtf_alignment import MTFAligner, MTFAlignment
from .regime import RegimeDetector, MarketRegime
from .learning import LearningEngine, LearningContext

__all__ = [
    "TechnicalAnalyzer", "AnalysisResult", "TradingSignal",
    "TechnicalIndicatorCalculator", "TechnicalIndicators",
    "SupportResistanceDetector", "SupportResistanceResult",
    "SignalScorer", "ScoreBreakdown",
    "PatternDetector", "DetectedPattern",
    "BreakoutValidator", "BreakoutValidation",
    "TargetCalculator", "PatternTargets",
    "MTFAligner", "MTFAlignment",
    "RegimeDetector", "MarketRegime",
    "LearningEngine", "LearningContext",
]
