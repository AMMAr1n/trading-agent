"""
scorer.py — Calculador del score de confianza
v0.7.0 — Añade scoring de chart patterns y breakout quality.

Ponderación actualizada:
- Tendencia/EMAs:    20 puntos (era 25)
- Volumen:           15 puntos (era 25)
- MACD:              10 puntos (era 20)
- RSI:               10 puntos (era 15)
- Bollinger:          5 puntos (era 15)
- Chart Patterns:    25 puntos (NUEVO — patrones estructurales multi-vela)
- Breakout Quality:  15 puntos (NUEVO — validación del breakout)
                    ─────────
                     100 puntos

Los chart patterns pesan más porque son señales de mayor probabilidad
que indicadores individuales. Un ascending triangle confirmado con
breakout de volumen tiene ~70% de probabilidad vs RSI sobrevendido ~55%.
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

# ─── PESOS v0.7.0 ─────────────────────────────────────────────────────────────
WEIGHTS = {
    "ema_trend":       20,   # Alineación de EMAs (era 25)
    "volume":          15,   # Confirmación de volumen (era 25)
    "macd":            10,   # Momentum y cruces (era 20)
    "rsi":             10,   # Condición del momentum (era 15)
    "bollinger":        5,   # Volatilidad y contexto (era 15)
    "chart_pattern":   25,   # Chart patterns multi-vela (NUEVO)
    "breakout":        15,   # Calidad del breakout (NUEVO)
}
MAX_SCORE = sum(WEIGHTS.values())  # 100


@dataclass
class ScoreBreakdown:
    total: float
    direction: str

    ema_trend_points: float
    volume_points: float
    macd_points: float
    rsi_points: float
    bollinger_points: float
    chart_pattern_points: float   # NUEVO
    breakout_points: float        # NUEVO

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

    def score_ema_trend(
        self,
        indicators: TechnicalIndicators,
        direction: str
    ) -> float:
        """Puntúa alineación de EMAs — hasta 20 puntos (era 25)."""
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

    def score_volume(self, indicators: TechnicalIndicators) -> float:
        """Puntúa volumen — hasta 15 puntos (era 25)."""
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

    def score_macd(self, indicators: TechnicalIndicators, direction: str) -> float:
        """Puntúa MACD — hasta 10 puntos (era 20)."""
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

    def score_rsi(self, indicators: TechnicalIndicators, direction: str) -> float:
        """Puntúa RSI — hasta 10 puntos (era 15). Mismo doble modo."""
        rsi = indicators.rsi.value
        trend = indicators.trend
        is_uptrend = "uptrend" in trend.lower() if trend else False

        if direction == "long":
            if rsi < 25:
                return 10.0
            elif rsi < 35:
                return 8.0
            elif rsi < 45:
                return 3.0
            elif rsi < 55:
                return 5.0
            elif rsi < 65:
                return 8.0
            elif rsi < 70:
                return 5.0
            elif rsi < 75:
                return 2.0
            else:
                return 0.0
        elif direction == "short":
            if rsi > 75:
                return 10.0
            elif rsi > 65:
                return 8.0
            elif rsi > 55:
                return 3.0
            elif rsi > 45:
                return 5.0
            elif rsi > 35:
                return 8.0
            elif rsi > 30:
                return 5.0
            elif rsi > 25:
                return 2.0
            else:
                return 0.0
        return 0.0

    def score_bollinger(self, indicators: TechnicalIndicators, direction: str) -> float:
        """Puntúa Bollinger — hasta 5 puntos (era 15)."""
        bb = indicators.bollinger
        if direction == "long":
            if bb.is_squeeze:
                return 5.0
            elif bb.is_at_lower_band:
                return 4.0
            elif bb.percent_b < 0.3:
                return 3.0
            elif bb.percent_b < 0.5:
                return 2.0
            else:
                return 0.0
        elif direction == "short":
            if bb.is_squeeze:
                return 5.0
            elif bb.is_at_upper_band:
                return 4.0
            elif bb.percent_b > 0.7:
                return 3.0
            elif bb.percent_b > 0.5:
                return 2.0
            else:
                return 0.0
        return 0.0

    def score_chart_pattern(self, mtf_alignment=None) -> float:
        """
        Puntúa chart patterns detectados — hasta 25 puntos (NUEVO).

        Basado en:
        - Mejor patrón detectado: confianza del patrón (0-100 → 0-15 pts)
        - Alineación multi-timeframe: +10 si alineados, -5 si conflicto
        """
        if mtf_alignment is None:
            return 0.0

        score = 0.0

        # Mejor patrón encontrado
        if mtf_alignment.best_pattern:
            pattern_confidence = mtf_alignment.best_pattern.confidence
            # Mapear 0-100 confianza → 0-15 puntos
            score += min(pattern_confidence / 100 * 15, 15.0)

        # Bonus/penalización por alineación MTF
        score += max(min(mtf_alignment.alignment_score / 2, 10), -5)

        return max(0, min(score, 25.0))

    def score_breakout(self, mtf_alignment=None) -> float:
        """
        Puntúa calidad del breakout — hasta 15 puntos (NUEVO).

        Solo puntúa si hay un breakout detectado.
        Strong = 15, Moderate = 10, Weak = 4, Failed/None = 0.
        """
        if mtf_alignment is None or mtf_alignment.best_breakout is None:
            return 0.0

        bo = mtf_alignment.best_breakout
        if not bo.is_valid:
            return 0.0

        quality_map = {
            "strong": 15.0,
            "moderate": 10.0,
            "weak": 4.0,
            "failed": 0.0,
        }
        return quality_map.get(bo.quality, 0.0)

    def score_candlestick_patterns(
        self,
        indicators: TechnicalIndicators,
        direction: str
    ) -> float:
        """Patrones de velas (ta-lib) — hasta 10 puntos bonus. Sin cambios."""
        patterns = getattr(indicators, 'candlestick_patterns', None) or []
        if not patterns:
            return 0.0

        STRONG_KEYWORDS = {
            "engulfing", "morning star", "evening star",
            "three white soldiers", "three black crows",
            "abandoned baby", "morning doji star", "evening doji star",
            "marubozu", "kicking"
        }

        score = 0.0
        for p in patterns:
            p_lower = p.lower()
            if "bullish" in p_lower:
                bias = "bullish"
            elif "bearish" in p_lower:
                bias = "bearish"
            else:
                continue

            is_strong = any(kw in p_lower for kw in STRONG_KEYWORDS)
            pts_confirm = 10.0 if is_strong else 5.0
            pts_contra = -8.0 if is_strong else -4.0

            if direction == "long":
                score += pts_confirm if bias == "bullish" else pts_contra
            elif direction == "short":
                score += pts_confirm if bias == "bearish" else pts_contra

        return max(min(score, 15.0), -10.0)

    def calculate(
        self,
        indicators: TechnicalIndicators,
        levels: SupportResistanceResult,
        context_bonus: float = 0.0,
        mtf_alignment=None,
    ) -> ScoreBreakdown:
        """
        Calcula el score de confianza completo.
        v0.7.0: añade chart_pattern y breakout al cálculo.
        """
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

        # SL/TP: preferir targets del patrón si disponibles
        if mtf_alignment and mtf_alignment.best_targets and mtf_alignment.best_targets.is_valid_setup:
            sl = mtf_alignment.best_targets.stop_loss
            tp = mtf_alignment.best_targets.take_profit
            if direction == "long":
                risk_pct = abs(indicators.current_price - sl) / indicators.current_price * 100
            else:
                risk_pct = abs(sl - indicators.current_price) / indicators.current_price * 100
        else:
            # Fallback a niveles de S/R como antes
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
            chart_pts, bo_pts,
            total_score, risk_pct, mtf_alignment
        )

        score = ScoreBreakdown(
            total=round(total_score, 1),
            direction=direction,
            ema_trend_points=ema_pts,
            volume_points=vol_pts,
            macd_points=macd_pts,
            rsi_points=rsi_pts,
            bollinger_points=bb_pts,
            chart_pattern_points=chart_pts,
            breakout_points=bo_pts,
            trend=indicators.trend,
            risk_pct=round(risk_pct, 2),
            suggested_sl=sl,
            suggested_tp=round(tp, 2),
            reasoning=reasoning
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

    def _build_reasoning(
        self,
        direction: str,
        indicators: TechnicalIndicators,
        ema_pts, vol_pts, macd_pts, rsi_pts, bb_pts,
        chart_pts, bo_pts,
        total, risk_pct, mtf_alignment=None
    ) -> str:
        parts = []
        direction_str = "LONG" if direction == "long" else "SHORT"
        parts.append(f"Dirección: {direction_str} | Tendencia: {indicators.trend}")

        # EMAs
        if ema_pts >= 20:
            parts.append(f"EMAs: alineación perfecta — estructura fuerte")
        elif ema_pts >= 15:
            parts.append(f"EMAs: precio sobre todas las medias")
        elif ema_pts >= 10:
            parts.append(f"EMAs: tendencia de corto/medio plazo")
        elif ema_pts >= 5:
            parts.append(f"EMAs: solo corto plazo")
        else:
            parts.append(f"EMAs: contra tendencia")

        # Volume
        ratio = indicators.volume.ratio
        if vol_pts >= 12:
            parts.append(f"Volumen: {ratio:.1f}x — confirma fuertemente")
        elif vol_pts >= 8:
            parts.append(f"Volumen: {ratio:.1f}x — buena confirmación")
        elif vol_pts >= 5:
            parts.append(f"Volumen: {ratio:.1f}x — normal")
        else:
            parts.append(f"Volumen: {ratio:.1f}x — bajo")

        # Chart pattern (NUEVO)
        if chart_pts > 0 and mtf_alignment and mtf_alignment.best_pattern:
            bp = mtf_alignment.best_pattern
            parts.append(
                f"Chart pattern: {bp.pattern_type.replace('_',' ').title()} "
                f"({bp.confidence:.0f}% conf) — {bp.direction}"
            )
        else:
            parts.append("Chart pattern: ninguno detectado")

        # Breakout (NUEVO)
        if bo_pts > 0 and mtf_alignment and mtf_alignment.best_breakout:
            bo = mtf_alignment.best_breakout
            parts.append(f"Breakout: {bo.quality} (vol {bo.volume_ratio_at_breakout:.1f}x)")
        elif mtf_alignment and mtf_alignment.best_pattern:
            parts.append("Breakout: pendiente — patrón aún no confirmado")

        # RSI, MACD, BB (compactos)
        parts.append(f"RSI: {indicators.rsi.value:.1f} ({indicators.rsi.signal})")
        parts.append(f"MACD: {indicators.macd.signal}")
        parts.append(f"Riesgo: {risk_pct:.1f}% | Score: {total:.0f}/100")

        return " | ".join(parts)
