"""
scorer.py — Calculador del score de confianza
v0.7.2 — Mejoras basadas en investigación (Charles University thesis):
  - Punto 1: Prioriza 4 patrones de velas validados estadísticamente en crypto
    (Hammer, Rising Window, On Neck, Shooting Star) con peso máximo.
    Los demás conservan peso reducido.
  - Punto 2: Ignora patrones gap-dependent que no ocurren en crypto 24/7
    (Kicking, Abandoned Baby, Tri Star).

Ponderación:
- EMAs: 20pts | Volumen: 15pts | MACD: 10pts | RSI: 10pts | BB: 5pts
- Chart Patterns: 25pts | Breakout: 15pts = 100 total
"""

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .indicators import TechnicalIndicators
from .levels import SupportResistanceResult

load_dotenv(override=False)
MIN_SCORE = int(os.getenv("MIN_SCORE", "30"))

logger = logging.getLogger(__name__)

WEIGHTS = {
    "ema_trend":       20,
    "volume":          15,
    "macd":            10,
    "rsi":             10,
    "bollinger":        5,
    "chart_pattern":   25,
    "breakout":        15,
}
MAX_SCORE = sum(WEIGHTS.values())

# ─── PATRONES DE VELAS VALIDADOS EN CRYPTO (Charles University, 2025) ────────
# Estos 4 patrones demostraron significancia estadística en múltiples datasets
# de criptomonedas. Reciben peso máximo (12pts confirmando, -10pts contradiciendo).
VALIDATED_CANDLESTICK_PATTERNS = {
    "hammer", "rising window", "on neck", "shooting star"
}

# Patrones gap-dependent que NO ocurren en crypto 24/7.
# El mercado opera sin pausa, no genera gaps entre sesiones.
# Buscarlos es ruido. Se ignoran completamente.
GAP_DEPENDENT_PATTERNS = {
    "kicking", "abandoned baby", "tri star",
    "upside tasuki gap", "downside tasuki gap"
}


@dataclass
class ScoreBreakdown:
    total: float
    direction: str

    ema_trend_points: float
    volume_points: float
    macd_points: float
    rsi_points: float
    bollinger_points: float
    chart_pattern_points: float
    breakout_points: float

    trend: str
    risk_pct: float
    suggested_sl: float
    suggested_tp: float
    reasoning: str

    @property
    def is_tradeable(self) -> bool:
        return self.total >= MIN_SCORE and self.direction != "neutral"

    @property
    def leverage_recommended(self) -> str:
        if self.total >= 75:
            return "3x"
        elif self.total >= 60:
            return "2x"
        return "1x"


