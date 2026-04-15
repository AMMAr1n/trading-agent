"""
mtf_alignment.py — Alineación multi-timeframe
v0.7.1 — Enfoque top-down + parámetros ajustados por timeframe.

Enfoque top-down:
  1D/1W → Detectar patrones macro (dirección principal)
  4h    → Confirmar el patrón y detectar breakout
  1h    → Solo timing de entrada (breakout reciente, retest)

Si 1D/1W detecta un patrón claro en dirección contraria a 1h,
la señal se descarta (veto del timeframe mayor).
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

# Peso y rol de cada timeframe
TF_CONFIG = {
    "1w": {"weight": 4.0, "role": "macro",        "min_bars": 8,  "tolerance": 2.0, "swing_window": 3},
    "1d": {"weight": 3.0, "role": "primary",       "min_bars": 12, "tolerance": 1.8, "swing_window": 4},
    "4h": {"weight": 2.0, "role": "confirmation",  "min_bars": 15, "tolerance": 1.5, "swing_window": 5},
    "2h": {"weight": 1.5, "role": "confirmation",  "min_bars": 15, "tolerance": 1.5, "swing_window": 5},
    "1h": {"weight": 1.0, "role": "entry",          "min_bars": 20, "tolerance": 1.2, "swing_window": 5},
}

# Orden top-down para análisis
TOP_DOWN_ORDER = ["1w", "1d", "4h", "2h", "1h"]


@dataclass
class TimeframeAnalysis:
    timeframe: str
    patterns: list[DetectedPattern]
    breakouts: list[BreakoutValidation]
    best_pattern: Optional[DetectedPattern]
    best_breakout: Optional[BreakoutValidation]
    direction: str
    weight: float
    role: str


@dataclass
class MTFAlignment:
    aligned: bool
    consensus_direction: str
    alignment_score: int
    dominant_tf: str
    entry_tf: str
    tf_analyses: dict[str, TimeframeAnalysis] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    best_pattern: Optional[DetectedPattern] = None
    best_breakout: Optional[BreakoutValidation] = None
    best_targets: Optional[PatternTargets] = None
    regime: Optional[MarketRegime] = None
    veto_reason: str = ""

    def prompt_section(self) -> str:
        lines = []

        if self.regime:
            lines.append("=== RÉGIMEN DE MERCADO ===")
            lines.append(self.regime.prompt_section())
            lines.append("")

        lines.append("=== CHART PATTERNS DETECTADOS ===")
        found_any = False
        for tf_name in TOP_DOWN_ORDER:
            analysis = self.tf_analyses.get(tf_name)
            if analysis and analysis.patterns:
                found_any = True
                for p in analysis.patterns[:2]:
                    lines.append(f"  [{tf_name.upper()}] {p.prompt_line()}")

        if not found_any:
            lines.append("  No se detectaron patrones chartistas claros.")
        lines.append("")

        emoji = "✅" if self.aligned else "⚠️"
        lines.append(f"=== ALINEACIÓN MULTI-TIMEFRAME {emoji} ===")
        lines.append(f"Consenso: {self.consensus_direction.upper()} "
                     f"(TF dominante: {self.dominant_tf})")
        lines.append(f"Score de alineación: {self.alignment_score:+d} puntos")

        if self.veto_reason:
            lines.append(f"⛔ VETO: {self.veto_reason}")
        elif self.conflicts:
            lines.append(f"Conflictos: {'; '.join(self.conflicts)}")
        elif self.aligned:
            lines.append("Todos los timeframes coinciden en dirección.")

        if self.best_breakout and self.best_pattern and self.best_pattern.breakout_occurred:
            lines.append("")
            lines.append("=== VALIDACIÓN DE BREAKOUT ===")
            lines.append(self.best_breakout.prompt_section())

        if self.best_targets and self.best_targets.is_valid_setup:
            lines.append("")
            lines.append("=== TARGETS POR GEOMETRÍA DEL PATRÓN ===")
            lines.append(self.best_targets.prompt_section())

        return "\n".join(lines)


class MTFAligner:

    def __init__(self):
        # Un detector por timeframe con parámetros ajustados
        self.detectors = {}
        for tf, cfg in TF_CONFIG.items():
            self.detectors[tf] = PatternDetector(
                min_pattern_bars=cfg["min_bars"],
                tolerance_pct=cfg["tolerance"],
                swing_window=cfg["swing_window"],
            )

        self.breakout_validator = BreakoutValidator()
        self.target_calculator = TargetCalculator()
        self.regime_detector = RegimeDetector()

    def _analyze_single_tf(self, df: pd.DataFrame, symbol: str, tf: str) -> TimeframeAnalysis:
        detector = self.detectors.get(tf, PatternDetector())
        config = TF_CONFIG.get(tf, {"weight": 1.0, "role": "entry"})

        patterns = detector.detect_all(df, symbol, tf)

        breakouts = []
        for p in patterns:
            if p.breakout_occurred:
                bv = self.breakout_validator.validate(df, p)
                breakouts.append(bv)
            else:
                breakouts.append(None)

        best_pattern = patterns[0] if patterns else None
        best_breakout = None
        for bv in breakouts:
            if bv and bv.is_valid:
                best_breakout = bv
                break

        direction = best_pattern.direction if best_pattern else "neutral"

        return TimeframeAnalysis(
            timeframe=tf,
            patterns=patterns,
            breakouts=[b for b in breakouts if b],
            best_pattern=best_pattern,
            best_breakout=best_breakout,
            direction=direction,
            weight=config["weight"],
            role=config["role"],
        )

    def analyze(self, candles_by_tf: dict, symbol: str) -> MTFAlignment:
        tf_analyses = {}

        # Análisis top-down: empezar por TFs mayores
        for tf in TOP_DOWN_ORDER:
            df = candles_by_tf.get(tf)
            if df is not None and len(df) >= 20:
                tf_analyses[tf] = self._analyze_single_tf(df, symbol, tf)

        if not tf_analyses:
            return MTFAlignment(
                aligned=False, consensus_direction="neutral",
                alignment_score=0, dominant_tf="none", entry_tf="none"
            )

        # Detectar régimen en el TF más grande disponible
        regime = None
        for tf in ["1d", "4h", "2h", "1h"]:
            df = candles_by_tf.get(tf)
            if df is not None and len(df) >= 50:
                regime = self.regime_detector.detect(df)
                if regime:
                    break

        # ── ENFOQUE TOP-DOWN ────────────────────────────────────────────
        # Paso 1: Determinar dirección del TF mayor con patrón
        macro_direction = "neutral"
        dominant_tf = "none"

        for tf in TOP_DOWN_ORDER:
            analysis = tf_analyses.get(tf)
            if analysis and analysis.direction != "neutral":
                macro_direction = analysis.direction
                dominant_tf = tf
                break  # El TF mayor con opinión manda

        # Paso 2: Verificar veto — si TF mayor tiene patrón claro
        # en dirección contraria a TFs menores, es veto
        veto_reason = ""
        if macro_direction != "neutral":
            for tf in TOP_DOWN_ORDER:
                analysis = tf_analyses.get(tf)
                if analysis and analysis.direction != "neutral":
                    if analysis.direction != macro_direction and analysis.weight < TF_CONFIG.get(dominant_tf, {}).get("weight", 0):
                        # TF menor contradice al mayor — verificar si el mayor tiene un patrón fuerte
                        dom_analysis = tf_analyses.get(dominant_tf)
                        if dom_analysis and dom_analysis.best_pattern and dom_analysis.best_pattern.confidence >= 60:
                            veto_reason = (
                                f"{dominant_tf.upper()} tiene {dom_analysis.best_pattern.pattern_type} "
                                f"{macro_direction} ({dom_analysis.best_pattern.confidence:.0f}% conf) — "
                                f"contradice {tf} {analysis.direction}"
                            )

        # Paso 3: Consenso ponderado
        bullish_weight = 0
        bearish_weight = 0
        for analysis in tf_analyses.values():
            if analysis.direction == "bullish":
                bullish_weight += analysis.weight
            elif analysis.direction == "bearish":
                bearish_weight += analysis.weight

        if bullish_weight + bearish_weight == 0:
            consensus = "neutral"
        elif bullish_weight > bearish_weight * 1.3:
            consensus = "bullish"
        elif bearish_weight > bullish_weight * 1.3:
            consensus = "bearish"
        else:
            consensus = "neutral"

        # Si hay veto, el consenso sigue la dirección del TF mayor
        if veto_reason and macro_direction != "neutral":
            consensus = macro_direction

        # Paso 4: Entry TF
        entry_tf = "1h"
        for tf in reversed(TOP_DOWN_ORDER):
            if tf in tf_analyses:
                entry_tf = tf
                break

        # Paso 5: Conflictos
        conflicts = []
        for tf, analysis in tf_analyses.items():
            if tf != dominant_tf and analysis.direction != "neutral":
                if analysis.direction != macro_direction and macro_direction != "neutral":
                    conflicts.append(
                        f"{tf} dice {analysis.direction} vs {dominant_tf} dice {macro_direction}"
                    )

        # Paso 6: Alignment score
        aligned = len(conflicts) == 0 and consensus != "neutral"

        if aligned and consensus != "neutral":
            alignment_score = 20
        elif len(conflicts) == 0:
            alignment_score = 5
        elif veto_reason:
            alignment_score = -25  # Veto fuerte
        elif len(conflicts) == 1:
            alignment_score = -10
        else:
            alignment_score = -20

        # ── MEJOR PATRÓN (priorizar TFs altos, pero validar targets) ─────
        best_pattern = None
        best_breakout = None

        # Primero buscar en TFs altos, luego en bajos
        for tf in TOP_DOWN_ORDER:
            analysis = tf_analyses.get(tf)
            if not analysis or not analysis.best_pattern:
                continue
            if analysis.best_pattern.direction == consensus or consensus == "neutral":
                if best_pattern is None or (
                    analysis.weight > TF_CONFIG.get(best_pattern.timeframe, {}).get("weight", 0) and
                    analysis.best_pattern.confidence >= 40
                ):
                    best_pattern = analysis.best_pattern
                    best_breakout = analysis.best_breakout

        # Calcular targets
        best_targets = None
        if best_pattern:
            tp_mult = regime.tp_multiplier if regime else 1.0
            sl_mult = regime.sl_multiplier if regime else 1.0
            best_targets = self.target_calculator.calculate(
                best_pattern, best_breakout, tp_mult, sl_mult
            )

            # v0.7.2: Si targets son inválidos, buscar patrón de TF más bajo
            if best_targets and not best_targets.is_valid_setup:
                logger.info(
                    f"{symbol} — Targets de {best_pattern.pattern_type}({best_pattern.timeframe}) "
                    f"inválidos ({best_targets.tp_method}). Buscando TF alternativo..."
                )
                for tf in reversed(TOP_DOWN_ORDER):
                    if tf == best_pattern.timeframe:
                        continue
                    analysis = tf_analyses.get(tf)
                    if not analysis or not analysis.best_pattern:
                        continue
                    if analysis.best_pattern.direction == consensus or consensus == "neutral":
                        alt_targets = self.target_calculator.calculate(
                            analysis.best_pattern,
                            analysis.best_breakout,
                            tp_mult, sl_mult
                        )
                        if alt_targets and alt_targets.is_valid_setup:
                            logger.info(
                                f"{symbol} — Usando targets de {analysis.best_pattern.pattern_type}"
                                f"({tf}) en vez de {best_pattern.timeframe}"
                            )
                            best_targets = alt_targets
                            # Mantener best_pattern del TF alto para scoring,
                            # pero usar targets del TF bajo
                            break

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
            veto_reason=veto_reason,
        )

        logger.info(
            f"{symbol} MTF: {consensus} | Aligned: {aligned} | "
            f"Dominant: {dominant_tf} | Score: {alignment_score:+d} | "
            f"Pattern: {best_pattern.pattern_type if best_pattern else 'none'} "
            f"({best_pattern.timeframe if best_pattern else '-'}) | "
            f"Regime: {regime.regime if regime else 'unknown'} | "
            f"Conflicts: {len(conflicts)}"
            + (f" | VETO: {veto_reason}" if veto_reason else "")
        )

        return result
