"""
mtf_alignment.py — Alineación multi-timeframe
Corre la detección de patrones en múltiples timeframes
y produce un veredicto unificado.

Regla de oro: el timeframe mayor manda la dirección.
El timeframe menor solo confirma el timing de entrada.

Ejemplo:
- 1D muestra ascending triangle bullish → dirección = LONG
- 1h muestra breakout confirmado → timing = AHORA
- Si 1D dice bearish pero 1h dice bullish → CONFLICTO → no operar

v0.7.0
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .patterns import PatternDetector, DetectedPattern
from .breakout import BreakoutValidator, BreakoutValidation
from .targets import TargetCalculator, PatternTargets
from .regime import RegimeDetector, MarketRegime

logger = logging.getLogger(__name__)

# Peso de cada timeframe (mayor = más peso)
TF_WEIGHTS = {
    "1h": 1.0,
    "2h": 1.5,
    "4h": 2.0,
    "1d": 3.0,
    "1w": 4.0,
}


@dataclass
class TimeframeAnalysis:
    """Análisis de un timeframe individual."""
    timeframe: str
    patterns: list[DetectedPattern]
    breakouts: list[BreakoutValidation]
    best_pattern: Optional[DetectedPattern]
    best_breakout: Optional[BreakoutValidation]
    direction: str  # "bullish" | "bearish" | "neutral"
    weight: float


@dataclass
class MTFAlignment:
    """Resultado de la alineación multi-timeframe."""
    aligned: bool                               # ¿Los timeframes están alineados?
    consensus_direction: str                     # "bullish" | "bearish" | "neutral"
    alignment_score: int                         # -30 a +30 puntos para el scorer
    dominant_tf: str                             # Timeframe que manda
    entry_tf: str                                # Timeframe de entrada
    tf_analyses: dict[str, TimeframeAnalysis] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    best_pattern: Optional[DetectedPattern] = None
    best_breakout: Optional[BreakoutValidation] = None
    best_targets: Optional[PatternTargets] = None
    regime: Optional[MarketRegime] = None

    def prompt_section(self) -> str:
        """Genera la sección completa de MTF para el prompt de Claude."""
        lines = []

        # Régimen
        if self.regime:
            lines.append("=== RÉGIMEN DE MERCADO ===")
            lines.append(self.regime.prompt_section())
            lines.append("")

        # Patrones por timeframe
        lines.append("=== CHART PATTERNS DETECTADOS ===")
        found_any = False
        for tf_name, analysis in sorted(self.tf_analyses.items(),
                                        key=lambda x: TF_WEIGHTS.get(x[0], 1)):
            if analysis.patterns:
                found_any = True
                for p in analysis.patterns[:2]:  # Max 2 por TF
                    lines.append(f"  [{tf_name}] {p.prompt_line()}")

        if not found_any:
            lines.append("  No se detectaron patrones chartistas claros.")
        lines.append("")

        # Alineación
        emoji = "✅" if self.aligned else "⚠️"
        lines.append(f"=== ALINEACIÓN MULTI-TIMEFRAME {emoji} ===")
        lines.append(f"Consenso: {self.consensus_direction.upper()} "
                     f"(TF dominante: {self.dominant_tf})")
        lines.append(f"Score de alineación: {self.alignment_score:+d} puntos")

        if self.conflicts:
            lines.append(f"Conflictos: {'; '.join(self.conflicts)}")
        elif self.aligned:
            lines.append("Todos los timeframes coinciden en dirección.")

        # Breakout
        if self.best_breakout and self.best_pattern and self.best_pattern.breakout_occurred:
            lines.append("")
            lines.append("=== VALIDACIÓN DE BREAKOUT ===")
            lines.append(self.best_breakout.prompt_section())

        # Targets
        if self.best_targets and self.best_targets.is_valid_setup:
            lines.append("")
            lines.append("=== TARGETS POR GEOMETRÍA DEL PATRÓN ===")
            lines.append(self.best_targets.prompt_section())

        return "\n".join(lines)


class MTFAligner:
    """
    Orquesta la detección de patrones en múltiples timeframes
    y produce un veredicto unificado.

    Usage:
        aligner = MTFAligner()
        result = aligner.analyze(candles_by_tf, symbol)
    """

    def __init__(self):
        self.pattern_detector = PatternDetector()
        self.breakout_validator = BreakoutValidator()
        self.target_calculator = TargetCalculator()
        self.regime_detector = RegimeDetector()

    def _analyze_single_tf(self, df: pd.DataFrame, symbol: str, tf: str) -> TimeframeAnalysis:
        """Analiza un solo timeframe."""
        patterns = self.pattern_detector.detect_all(df, symbol, tf)

        breakouts = []
        for p in patterns:
            if p.breakout_occurred:
                bv = self.breakout_validator.validate(df, p)
                breakouts.append(bv)
            else:
                breakouts.append(None)

        # Mejor patrón = mayor confianza
        best_pattern = patterns[0] if patterns else None
        best_breakout = breakouts[0] if breakouts and breakouts[0] else None

        # Dirección del TF basada en el mejor patrón
        if best_pattern:
            direction = best_pattern.direction
        else:
            direction = "neutral"

        return TimeframeAnalysis(
            timeframe=tf,
            patterns=patterns,
            breakouts=[b for b in breakouts if b],
            best_pattern=best_pattern,
            best_breakout=best_breakout,
            direction=direction,
            weight=TF_WEIGHTS.get(tf, 1.0),
        )

    def analyze(
        self,
        candles_by_tf: dict[str, pd.DataFrame],
        symbol: str
    ) -> MTFAlignment:
        """
        Analiza múltiples timeframes y produce un veredicto.

        Args:
            candles_by_tf: Dict de {timeframe: DataFrame}.
                          Ej: {"1h": df_1h, "4h": df_4h, "1d": df_1d}
            symbol: Par de trading.

        Returns:
            MTFAlignment con el veredicto completo.
        """
        tf_analyses = {}

        for tf, df in candles_by_tf.items():
            if df is not None and len(df) >= 30:
                tf_analyses[tf] = self._analyze_single_tf(df, symbol, tf)

        if not tf_analyses:
            return MTFAlignment(
                aligned=False, consensus_direction="neutral",
                alignment_score=0, dominant_tf="none", entry_tf="none"
            )

        # Detectar régimen en el TF más grande disponible
        regime = None
        for tf in ["1d", "4h", "2h", "1h"]:
            if tf in candles_by_tf and candles_by_tf[tf] is not None and len(candles_by_tf[tf]) >= 50:
                regime = self.regime_detector.detect(candles_by_tf[tf])
                if regime:
                    break

        # Calcular dirección de consenso (weighted vote)
        bullish_weight = 0
        bearish_weight = 0
        for analysis in tf_analyses.values():
            if analysis.direction == "bullish":
                bullish_weight += analysis.weight
            elif analysis.direction == "bearish":
                bearish_weight += analysis.weight

        total_weight = bullish_weight + bearish_weight
        if total_weight == 0:
            consensus = "neutral"
        elif bullish_weight > bearish_weight * 1.3:
            consensus = "bullish"
        elif bearish_weight > bullish_weight * 1.3:
            consensus = "bearish"
        else:
            consensus = "neutral"  # Demasiado cerrado = conflicto

        # Determinar TF dominante y de entrada
        sorted_tfs = sorted(tf_analyses.items(), key=lambda x: x[1].weight, reverse=True)
        dominant_tf = sorted_tfs[0][0]
        entry_tf = sorted_tfs[-1][0] if len(sorted_tfs) > 1 else dominant_tf

        # Detectar conflictos
        conflicts = []
        dominant_dir = tf_analyses[dominant_tf].direction
        for tf, analysis in tf_analyses.items():
            if tf != dominant_tf and analysis.direction != "neutral":
                if analysis.direction != dominant_dir and dominant_dir != "neutral":
                    conflicts.append(
                        f"{tf} dice {analysis.direction} vs {dominant_tf} dice {dominant_dir}"
                    )

        # Alineación
        aligned = len(conflicts) == 0 and consensus != "neutral"

        # Score de alineación para el scorer
        if aligned and consensus != "neutral":
            alignment_score = 20  # Bonus por alineación completa
        elif len(conflicts) == 0:
            alignment_score = 5   # Neutral, sin conflictos
        elif len(conflicts) == 1:
            alignment_score = -10  # Un conflicto
        else:
            alignment_score = -20  # Múltiples conflictos

        # Encontrar el mejor patrón global (mayor confianza con breakout)
        best_pattern = None
        best_breakout = None
        best_confidence = 0

        for analysis in tf_analyses.values():
            if analysis.best_pattern and analysis.best_pattern.confidence > best_confidence:
                if analysis.best_pattern.direction == consensus or consensus == "neutral":
                    best_confidence = analysis.best_pattern.confidence
                    best_pattern = analysis.best_pattern
                    best_breakout = analysis.best_breakout

        # Calcular targets si hay patrón con breakout
        best_targets = None
        if best_pattern:
            tp_mult = regime.tp_multiplier if regime else 1.0
            sl_mult = regime.sl_multiplier if regime else 1.0
            best_targets = self.target_calculator.calculate(
                best_pattern, best_breakout, tp_mult, sl_mult
            )

        result = MTFAlignment(
            aligned=aligned,
            consensus_direction=consensus,
            alignment_score=alignment_score,
            dominant_tf=dominant_tf,
            entry_tf=entry_tf,
            tf_analyses=tf_analyses,
            conflicts=conflicts,
            best_pattern=best_pattern,
            best_breakout=best_breakout,
            best_targets=best_targets,
            regime=regime,
        )

        logger.info(
            f"{symbol} MTF: {consensus} | Aligned: {aligned} | "
            f"Score: {alignment_score:+d} | "
            f"Pattern: {best_pattern.pattern_type if best_pattern else 'none'} | "
            f"Conflicts: {len(conflicts)}"
        )

        return result