class SignalScorer:

    def score_ema_trend(self, indicators, direction):
        price = indicators.current_price
        ema20 = indicators.ema_20
        ema50 = indicators.ema_50
        ema200 = indicators.ema_200

        if direction == "long":
            if price > ema20 > ema50 > ema200:
                return 20.0
            elif price > ema20 and price > ema50 and price > ema200:
                return 15.0
            elif price > ema20 and price > ema50:
                return 10.0
            elif price > ema20:
                return 5.0
            else:
                return 0.0
        elif direction == "short":
            if price < ema20 < ema50 < ema200:
                return 20.0
            elif price < ema20 and price < ema50 and price < ema200:
                return 15.0
            elif price < ema20 and price < ema50:
                return 10.0
            elif price < ema20:
                return 5.0
            else:
                return 0.0
        return 0.0

    def score_volume(self, indicators):
        ratio = indicators.volume.ratio
        if ratio >= 3.0:
            return 15.0
        elif ratio >= 2.0:
            return 12.0
        elif ratio >= 1.5:
            return 10.0
        elif ratio >= 1.2:
            return 8.0
        elif ratio >= 0.8:
            return 5.0
        elif ratio >= 0.5:
            return 3.0
        else:
            return 1.0

    def score_macd(self, indicators, direction):
        macd = indicators.macd
        if direction == "long":
            if macd.is_bullish_cross:
                return 10.0
            elif macd.is_bullish and macd.histogram > 0:
                return 7.0
            elif macd.is_bullish:
                return 4.0
            else:
                return 0.0
        elif direction == "short":
            if macd.is_bearish_cross:
                return 10.0
            elif macd.is_bearish and macd.histogram < 0:
                return 7.0
            elif macd.is_bearish:
                return 4.0
            else:
                return 0.0
        return 0.0

    def score_rsi(self, indicators, direction):
        rsi = indicators.rsi.value
        if direction == "long":
            if rsi < 25: return 10.0
            elif rsi < 35: return 8.0
            elif rsi < 45: return 3.0
            elif rsi < 55: return 5.0
            elif rsi < 65: return 8.0
            elif rsi < 70: return 5.0
            elif rsi < 75: return 2.0
            else: return 0.0
        elif direction == "short":
            if rsi > 75: return 10.0
            elif rsi > 65: return 8.0
            elif rsi > 55: return 3.0
            elif rsi > 45: return 5.0
            elif rsi > 35: return 8.0
            elif rsi > 30: return 5.0
            elif rsi > 25: return 2.0
            else: return 0.0
        return 0.0

    def score_bollinger(self, indicators, direction):
        bb = indicators.bollinger
        if direction == "long":
            if bb.is_squeeze: return 5.0
            elif bb.is_at_lower_band: return 4.0
            elif bb.percent_b < 0.3: return 3.0
            elif bb.percent_b < 0.5: return 2.0
            else: return 0.0
        elif direction == "short":
            if bb.is_squeeze: return 5.0
            elif bb.is_at_upper_band: return 4.0
            elif bb.percent_b > 0.7: return 3.0
            elif bb.percent_b > 0.5: return 2.0
            else: return 0.0
        return 0.0

    def score_chart_pattern(self, mtf_alignment=None):
        if mtf_alignment is None:
            return 0.0
        score = 0.0
        if mtf_alignment.best_pattern:
            pattern_confidence = mtf_alignment.best_pattern.confidence
            score += min(pattern_confidence / 100 * 15, 15.0)
        score += max(min(mtf_alignment.alignment_score / 2, 10), -5)
        return max(0, min(score, 25.0))

    def score_breakout(self, mtf_alignment=None):
        if mtf_alignment is None or mtf_alignment.best_breakout is None:
            return 0.0
        bo = mtf_alignment.best_breakout
        if not bo.is_valid:
            return 0.0
        quality_map = {"strong": 15.0, "moderate": 10.0, "weak": 4.0, "failed": 0.0}
        return quality_map.get(bo.quality, 0.0)

    def score_candlestick_patterns(self, indicators, direction):
        """
        Patrones de velas (ta-lib) — hasta 15 puntos bonus.
        v0.7.2: Prioriza los 4 patrones validados en crypto.
        Ignora patrones gap-dependent.

        Tier de prioridad:
        - VALIDADOS (Hammer, Rising Window, On Neck, Shooting Star):
          12pts confirmando, -10pts contradiciendo
        - FUERTES (Engulfing, Morning/Evening Star, Three White/Black, Marubozu):
          8pts confirmando, -6pts contradiciendo
        - ESTÁNDAR (todos los demás no gap-dependent):
          4pts confirmando, -3pts contradiciendo
        """
        patterns = getattr(indicators, 'candlestick_patterns', None) or []
        if not patterns:
            return 0.0

        STRONG_KEYWORDS = {
            "engulfing", "morning star", "evening star",
            "three white soldiers", "three black crows",
            "marubozu"
        }

        score = 0.0
        for p in patterns:
            p_lower = p.lower()

            # Punto 2: Ignorar patrones gap-dependent
            if any(gap in p_lower for gap in GAP_DEPENDENT_PATTERNS):
                continue

            # Determinar sesgo
            if "bullish" in p_lower:
                bias = "bullish"
            elif "bearish" in p_lower:
                bias = "bearish"
            else:
                continue

            # Punto 1: Priorizar patrones validados
            is_validated = any(vp in p_lower for vp in VALIDATED_CANDLESTICK_PATTERNS)
            is_strong = any(kw in p_lower for kw in STRONG_KEYWORDS)

            if is_validated:
                pts_confirm = 12.0
                pts_contra = -10.0
            elif is_strong:
                pts_confirm = 8.0
                pts_contra = -6.0
            else:
                pts_confirm = 4.0
                pts_contra = -3.0

            if direction == "long":
                score += pts_confirm if bias == "bullish" else pts_contra
            elif direction == "short":
                score += pts_confirm if bias == "bearish" else pts_contra

        return max(min(score, 15.0), -10.0)

    def calculate(self, indicators, levels, context_bonus=0.0, mtf_alignment=None):
        direction = indicators.suggested_direction

        if direction == "neutral":
            return ScoreBreakdown(
                total=0.0, direction="neutral",
                ema_trend_points=0.0, volume_points=0.0,
                macd_points=0.0, rsi_points=0.0, bollinger_points=0.0,
                chart_pattern_points=0.0, breakout_points=0.0,
                trend=indicators.trend, risk_pct=0.0,
                suggested_sl=0.0, suggested_tp=0.0,
                reasoning="Señales contradictorias — sin dirección clara"
            )

        ema_pts = self.score_ema_trend(indicators, direction)
        vol_pts = self.score_volume(indicators)
        macd_pts = self.score_macd(indicators, direction)
        rsi_pts = self.score_rsi(indicators, direction)
        bb_pts = self.score_bollinger(indicators, direction)
        chart_pts = self.score_chart_pattern(mtf_alignment)
        bo_pts = self.score_breakout(mtf_alignment)
        candle_pts = self.score_candlestick_patterns(indicators, direction)

        base_score = ema_pts + vol_pts + macd_pts + rsi_pts + bb_pts + chart_pts + bo_pts + candle_pts
        context_bonus = min(context_bonus, 10.0)
        total_score = max(0, min(base_score + context_bonus, 100.0))

        if mtf_alignment and mtf_alignment.best_targets and mtf_alignment.best_targets.is_valid_setup:
            sl = mtf_alignment.best_targets.stop_loss
            tp = mtf_alignment.best_targets.take_profit
            if direction == "long":
                risk_pct = abs(indicators.current_price - sl) / indicators.current_price * 100
            else:
                risk_pct = abs(sl - indicators.current_price) / indicators.current_price * 100
        else:
            if direction == "long":
                sl = levels.dynamic_stop_loss_long
                risk_pct = levels.risk_pct_long
                tp = indicators.current_price + (indicators.current_price - sl) * 2
            else:
                sl = levels.dynamic_stop_loss_short
                risk_pct = levels.risk_pct_short
                tp = indicators.current_price - (sl - indicators.current_price) * 2

        reasoning = self._build_reasoning(
            direction, indicators,
            ema_pts, vol_pts, macd_pts, rsi_pts, bb_pts,
            chart_pts, bo_pts, total_score, risk_pct, mtf_alignment
        )

        score = ScoreBreakdown(
            total=round(total_score, 1), direction=direction,
            ema_trend_points=ema_pts, volume_points=vol_pts,
            macd_points=macd_pts, rsi_points=rsi_pts,
            bollinger_points=bb_pts, chart_pattern_points=chart_pts,
            breakout_points=bo_pts, trend=indicators.trend,
            risk_pct=round(risk_pct, 2), suggested_sl=sl,
            suggested_tp=round(tp, 2), reasoning=reasoning
        )

        patterns_str = ",".join(getattr(indicators, 'candlestick_patterns', []) or [])
        pattern_name = mtf_alignment.best_pattern.pattern_type if (mtf_alignment and mtf_alignment.best_pattern) else "none"
        logger.info(
            f"{indicators.symbol}/{indicators.timeframe} — "
            f"Score: {total_score:.0f}/100 | Dir: {direction} | "
            f"EMA:{ema_pts:.0f} Vol:{vol_pts:.0f} MACD:{macd_pts:.0f} "
            f"RSI:{rsi_pts:.0f} BB:{bb_pts:.0f} "
            f"Pattern:{chart_pts:.0f} BO:{bo_pts:.0f} Candle:{candle_pts:.0f} | "
            f"ChartPattern: {pattern_name} | "
            f"Tradeable: {score.is_tradeable}"
        )

        return score

    def _build_reasoning(self, direction, indicators,
                         ema_pts, vol_pts, macd_pts, rsi_pts, bb_pts,
                         chart_pts, bo_pts, total, risk_pct, mtf_alignment=None):
        parts = []
        direction_str = "LONG" if direction == "long" else "SHORT"
        parts.append(f"Dirección: {direction_str} | Tendencia: {indicators.trend}")

        if ema_pts >= 20:
            parts.append("EMAs: alineación perfecta")
        elif ema_pts >= 15:
            parts.append("EMAs: precio sobre todas las medias")
        elif ema_pts >= 10:
            parts.append("EMAs: tendencia corto/medio plazo")
        elif ema_pts >= 5:
            parts.append("EMAs: solo corto plazo")
        else:
            parts.append("EMAs: contra tendencia")

        ratio = indicators.volume.ratio
        if vol_pts >= 12:
            parts.append(f"Volumen: {ratio:.1f}x — confirma fuertemente")
        elif vol_pts >= 8:
            parts.append(f"Volumen: {ratio:.1f}x — buena confirmación")
        elif vol_pts >= 5:
            parts.append(f"Volumen: {ratio:.1f}x — normal")
        else:
            parts.append(f"Volumen: {ratio:.1f}x — bajo")

        if chart_pts > 0 and mtf_alignment and mtf_alignment.best_pattern:
            bp = mtf_alignment.best_pattern
            parts.append(
                f"Chart pattern: {bp.pattern_type.replace('_',' ').title()} "
                f"({bp.confidence:.0f}% conf) — {bp.direction}"
            )
        else:
            parts.append("Chart pattern: ninguno detectado")

        if bo_pts > 0 and mtf_alignment and mtf_alignment.best_breakout:
            bo = mtf_alignment.best_breakout
            parts.append(f"Breakout: {bo.quality} (vol {bo.volume_ratio_at_breakout:.1f}x)")
        elif mtf_alignment and mtf_alignment.best_pattern:
            parts.append("Breakout: pendiente")

        parts.append(f"RSI: {indicators.rsi.value:.1f} ({indicators.rsi.signal})")
        parts.append(f"MACD: {indicators.macd.signal}")
        parts.append(f"Riesgo: {risk_pct:.1f}% | Score: {total:.0f}/100")

        return " | ".join(parts)
