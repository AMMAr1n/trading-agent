"""
v0.7.0 — Nuevos módulos del analyzer

Módulos:
  patterns.py       — Detector de chart patterns (12 formaciones)
  breakout.py       — Validador de breakouts
  targets.py        — Calculador de targets por geometría
  mtf_alignment.py  — Alineación multi-timeframe
  regime.py         — Detector de régimen de mercado
  learning.py       — Motor de aprendizaje evolutivo
"""

from .patterns import PatternDetector, DetectedPattern
from .breakout import BreakoutValidator, BreakoutValidation
from .targets import TargetCalculator, PatternTargets
from .mtf_alignment import MTFAligner, MTFAlignment
from .regime import RegimeDetector, MarketRegime
from .learning import LearningEngine, LearningContext

__all__ = [
    "PatternDetector", "DetectedPattern",
    "BreakoutValidator", "BreakoutValidation",
    "TargetCalculator", "PatternTargets",
    "MTFAligner", "MTFAlignment",
    "RegimeDetector", "MarketRegime",
    "LearningEngine", "LearningContext",
]
