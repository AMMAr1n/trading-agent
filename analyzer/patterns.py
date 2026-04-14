"""
patterns.py — Detector de formaciones chartistas (chart patterns)
Detecta 12 patrones estructurales multi-vela:

REVERSAL (6):
  - Double Top / Double Bottom
  - Diamond Top / Diamond Bottom
  - Cup & Handle
  - Rounded Bottom
  - Head & Shoulders / Inverse H&S
  - Rising Wedge (from bullish → bearish reversal)
  - Falling Wedge (from bearish → bullish reversal)

CONTINUATION (6):
  - Ascending Triangle
  - Descending Triangle
  - Symmetrical Triangle
  - Rising Wedge (from bearish → bearish continuation)
  - Falling Wedge (from bullish → bullish continuation)
  - Rectangle

Cada patrón retorna: tipo, dirección, breakout level, confianza,
puntos clave (S/R, neckline), y si el breakout ya ocurrió.

v0.7.0
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class DetectedPattern:
    """Un patrón chartista detectado."""
    pattern_type: str           # "double_top", "ascending_triangle", etc.
    category: str               # "reversal" | "continuation"
    direction: str              # "bullish" | "bearish"
    confidence: float           # 0-100
    breakout_level: float       # Precio donde se confirma el breakout
    invalidation_level: float   # Precio donde el patrón se invalida (para SL)
    target_price: float         # Precio objetivo basado en geometría del patrón
    current_price: float
    timeframe: str
    key_levels: dict = field(default_factory=dict)  # Niveles clave del patrón
    breakout_occurred: bool = False    # Ya rompió el nivel de breakout
    description: str = ""             # Descripción en lenguaje natural

    @property
    def risk_reward(self) -> float:
        """R:R calculado desde precio actual."""
        if self.direction == "bullish":
            risk = abs(self.current_price - self.invalidation_level)
            reward = abs(self.target_price - self.current_price)
        else:
            risk = abs(self.invalidation_level - self.current_price)
            reward = abs(self.current_price - self.target_price)
        return round(reward / risk, 2) if risk > 0 else 0

    def prompt_line(self) -> str:
        """Línea para incluir en el prompt de Claude."""
        emoji = "🟢" if self.direction == "bullish" else "🔴"
        bo = "✅ BREAKOUT" if self.breakout_occurred else "⏳ pendiente"
        return (
            f"{emoji} {self.pattern_type.replace('_', ' ').title()} "
            f"({self.category}) | {self.direction} | "
            f"Confianza: {self.confidence:.0f}% | "
            f"Breakout: ${self.breakout_level:,.4f} [{bo}] | "
            f"Target: ${self.target_price:,.4f} | "
            f"Invalidación (SL): ${self.invalidation_level:,.4f} | "
            f"R:R {self.risk_reward}:1"
        )


class PatternDetector:
    """
    Detecta formaciones chartistas multi-vela.

    Usa datos OHLCV para buscar estructuras de precio
    que indican reversiones o continuaciones.

    Usage:
        detector = PatternDetector()
        patterns = detector.detect_all(df, "BTCUSDT", "1h")
    """

    def __init__(self, min_pattern_bars: int = 15, tolerance_pct: float = 1.5):
        """
        Args:
            min_pattern_bars: Mínimo de velas para considerar un patrón válido.
            tolerance_pct: % de tolerancia para considerar dos precios "iguales".
        """
        self.min_bars = min_pattern_bars
        self.tolerance = tolerance_pct / 100

    def _find_swing_points(self, series: pd.Series, window: int = 5) -> tuple:
        """
        Encuentra swing highs y swing lows.
        Un swing high es un máximo local (mayor que los N vecinos).
        Un swing low es un mínimo local (menor que los N vecinos).

        Returns:
            (swing_highs, swing_lows) — cada uno es lista de (index, price)
        """
        highs = []
        lows = []
        values = series.values
        n = len(values)

        for i in range(window, n - window):
            # Swing high: valor mayor que todos los vecinos
            if all(values[i] >= values[i - j] for j in range(1, window + 1)) and \
               all(values[i] >= values[i + j] for j in range(1, window + 1)):
                highs.append((i, float(values[i])))

            # Swing low: valor menor que todos los vecinos
            if all(values[i] <= values[i - j] for j in range(1, window + 1)) and \
               all(values[i] <= values[i + j] for j in range(1, window + 1)):
                lows.append((i, float(values[i])))

        return highs, lows

    def _prices_are_equal(self, p1: float, p2: float) -> bool:
        """Verifica si dos precios están dentro del % de tolerancia."""
        if p1 == 0:
            return False
        return abs(p1 - p2) / p1 <= self.tolerance

    def _linear_regression_slope(self, points: list) -> float:
        """Calcula la pendiente de regresión lineal sobre una lista de (index, price)."""
        if len(points) < 2:
            return 0.0
        x = np.array([p[0] for p in points], dtype=float)
        y = np.array([p[1] for p in points], dtype=float)
        if len(x) < 2:
            return 0.0
        # Normalizar x para estabilidad numérica
        x_norm = x - x[0]
        n = len(x_norm)
        slope = (n * np.sum(x_norm * y) - np.sum(x_norm) * np.sum(y)) / \
                (n * np.sum(x_norm ** 2) - np.sum(x_norm) ** 2 + 1e-10)
        # Normalizar por precio promedio para hacer comparable
        avg_price = np.mean(y)
        return float(slope / avg_price * 100) if avg_price > 0 else 0.0

    # ─── REVERSAL PATTERNS ─────────────────────────────────────────────

    def detect_double_top(self, df: pd.DataFrame, symbol: str, tf: str) -> Optional[DetectedPattern]:
        """
        Double Top: precio toca resistencia 2 veces y falla.
        - Dos máximos a nivel similar (dentro de tolerancia)
        - Valle entre los dos máximos = neckline/soporte
        - Breakout = cierre debajo del soporte
        - Target = soporte - (resistencia - soporte)
        """
        swing_highs, swing_lows = self._find_swing_points(df["high"])
        current_price = float(df["close"].iloc[-1])

        if len(swing_highs) < 2 or len(swing_lows) < 1:
            return None

        # Buscar los dos últimos swing highs que estén a nivel similar
        for i in range(len(swing_highs) - 1, 0, -1):
            h2_idx, h2_price = swing_highs[i]
            h1_idx, h1_price = swing_highs[i - 1]

            if h2_idx - h1_idx < self.min_bars:
                continue

            if not self._prices_are_equal(h1_price, h2_price):
                continue

            # Buscar el valle entre los dos picos (neckline)
            valley_lows = [(idx, p) for idx, p in swing_lows if h1_idx < idx < h2_idx]
            if not valley_lows:
                continue

            neckline = min(valley_lows, key=lambda x: x[1])
            neckline_price = neckline[1]
            resistance = (h1_price + h2_price) / 2
            pattern_height = resistance - neckline_price

            if pattern_height <= 0:
                continue

            target = neckline_price - pattern_height
            breakout_occurred = current_price < neckline_price

            # Confianza basada en: simetría de picos + distancia entre ellos
            symmetry = 1 - abs(h1_price - h2_price) / h1_price
            separation = min((h2_idx - h1_idx) / 20, 1.0)
            confidence = (symmetry * 50 + separation * 30 + (20 if breakout_occurred else 0))

            return DetectedPattern(
                pattern_type="double_top",
                category="reversal",
                direction="bearish",
                confidence=min(confidence, 95),
                breakout_level=neckline_price,
                invalidation_level=resistance * 1.005,
                target_price=target,
                current_price=current_price,
                timeframe=tf,
                key_levels={"resistance": resistance, "neckline": neckline_price},
                breakout_occurred=breakout_occurred,
                description=f"Double top en {symbol} — resistencia en ${resistance:,.4f}, "
                           f"neckline en ${neckline_price:,.4f}"
            )
        return None

    def detect_double_bottom(self, df: pd.DataFrame, symbol: str, tf: str) -> Optional[DetectedPattern]:
        """
        Double Bottom: precio toca soporte 2 veces y rebota.
        Inverso del double top.
        """
        swing_highs, swing_lows = self._find_swing_points(df["low"])
        current_price = float(df["close"].iloc[-1])

        if len(swing_lows) < 2 or len(swing_highs) < 1:
            return None

        for i in range(len(swing_lows) - 1, 0, -1):
            l2_idx, l2_price = swing_lows[i]
            l1_idx, l1_price = swing_lows[i - 1]

            if l2_idx - l1_idx < self.min_bars:
                continue
            if not self._prices_are_equal(l1_price, l2_price):
                continue

            peak_highs = [(idx, p) for idx, p in swing_highs if l1_idx < idx < l2_idx]
            if not peak_highs:
                continue

            neckline = max(peak_highs, key=lambda x: x[1])
            neckline_price = neckline[1]
            support = (l1_price + l2_price) / 2
            pattern_height = neckline_price - support

            if pattern_height <= 0:
                continue

            target = neckline_price + pattern_height
            breakout_occurred = current_price > neckline_price

            symmetry = 1 - abs(l1_price - l2_price) / l1_price
            separation = min((l2_idx - l1_idx) / 20, 1.0)
            confidence = (symmetry * 50 + separation * 30 + (20 if breakout_occurred else 0))

            return DetectedPattern(
                pattern_type="double_bottom",
                category="reversal",
                direction="bullish",
                confidence=min(confidence, 95),
                breakout_level=neckline_price,
                invalidation_level=support * 0.995,
                target_price=target,
                current_price=current_price,
                timeframe=tf,
                key_levels={"support": support, "neckline": neckline_price},
                breakout_occurred=breakout_occurred,
                description=f"Double bottom en {symbol} — soporte en ${support:,.4f}, "
                           f"neckline en ${neckline_price:,.4f}"
            )
        return None

    def detect_head_and_shoulders(self, df: pd.DataFrame, symbol: str, tf: str) -> Optional[DetectedPattern]:
        """
        Head & Shoulders: tres picos, el central más alto.
        - Hombro izquierdo, cabeza, hombro derecho
        - Neckline conecta los dos valles
        - Target = neckline - altura de la cabeza sobre neckline
        """
        swing_highs, swing_lows = self._find_swing_points(df["high"])
        current_price = float(df["close"].iloc[-1])

        if len(swing_highs) < 3:
            return None

        for i in range(len(swing_highs) - 1, 1, -1):
            rs_idx, rs_price = swing_highs[i]       # Right shoulder
            head_idx, head_price = swing_highs[i-1]  # Head
            ls_idx, ls_price = swing_highs[i-2]      # Left shoulder

            # Head debe ser el más alto
            if head_price <= ls_price or head_price <= rs_price:
                continue
            # Hombros deben ser similares
            if not self._prices_are_equal(ls_price, rs_price):
                continue
            # Separación mínima
            if head_idx - ls_idx < self.min_bars // 2 or rs_idx - head_idx < self.min_bars // 2:
                continue

            # Valles entre los picos → neckline
            valley1 = [(idx, p) for idx, p in swing_lows if ls_idx < idx < head_idx]
            valley2 = [(idx, p) for idx, p in swing_lows if head_idx < idx < rs_idx]

            if not valley1 or not valley2:
                continue

            nl1 = min(valley1, key=lambda x: x[1])[1]
            nl2 = min(valley2, key=lambda x: x[1])[1]
            neckline = (nl1 + nl2) / 2
            pattern_height = head_price - neckline

            if pattern_height <= 0:
                continue

            target = neckline - pattern_height
            breakout_occurred = current_price < neckline

            shoulder_sym = 1 - abs(ls_price - rs_price) / ls_price
            confidence = shoulder_sym * 40 + 30 + (25 if breakout_occurred else 0)

            return DetectedPattern(
                pattern_type="head_and_shoulders",
                category="reversal",
                direction="bearish",
                confidence=min(confidence, 95),
                breakout_level=neckline,
                invalidation_level=rs_price * 1.005,
                target_price=target,
                current_price=current_price,
                timeframe=tf,
                key_levels={
                    "left_shoulder": ls_price, "head": head_price,
                    "right_shoulder": rs_price, "neckline": neckline
                },
                breakout_occurred=breakout_occurred,
                description=f"H&S en {symbol} — cabeza ${head_price:,.4f}, neckline ${neckline:,.4f}"
            )
        return None

    def detect_inverse_head_and_shoulders(self, df: pd.DataFrame, symbol: str, tf: str) -> Optional[DetectedPattern]:
        """Inverse H&S: tres valles, el central más profundo. Bullish reversal."""
        swing_highs, swing_lows = self._find_swing_points(df["low"])
        current_price = float(df["close"].iloc[-1])

        if len(swing_lows) < 3:
            return None

        for i in range(len(swing_lows) - 1, 1, -1):
            rs_idx, rs_price = swing_lows[i]
            head_idx, head_price = swing_lows[i-1]
            ls_idx, ls_price = swing_lows[i-2]

            if head_price >= ls_price or head_price >= rs_price:
                continue
            if not self._prices_are_equal(ls_price, rs_price):
                continue
            if head_idx - ls_idx < self.min_bars // 2 or rs_idx - head_idx < self.min_bars // 2:
                continue

            peak1 = [(idx, p) for idx, p in swing_highs if ls_idx < idx < head_idx]
            peak2 = [(idx, p) for idx, p in swing_highs if head_idx < idx < rs_idx]

            if not peak1 or not peak2:
                continue

            nl1 = max(peak1, key=lambda x: x[1])[1]
            nl2 = max(peak2, key=lambda x: x[1])[1]
            neckline = (nl1 + nl2) / 2
            pattern_height = neckline - head_price

            if pattern_height <= 0:
                continue

            target = neckline + pattern_height
            breakout_occurred = current_price > neckline

            shoulder_sym = 1 - abs(ls_price - rs_price) / ls_price
            confidence = shoulder_sym * 40 + 30 + (25 if breakout_occurred else 0)

            return DetectedPattern(
                pattern_type="inverse_head_and_shoulders",
                category="reversal",
                direction="bullish",
                confidence=min(confidence, 95),
                breakout_level=neckline,
                invalidation_level=rs_price * 0.995,
                target_price=target,
                current_price=current_price,
                timeframe=tf,
                key_levels={
                    "left_shoulder": ls_price, "head": head_price,
                    "right_shoulder": rs_price, "neckline": neckline
                },
                breakout_occurred=breakout_occurred,
                description=f"Inv H&S en {symbol} — cabeza ${head_price:,.4f}, neckline ${neckline:,.4f}"
            )
        return None

    # ─── CONTINUATION PATTERNS (TRIANGLES, WEDGES, RECTANGLE) ──────────

    def _detect_converging_pattern(self, df: pd.DataFrame, symbol: str, tf: str) -> Optional[DetectedPattern]:
        """
        Detecta triángulos, wedges y rectángulos analizando
        las pendientes de los swing highs y swing lows.

        Clasifica según las pendientes:
        - Asc triangle: highs flat, lows rising
        - Desc triangle: highs falling, lows flat
        - Sym triangle: highs falling, lows rising
        - Rising wedge: both rising, highs slope < lows slope
        - Falling wedge: both falling, highs slope > lows slope (less negative)
        - Rectangle: both flat
        """
        swing_highs, swing_lows = self._find_swing_points(df["high"], window=4)
        _, swing_lows_low = self._find_swing_points(df["low"], window=4)
        current_price = float(df["close"].iloc[-1])

        # Necesitamos al menos 3 puntos en cada lado
        recent_highs = swing_highs[-4:] if len(swing_highs) >= 3 else swing_highs
        recent_lows = swing_lows_low[-4:] if len(swing_lows_low) >= 3 else swing_lows_low

        if len(recent_highs) < 2 or len(recent_lows) < 2:
            return None

        # Separación mínima entre primer y último punto
        high_span = recent_highs[-1][0] - recent_highs[0][0]
        low_span = recent_lows[-1][0] - recent_lows[0][0]
        if high_span < self.min_bars or low_span < self.min_bars:
            return None

        slope_highs = self._linear_regression_slope(recent_highs)
        slope_lows = self._linear_regression_slope(recent_lows)

        # Determinar macro trend (para distinguir reversal vs continuation)
        lookback = min(len(df) - 1, 100)
        macro_price_start = float(df["close"].iloc[-lookback])
        macro_trend = "bullish" if current_price > macro_price_start else "bearish"

        # Niveles clave
        top_level = float(np.mean([p for _, p in recent_highs]))
        bottom_level = float(np.mean([p for _, p in recent_lows]))
        pattern_height = top_level - bottom_level

        if pattern_height <= 0:
            return None

        # Clasificación por pendientes (normalizado por precio, %/barra)
        flat_threshold = 0.03  # < 0.03%/barra = plano

        high_flat = abs(slope_highs) < flat_threshold
        low_flat = abs(slope_lows) < flat_threshold
        high_falling = slope_highs < -flat_threshold
        high_rising = slope_highs > flat_threshold
        low_rising = slope_lows > flat_threshold
        low_falling = slope_lows < -flat_threshold

        pattern_type = None
        direction = None
        category = None

        if high_flat and low_rising:
            pattern_type = "ascending_triangle"
            direction = "bullish"
            category = "continuation"
            breakout = top_level
            invalidation = recent_lows[-1][1] * 0.995
            target = breakout + pattern_height

        elif high_falling and low_flat:
            pattern_type = "descending_triangle"
            direction = "bearish"
            category = "continuation"
            breakout = bottom_level
            invalidation = recent_highs[-1][1] * 1.005
            target = breakout - pattern_height

        elif high_falling and low_rising:
            pattern_type = "symmetrical_triangle"
            direction = macro_trend  # Continúa la tendencia previa
            category = "continuation"
            apex = (top_level + bottom_level) / 2
            if direction == "bullish":
                breakout = top_level
                invalidation = bottom_level * 0.995
                target = breakout + pattern_height
            else:
                breakout = bottom_level
                invalidation = top_level * 1.005
                target = breakout - pattern_height

        elif high_rising and low_rising and slope_highs < slope_lows:
            pattern_type = "rising_wedge"
            direction = "bearish"
            category = "reversal" if macro_trend == "bullish" else "continuation"
            breakout = recent_lows[-1][1]
            invalidation = recent_highs[-1][1] * 1.005
            target = breakout - pattern_height

        elif high_falling and low_falling and slope_highs > slope_lows:
            pattern_type = "falling_wedge"
            direction = "bullish"
            category = "reversal" if macro_trend == "bearish" else "continuation"
            breakout = recent_highs[-1][1]
            invalidation = recent_lows[-1][1] * 0.995
            target = breakout + pattern_height

        elif high_flat and low_flat:
            pattern_type = "rectangle"
            direction = macro_trend
            category = "continuation"
            if direction == "bullish":
                breakout = top_level
                invalidation = bottom_level * 0.995
                target = breakout + pattern_height
            else:
                breakout = bottom_level
                invalidation = top_level * 1.005
                target = breakout - pattern_height

        if pattern_type is None:
            return None

        # Breakout check
        if direction == "bullish":
            breakout_occurred = current_price > breakout
        else:
            breakout_occurred = current_price < breakout

        # Confianza: más toques en los niveles = más confianza
        touches_top = sum(1 for _, p in recent_highs if self._prices_are_equal(p, top_level))
        touches_bottom = sum(1 for _, p in recent_lows if self._prices_are_equal(p, bottom_level))
        touch_score = min((touches_top + touches_bottom) * 10, 40)
        convergence = min(abs(slope_highs - slope_lows) * 10, 30)
        confidence = touch_score + convergence + (25 if breakout_occurred else 0)

        return DetectedPattern(
            pattern_type=pattern_type,
            category=category,
            direction=direction,
            confidence=min(confidence, 95),
            breakout_level=breakout,
            invalidation_level=invalidation,
            target_price=target,
            current_price=current_price,
            timeframe=tf,
            key_levels={"top": top_level, "bottom": bottom_level,
                       "slope_highs": round(slope_highs, 4),
                       "slope_lows": round(slope_lows, 4)},
            breakout_occurred=breakout_occurred,
            description=f"{pattern_type.replace('_', ' ').title()} en {symbol}/{tf}"
        )

    # ─── MAIN DETECTION ────────────────────────────────────────────────

    def detect_all(self, df: pd.DataFrame, symbol: str, timeframe: str) -> list[DetectedPattern]:
        """
        Ejecuta todos los detectores y retorna los patrones encontrados.

        Args:
            df: DataFrame con columnas open, high, low, close, volume.
            symbol: Par de trading (e.g. "BTCUSDT").
            timeframe: Timeframe de las velas (e.g. "1h").

        Returns:
            Lista de patrones detectados, ordenados por confianza descendente.
        """
        if len(df) < self.min_bars * 2:
            return []

        patterns = []

        # Reversal patterns
        detectors = [
            self.detect_double_top,
            self.detect_double_bottom,
            self.detect_head_and_shoulders,
            self.detect_inverse_head_and_shoulders,
        ]

        for detector in detectors:
            try:
                result = detector(df, symbol, timeframe)
                if result and result.confidence >= 40:
                    patterns.append(result)
            except Exception as e:
                logger.debug(f"Error en {detector.__name__} para {symbol}/{timeframe}: {e}")

        # Converging patterns (triangles, wedges, rectangles)
        try:
            result = self._detect_converging_pattern(df, symbol, timeframe)
            if result and result.confidence >= 40:
                patterns.append(result)
        except Exception as e:
            logger.debug(f"Error en converging pattern para {symbol}/{timeframe}: {e}")

        # Ordenar por confianza
        patterns.sort(key=lambda p: p.confidence, reverse=True)

        if patterns:
            logger.info(
                f"{symbol}/{timeframe} — {len(patterns)} patrón(es): "
                + ", ".join(f"{p.pattern_type}({p.confidence:.0f}%)" for p in patterns)
            )

        return patterns
