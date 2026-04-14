"""
regime.py — Detector de régimen de mercado
Clasifica el mercado actual en: trending, ranging, o volatile.
Esto permite al agente adaptar su estrategia según las condiciones.

Un trader profesional no usa la misma estrategia en todos los mercados:
- Trending → breakouts, follow the trend, TP más ambicioso
- Ranging → reversiones en S/R, TP conservador, evitar breakouts falsos
- Volatile → reducir tamaño, ampliar SL, o no operar

v0.7.0
"""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class MarketRegime:
    """Resultado de la clasificación de régimen."""
    regime: str              # "trending" | "ranging" | "volatile"
    confidence: float        # 0-1, qué tan claro es el régimen
    adx: float               # Average Directional Index (fuerza de tendencia)
    atr_pct: float           # ATR como % del precio (volatilidad)
    bb_width_pct: float      # Ancho de Bollinger como % del precio
    trend_direction: str     # "bullish" | "bearish" | "neutral"

    # Recomendaciones para el agente
    favor_breakouts: bool
    favor_reversions: bool
    sizing_multiplier: float     # 1.0 normal, 0.5-0.7 en volatile
    tp_multiplier: float         # 1.0 normal, 1.3 en trending, 0.8 en ranging
    sl_multiplier: float         # 1.0 normal, 1.3 en volatile
    min_score_adjustment: int    # +5 en volatile (más selectivo), -5 en trending

    def prompt_section(self) -> str:
        """Genera la sección de régimen para el prompt de Claude."""
        regime_emoji = {
            "trending": "📈" if self.trend_direction == "bullish" else "📉",
            "ranging": "↔️",
            "volatile": "⚡"
        }
        emoji = regime_emoji.get(self.regime, "")

        lines = [
            f"Régimen: {self.regime.upper()} {emoji} (confianza: {self.confidence:.0%})",
            f"ADX: {self.adx:.1f} | ATR: {self.atr_pct:.2f}% | BB width: {self.bb_width_pct:.2f}%",
            f"Dirección tendencia: {self.trend_direction}",
            "",
            "Ajustes recomendados:"
        ]

        if self.regime == "trending":
            lines.append(f"  - Favorecer breakouts en dirección de la tendencia ({self.trend_direction})")
            lines.append(f"  - TP puede ser más ambicioso (multiplicador: {self.tp_multiplier:.1f}x)")
            lines.append("  - Evitar operar contra-tendencia salvo reversión muy clara")
        elif self.regime == "ranging":
            lines.append("  - Favorecer reversiones en soportes/resistencias")
            lines.append(f"  - TP conservador (multiplicador: {self.tp_multiplier:.1f}x)")
            lines.append("  - Breakouts tienen alta probabilidad de ser falsos")
        elif self.regime == "volatile":
            lines.append(f"  - Reducir tamaño de posición (multiplicador: {self.sizing_multiplier:.1f}x)")
            lines.append(f"  - Ampliar SL para no ser sacado por ruido (multiplicador: {self.sl_multiplier:.1f}x)")
            lines.append(f"  - Ser más selectivo (MIN_SCORE ajustado: {self.min_score_adjustment:+d})")

        return "\n".join(lines)


class RegimeDetector:
    """
    Detecta el régimen de mercado usando 3 métricas:

    1. ADX (Average Directional Index) — mide fuerza de tendencia
       > 25 = tendencia fuerte, < 20 = sin tendencia clara
    2. ATR como % del precio — mide volatilidad absoluta
    3. Ancho de Bollinger Bands — mide volatilidad estadística

    La combinación de las 3 clasifica el mercado.
    """

    def __init__(self, adx_period: int = 14, atr_period: int = 14, bb_period: int = 20):
        self.adx_period = adx_period
        self.atr_period = atr_period
        self.bb_period = bb_period

    def _calculate_adx(self, df: pd.DataFrame) -> float:
        """Calcula el ADX (Average Directional Index)."""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        n = self.adx_period

        # True Range
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # +DM y -DM
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        # Smoothed averages (Wilder's smoothing)
        atr = pd.Series(tr).ewm(alpha=1/n, min_periods=n).mean()
        plus_di = 100 * pd.Series(plus_dm).ewm(alpha=1/n, min_periods=n).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).ewm(alpha=1/n, min_periods=n).mean() / atr

        # DX y ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        adx = dx.ewm(alpha=1/n, min_periods=n).mean()

        return float(adx.iloc[-1]) if not adx.empty and not np.isnan(adx.iloc[-1]) else 20.0

    def _calculate_atr_pct(self, df: pd.DataFrame) -> float:
        """Calcula ATR como porcentaje del precio actual."""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.rolling(window=self.atr_period).mean().iloc[-1]
        current_price = close.iloc[-1]

        return float(atr / current_price * 100) if current_price > 0 else 0.0

    def _calculate_bb_width(self, df: pd.DataFrame) -> float:
        """Calcula el ancho de Bollinger Bands como % del precio."""
        close = df["close"]
        sma = close.rolling(window=self.bb_period).mean()
        std = close.rolling(window=self.bb_period).std()

        upper = sma + 2 * std
        lower = sma - 2 * std
        width = (upper - lower) / sma * 100

        return float(width.iloc[-1]) if not width.empty and not np.isnan(width.iloc[-1]) else 2.0

    def _get_trend_direction(self, df: pd.DataFrame) -> str:
        """Determina dirección de tendencia usando EMAs."""
        close = df["close"]
        ema20 = close.ewm(span=20).mean().iloc[-1]
        ema50 = close.ewm(span=50).mean().iloc[-1]
        current = close.iloc[-1]

        if current > ema20 > ema50:
            return "bullish"
        elif current < ema20 < ema50:
            return "bearish"
        return "neutral"

    def detect(self, df: pd.DataFrame) -> Optional[MarketRegime]:
        """
        Clasifica el régimen de mercado actual.

        Args:
            df: DataFrame con columnas open, high, low, close, volume.
                Mínimo 50 filas.

        Returns:
            MarketRegime con la clasificación y recomendaciones.
        """
        if len(df) < 50:
            logger.warning(f"Pocas velas para régimen: {len(df)}, mínimo 50")
            return None

        try:
            adx = self._calculate_adx(df)
            atr_pct = self._calculate_atr_pct(df)
            bb_width = self._calculate_bb_width(df)
            trend_dir = self._get_trend_direction(df)

            # Clasificación
            if adx > 25 and atr_pct < 4.0:
                # Tendencia clara sin volatilidad extrema
                regime = "trending"
                confidence = min((adx - 25) / 25, 1.0)
                favor_breakouts = True
                favor_reversions = False
                sizing_mult = 1.0
                tp_mult = 1.3
                sl_mult = 1.0
                min_score_adj = -3  # Menos selectivo en tendencia clara

            elif adx < 20 and bb_width < 3.0:
                # Sin tendencia, volatilidad baja — mercado lateral
                regime = "ranging"
                confidence = min((20 - adx) / 10, 1.0)
                favor_breakouts = False
                favor_reversions = True
                sizing_mult = 0.8
                tp_mult = 0.8
                sl_mult = 1.0
                min_score_adj = 3  # Más selectivo en ranging

            elif atr_pct > 3.5 or bb_width > 5.0:
                # Volatilidad alta — mercado peligroso
                regime = "volatile"
                confidence = min(atr_pct / 5.0, 1.0)
                favor_breakouts = False
                favor_reversions = False
                sizing_mult = 0.5
                tp_mult = 1.0
                sl_mult = 1.5
                min_score_adj = 8  # Mucho más selectivo

            else:
                # Transición / indefinido — tratar como ranging suave
                regime = "ranging"
                confidence = 0.4
                favor_breakouts = adx > 22
                favor_reversions = adx < 22
                sizing_mult = 0.9
                tp_mult = 1.0
                sl_mult = 1.1
                min_score_adj = 0

            result = MarketRegime(
                regime=regime,
                confidence=confidence,
                adx=adx,
                atr_pct=round(atr_pct, 3),
                bb_width_pct=round(bb_width, 3),
                trend_direction=trend_dir,
                favor_breakouts=favor_breakouts,
                favor_reversions=favor_reversions,
                sizing_multiplier=sizing_mult,
                tp_multiplier=tp_mult,
                sl_multiplier=sl_mult,
                min_score_adjustment=min_score_adj,
            )

            logger.info(
                f"Régimen: {regime} ({confidence:.0%}) | "
                f"ADX: {adx:.1f} | ATR%: {atr_pct:.2f} | "
                f"BB%: {bb_width:.2f} | Dir: {trend_dir}"
            )
            return result

        except Exception as e:
            logger.error(f"Error detectando régimen: {e}")
            return None
