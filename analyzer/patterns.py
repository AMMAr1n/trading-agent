"""
patterns.py — Detector de formaciones chartistas (chart patterns)
v0.7.2 — 16 patrones + filtro SMA50 de confianza.
Basado en Charles University thesis (2025): SMA50 como filtro de tendencia
validado estadísticamente en crypto markets.

REVERSAL (8):
  - Double Top / Double Bottom
  - Head & Shoulders / Inverse H&S
  - Triple Top / Triple Bottom (NUEVO)
  - Cup & Handle (NUEVO)
  - Rising/Falling Wedge (como reversal según contexto)

CONTINUATION (8):
  - Ascending / Descending / Symmetrical Triangle
  - Rising/Falling Wedge (como continuation según contexto)
  - Rectangle
  - Bull Flag / Bear Flag (NUEVO)
  - Pennant (NUEVO)

Cada patrón retorna: tipo, dirección, breakout level, confianza,
puntos clave (S/R, neckline), y si el breakout ya ocurrió.
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
    pattern_type: str
    category: str               # "reversal" | "continuation"
    direction: str              # "bullish" | "bearish"
    confidence: float           # 0-100
    breakout_level: float
    invalidation_level: float
    target_price: float
    current_price: float
    timeframe: str
    key_levels: dict = field(default_factory=dict)
    breakout_occurred: bool = False
    description: str = ""

    @property
    def risk_reward(self) -> float:
        if self.direction == "bullish":
            risk = abs(self.current_price - self.invalidation_level)
            reward = abs(self.target_price - self.current_price)
        else:
            risk = abs(self.invalidation_level - self.current_price)
            reward = abs(self.current_price - self.target_price)
        return round(reward / risk, 2) if risk > 0 else 0

    def prompt_line(self) -> str:
        emoji = "🟢" if self.direction == "bullish" else "🔴"
        bo = "✅ BREAKOUT" if self.breakout_occurred else "⏳ pendiente"
        return (
            f"{emoji} {self.pattern_type.replace('_', ' ').title()} "
            f"({self.category}) | {self.direction} | "
            f"Confianza: {self.confidence:.0f}% | "
            f"Breakout: ${self.breakout_level:,.4f} [{bo}] | "
            f"Target: ${self.target_price:,.4f} | "
            f"Invalidación: ${self.invalidation_level:,.4f} | "
            f"R:R {self.risk_reward}:1"
        )


class PatternDetector:

    def __init__(self, min_pattern_bars: int = 15, tolerance_pct: float = 1.5,
                 swing_window: int = 5):
        self.min_bars = min_pattern_bars
        self.tolerance = tolerance_pct / 100
        self.swing_window = swing_window

    def _find_swing_points(self, series: pd.Series, window: int = None) -> tuple:
        w = window or self.swing_window
        highs = []
        lows = []
        values = series.values
        n = len(values)
        for i in range(w, n - w):
            if all(values[i] >= values[i - j] for j in range(1, w + 1)) and \
               all(values[i] >= values[i + j] for j in range(1, w + 1)):
                highs.append((i, float(values[i])))
            if all(values[i] <= values[i - j] for j in range(1, w + 1)) and \
               all(values[i] <= values[i + j] for j in range(1, w + 1)):
                lows.append((i, float(values[i])))
        return highs, lows

    def _prices_are_equal(self, p1: float, p2: float) -> bool:
        if p1 == 0:
            return False
        return abs(p1 - p2) / p1 <= self.tolerance

    def _linear_regression_slope(self, points: list) -> float:
        if len(points) < 2:
            return 0.0
        x = np.array([p[0] for p in points], dtype=float)
        y = np.array([p[1] for p in points], dtype=float)
        x_norm = x - x[0]
        n = len(x_norm)
        slope = (n * np.sum(x_norm * y) - np.sum(x_norm) * np.sum(y)) / \
                (n * np.sum(x_norm ** 2) - np.sum(x_norm) ** 2 + 1e-10)
        avg_price = np.mean(y)
        return float(slope / avg_price * 100) if avg_price > 0 else 0.0

    def _apply_sma50_filter(self, pattern, df):
        """
        v0.7.2: Ajusta confianza del patrón según SMA50.
        Patrón bullish + precio > SMA50 → +5% confianza (alineado con tendencia)
        Patrón bullish + precio < SMA50 → -15% confianza (contra tendencia)
        Patrón bearish + precio < SMA50 → +5% confianza
        Patrón bearish + precio > SMA50 → -15% confianza
        """
        if pattern is None or len(df) < 50:
            return pattern
        sma50 = df["close"].rolling(window=50).mean().iloc[-1]
        if np.isnan(sma50):
            return pattern
        price = pattern.current_price
        if pattern.direction == "bullish":
            if price > sma50:
                pattern.confidence = min(pattern.confidence + 5, 98)
            else:
                pattern.confidence = max(pattern.confidence - 15, 10)
        elif pattern.direction == "bearish":
            if price < sma50:
                pattern.confidence = min(pattern.confidence + 5, 98)
            else:
                pattern.confidence = max(pattern.confidence - 15, 10)
        return pattern

    # ─── REVERSAL PATTERNS ─────────────────────────────────────────────

    def detect_double_top(self, df, symbol, tf):
        swing_highs, swing_lows = self._find_swing_points(df["high"])
        current_price = float(df["close"].iloc[-1])
        if len(swing_highs) < 2 or len(swing_lows) < 1:
            return None
        for i in range(len(swing_highs) - 1, 0, -1):
            h2_idx, h2_price = swing_highs[i]
            h1_idx, h1_price = swing_highs[i - 1]
            if h2_idx - h1_idx < self.min_bars:
                continue
            if not self._prices_are_equal(h1_price, h2_price):
                continue
            valley_lows = [(idx, p) for idx, p in swing_lows if h1_idx < idx < h2_idx]
            if not valley_lows:
                continue
            neckline_price = min(valley_lows, key=lambda x: x[1])[1]
            resistance = (h1_price + h2_price) / 2
            pattern_height = resistance - neckline_price
            if pattern_height <= 0:
                continue
            target = neckline_price - pattern_height
            breakout_occurred = current_price < neckline_price
            symmetry = 1 - abs(h1_price - h2_price) / h1_price
            separation = min((h2_idx - h1_idx) / 20, 1.0)
            confidence = symmetry * 50 + separation * 30 + (20 if breakout_occurred else 0)
            return DetectedPattern(
                pattern_type="double_top", category="reversal", direction="bearish",
                confidence=min(confidence, 95), breakout_level=neckline_price,
                invalidation_level=resistance * 1.005, target_price=target,
                current_price=current_price, timeframe=tf,
                key_levels={"resistance": resistance, "neckline": neckline_price},
                breakout_occurred=breakout_occurred,
                description=f"Double top en {symbol} — resistencia ${resistance:,.4f}"
            )
        return None

    def detect_double_bottom(self, df, symbol, tf):
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
            neckline_price = max(peak_highs, key=lambda x: x[1])[1]
            support = (l1_price + l2_price) / 2
            pattern_height = neckline_price - support
            if pattern_height <= 0:
                continue
            target = neckline_price + pattern_height
            breakout_occurred = current_price > neckline_price
            symmetry = 1 - abs(l1_price - l2_price) / l1_price
            separation = min((l2_idx - l1_idx) / 20, 1.0)
            confidence = symmetry * 50 + separation * 30 + (20 if breakout_occurred else 0)
            return DetectedPattern(
                pattern_type="double_bottom", category="reversal", direction="bullish",
                confidence=min(confidence, 95), breakout_level=neckline_price,
                invalidation_level=support * 0.995, target_price=target,
                current_price=current_price, timeframe=tf,
                key_levels={"support": support, "neckline": neckline_price},
                breakout_occurred=breakout_occurred,
                description=f"Double bottom en {symbol} — soporte ${support:,.4f}"
            )
        return None

    def detect_triple_top(self, df, symbol, tf):
        """Triple Top: 3 toques a resistencia — 85% success rate."""
        swing_highs, swing_lows = self._find_swing_points(df["high"])
        current_price = float(df["close"].iloc[-1])
        if len(swing_highs) < 3 or len(swing_lows) < 2:
            return None
        for i in range(len(swing_highs) - 1, 1, -1):
            h3_idx, h3_price = swing_highs[i]
            h2_idx, h2_price = swing_highs[i - 1]
            h1_idx, h1_price = swing_highs[i - 2]
            if h3_idx - h1_idx < self.min_bars * 1.5:
                continue
            if not (self._prices_are_equal(h1_price, h2_price) and
                    self._prices_are_equal(h2_price, h3_price)):
                continue
            valleys = [(idx, p) for idx, p in swing_lows if h1_idx < idx < h3_idx]
            if len(valleys) < 2:
                continue
            neckline = min(v[1] for v in valleys)
            resistance = (h1_price + h2_price + h3_price) / 3
            pattern_height = resistance - neckline
            if pattern_height <= 0:
                continue
            target = neckline - pattern_height
            breakout_occurred = current_price < neckline
            confidence = 55 + (25 if breakout_occurred else 0) + min((h3_idx - h1_idx) / 30, 15)
            return DetectedPattern(
                pattern_type="triple_top", category="reversal", direction="bearish",
                confidence=min(confidence, 95), breakout_level=neckline,
                invalidation_level=resistance * 1.005, target_price=target,
                current_price=current_price, timeframe=tf,
                key_levels={"resistance": resistance, "neckline": neckline},
                breakout_occurred=breakout_occurred,
                description=f"Triple top en {symbol} — 3 rechazos en ${resistance:,.4f}"
            )
        return None

    def detect_triple_bottom(self, df, symbol, tf):
        """Triple Bottom: 3 toques a soporte — 85% success rate."""
        swing_highs, swing_lows = self._find_swing_points(df["low"])
        current_price = float(df["close"].iloc[-1])
        if len(swing_lows) < 3 or len(swing_highs) < 2:
            return None
        for i in range(len(swing_lows) - 1, 1, -1):
            l3_idx, l3_price = swing_lows[i]
            l2_idx, l2_price = swing_lows[i - 1]
            l1_idx, l1_price = swing_lows[i - 2]
            if l3_idx - l1_idx < self.min_bars * 1.5:
                continue
            if not (self._prices_are_equal(l1_price, l2_price) and
                    self._prices_are_equal(l2_price, l3_price)):
                continue
            peaks = [(idx, p) for idx, p in swing_highs if l1_idx < idx < l3_idx]
            if len(peaks) < 2:
                continue
            neckline = max(p[1] for p in peaks)
            support = (l1_price + l2_price + l3_price) / 3
            pattern_height = neckline - support
            if pattern_height <= 0:
                continue
            target = neckline + pattern_height
            breakout_occurred = current_price > neckline
            confidence = 55 + (25 if breakout_occurred else 0) + min((l3_idx - l1_idx) / 30, 15)
            return DetectedPattern(
                pattern_type="triple_bottom", category="reversal", direction="bullish",
                confidence=min(confidence, 95), breakout_level=neckline,
                invalidation_level=support * 0.995, target_price=target,
                current_price=current_price, timeframe=tf,
                key_levels={"support": support, "neckline": neckline},
                breakout_occurred=breakout_occurred,
                description=f"Triple bottom en {symbol} — 3 rebotes en ${support:,.4f}"
            )
        return None

    def detect_head_and_shoulders(self, df, symbol, tf):
        swing_highs, swing_lows = self._find_swing_points(df["high"])
        current_price = float(df["close"].iloc[-1])
        if len(swing_highs) < 3:
            return None
        for i in range(len(swing_highs) - 1, 1, -1):
            rs_idx, rs_price = swing_highs[i]
            head_idx, head_price = swing_highs[i-1]
            ls_idx, ls_price = swing_highs[i-2]
            if head_price <= ls_price or head_price <= rs_price:
                continue
            if not self._prices_are_equal(ls_price, rs_price):
                continue
            if head_idx - ls_idx < self.min_bars // 2 or rs_idx - head_idx < self.min_bars // 2:
                continue
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
                pattern_type="head_and_shoulders", category="reversal", direction="bearish",
                confidence=min(confidence, 95), breakout_level=neckline,
                invalidation_level=rs_price * 1.005, target_price=target,
                current_price=current_price, timeframe=tf,
                key_levels={"left_shoulder": ls_price, "head": head_price,
                            "right_shoulder": rs_price, "neckline": neckline},
                breakout_occurred=breakout_occurred,
                description=f"H&S en {symbol} — cabeza ${head_price:,.4f}"
            )
        return None

    def detect_inverse_head_and_shoulders(self, df, symbol, tf):
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
                pattern_type="inverse_head_and_shoulders", category="reversal", direction="bullish",
                confidence=min(confidence, 95), breakout_level=neckline,
                invalidation_level=rs_price * 0.995, target_price=target,
                current_price=current_price, timeframe=tf,
                key_levels={"left_shoulder": ls_price, "head": head_price,
                            "right_shoulder": rs_price, "neckline": neckline},
                breakout_occurred=breakout_occurred,
                description=f"Inv H&S en {symbol} — cabeza ${head_price:,.4f}"
            )
        return None

    def detect_cup_and_handle(self, df, symbol, tf):
        """
        Cup & Handle: U-shape redondeado + pullback < 50% de la profundidad.
        Bullish reversal — ~70% success rate.
        """
        current_price = float(df["close"].iloc[-1])
        closes = df["close"].values
        n = len(closes)
        if n < self.min_bars * 2:
            return None

        # Buscar el punto más bajo en el rango central (el fondo del cup)
        search_start = n // 4
        search_end = n - n // 4
        if search_end <= search_start:
            return None

        cup_bottom_idx = search_start + int(np.argmin(closes[search_start:search_end]))
        cup_bottom = float(closes[cup_bottom_idx])

        # Los bordes del cup (left lip y right lip) deben estar a nivel similar
        left_lip = float(np.max(closes[:cup_bottom_idx])) if cup_bottom_idx > 0 else cup_bottom
        right_region = closes[cup_bottom_idx:]
        if len(right_region) < 5:
            return None
        right_lip = float(np.max(right_region))

        if not self._prices_are_equal(left_lip, right_lip):
            return None

        cup_depth = right_lip - cup_bottom
        if cup_depth <= 0 or cup_depth / right_lip < 0.03:
            return None  # Cup demasiado plano

        # Verificar U-shape (no V-shape): el fondo debe ser redondeado
        mid_left = float(np.mean(closes[max(0, cup_bottom_idx - 5):cup_bottom_idx]))
        mid_right = float(np.mean(closes[cup_bottom_idx:min(n, cup_bottom_idx + 5)]))
        if abs(mid_left - mid_right) / cup_bottom > 0.05:
            return None  # V-shape, no U-shape

        # Handle: pullback desde right_lip, debe ser < 50% de cup depth
        handle_start = cup_bottom_idx + int(np.argmax(right_region))
        if handle_start >= n - 3:
            return None  # No hay handle

        handle_low = float(np.min(closes[handle_start:]))
        handle_depth = right_lip - handle_low

        if handle_depth > cup_depth * 0.5:
            return None  # Handle demasiado profundo

        breakout_level = right_lip
        target = breakout_level + cup_depth
        breakout_occurred = current_price > breakout_level

        confidence = 45 + (20 if breakout_occurred else 0)
        # Bonus por simetría del cup
        if self._prices_are_equal(left_lip, right_lip):
            confidence += 15
        # Bonus por handle suave
        if handle_depth < cup_depth * 0.3:
            confidence += 10

        return DetectedPattern(
            pattern_type="cup_and_handle", category="reversal", direction="bullish",
            confidence=min(confidence, 95), breakout_level=breakout_level,
            invalidation_level=handle_low * 0.995, target_price=target,
            current_price=current_price, timeframe=tf,
            key_levels={"cup_bottom": cup_bottom, "left_lip": left_lip,
                       "right_lip": right_lip, "handle_low": handle_low},
            breakout_occurred=breakout_occurred,
            description=f"Cup & handle en {symbol} — breakout ${breakout_level:,.4f}"
        )

    # ─── CONTINUATION PATTERNS ─────────────────────────────────────────

    def detect_bull_flag(self, df, symbol, tf):
        """
        Bull Flag: movimiento fuerte alcista (pole) + consolidación descendente (flag).
        Continuation bullish — ~75% success rate.
        """
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        n = len(closes)
        if n < self.min_bars:
            return None

        current_price = float(closes[-1])

        # Buscar el pole: movimiento fuerte alcista en las últimas N velas
        lookback = min(n - 5, 40)
        for pole_start in range(n - lookback, n - 10):
            pole_end = pole_start + min(8, n - pole_start - 5)
            pole_gain_pct = (closes[pole_end] - closes[pole_start]) / closes[pole_start] * 100

            if pole_gain_pct < 3.0:  # Mínimo 3% de ganancia en el pole
                continue

            # Flag: consolidación descendente después del pole
            flag_closes = closes[pole_end:]
            flag_highs = highs[pole_end:]
            flag_lows = lows[pole_end:]

            if len(flag_closes) < 4:
                continue

            # La flag debe tener pendiente descendente o lateral
            flag_slope = (flag_closes[-1] - flag_closes[0]) / flag_closes[0] * 100
            if flag_slope > 1.0:  # Si sube mucho, no es flag
                continue
            if flag_slope < -3.0:  # Si baja mucho, es corrección no flag
                continue

            # El rango de la flag debe ser estrecho vs el pole
            flag_range = float(np.max(flag_highs) - np.min(flag_lows))
            pole_range = float(highs[pole_end] - closes[pole_start])
            if pole_range <= 0:
                continue
            if flag_range / pole_range > 0.5:
                continue  # Flag demasiado ancha

            breakout_level = float(np.max(flag_highs))
            pole_height = float(closes[pole_end] - closes[pole_start])
            target = breakout_level + pole_height
            invalidation = float(np.min(flag_lows)) * 0.995
            breakout_occurred = current_price > breakout_level

            tightness = 1 - (flag_range / pole_range)
            confidence = 40 + tightness * 20 + (25 if breakout_occurred else 0)

            return DetectedPattern(
                pattern_type="bull_flag", category="continuation", direction="bullish",
                confidence=min(confidence, 95), breakout_level=breakout_level,
                invalidation_level=invalidation, target_price=target,
                current_price=current_price, timeframe=tf,
                key_levels={"pole_start": float(closes[pole_start]),
                           "pole_end": float(closes[pole_end]),
                           "flag_low": float(np.min(flag_lows))},
                breakout_occurred=breakout_occurred,
                description=f"Bull flag en {symbol} — pole {pole_gain_pct:.1f}%"
            )
        return None

    def detect_bear_flag(self, df, symbol, tf):
        """
        Bear Flag: movimiento fuerte bajista (pole) + consolidación ascendente (flag).
        Continuation bearish — ~75% success rate.
        """
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        n = len(closes)
        if n < self.min_bars:
            return None

        current_price = float(closes[-1])
        lookback = min(n - 5, 40)

        for pole_start in range(n - lookback, n - 10):
            pole_end = pole_start + min(8, n - pole_start - 5)
            pole_loss_pct = (closes[pole_start] - closes[pole_end]) / closes[pole_start] * 100

            if pole_loss_pct < 3.0:
                continue

            flag_closes = closes[pole_end:]
            flag_highs = highs[pole_end:]
            flag_lows = lows[pole_end:]

            if len(flag_closes) < 4:
                continue

            flag_slope = (flag_closes[-1] - flag_closes[0]) / flag_closes[0] * 100
            if flag_slope < -1.0:
                continue
            if flag_slope > 3.0:
                continue

            flag_range = float(np.max(flag_highs) - np.min(flag_lows))
            pole_range = float(closes[pole_start] - lows[pole_end])
            if pole_range <= 0:
                continue
            if flag_range / pole_range > 0.5:
                continue

            breakout_level = float(np.min(flag_lows))
            pole_height = float(closes[pole_start] - closes[pole_end])
            target = breakout_level - pole_height
            invalidation = float(np.max(flag_highs)) * 1.005
            breakout_occurred = current_price < breakout_level

            tightness = 1 - (flag_range / pole_range)
            confidence = 40 + tightness * 20 + (25 if breakout_occurred else 0)

            return DetectedPattern(
                pattern_type="bear_flag", category="continuation", direction="bearish",
                confidence=min(confidence, 95), breakout_level=breakout_level,
                invalidation_level=invalidation, target_price=target,
                current_price=current_price, timeframe=tf,
                key_levels={"pole_start": float(closes[pole_start]),
                           "pole_end": float(closes[pole_end]),
                           "flag_high": float(np.max(flag_highs))},
                breakout_occurred=breakout_occurred,
                description=f"Bear flag en {symbol} — pole {pole_loss_pct:.1f}%"
            )
        return None

    def detect_pennant(self, df, symbol, tf):
        """
        Pennant: movimiento fuerte + triángulo simétrico pequeño y compacto.
        Similar a flag pero con convergencia. ~56% success rate.
        """
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        n = len(closes)
        if n < 15:
            return None

        current_price = float(closes[-1])

        # Buscar pole fuerte (>4% en pocas velas)
        for pole_start in range(max(0, n - 35), n - 10):
            pole_end = pole_start + min(6, n - pole_start - 5)
            pole_move = (closes[pole_end] - closes[pole_start]) / closes[pole_start] * 100

            is_bullish = pole_move > 4.0
            is_bearish = pole_move < -4.0
            if not is_bullish and not is_bearish:
                continue

            # Pennant: consolidación convergente después del pole
            pennant_highs = highs[pole_end:]
            pennant_lows = lows[pole_end:]
            if len(pennant_highs) < 4:
                continue

            # Verificar convergencia: rango se estrecha
            first_range = float(pennant_highs[0] - pennant_lows[0])
            last_range = float(pennant_highs[-1] - pennant_lows[-1])
            if first_range <= 0 or last_range >= first_range:
                continue  # No converge

            direction = "bullish" if is_bullish else "bearish"
            pole_height = abs(float(closes[pole_end] - closes[pole_start]))

            if direction == "bullish":
                breakout_level = float(np.max(pennant_highs))
                target = breakout_level + pole_height
                invalidation = float(np.min(pennant_lows)) * 0.995
            else:
                breakout_level = float(np.min(pennant_lows))
                target = breakout_level - pole_height
                invalidation = float(np.max(pennant_highs)) * 1.005

            breakout_occurred = (current_price > breakout_level if direction == "bullish"
                                else current_price < breakout_level)

            convergence = 1 - (last_range / first_range)
            confidence = 35 + convergence * 20 + (20 if breakout_occurred else 0)

            return DetectedPattern(
                pattern_type="pennant", category="continuation", direction=direction,
                confidence=min(confidence, 90),
                breakout_level=breakout_level,
                invalidation_level=invalidation, target_price=target,
                current_price=current_price, timeframe=tf,
                key_levels={"pole_height": pole_height, "convergence": round(convergence, 2)},
                breakout_occurred=breakout_occurred,
                description=f"Pennant {direction} en {symbol}"
            )
        return None

    def _detect_converging_pattern(self, df, symbol, tf):
        """Detecta triangles, wedges, rectangles por pendientes de swings."""
        swing_highs, _ = self._find_swing_points(df["high"])
        _, swing_lows_low = self._find_swing_points(df["low"])
        current_price = float(df["close"].iloc[-1])

        recent_highs = swing_highs[-4:] if len(swing_highs) >= 3 else swing_highs
        recent_lows = swing_lows_low[-4:] if len(swing_lows_low) >= 3 else swing_lows_low

        if len(recent_highs) < 2 or len(recent_lows) < 2:
            return None

        high_span = recent_highs[-1][0] - recent_highs[0][0]
        low_span = recent_lows[-1][0] - recent_lows[0][0]
        if high_span < self.min_bars or low_span < self.min_bars:
            return None

        slope_highs = self._linear_regression_slope(recent_highs)
        slope_lows = self._linear_regression_slope(recent_lows)

        lookback = min(len(df) - 1, 100)
        macro_price_start = float(df["close"].iloc[-lookback])
        macro_trend = "bullish" if current_price > macro_price_start else "bearish"

        top_level = float(np.mean([p for _, p in recent_highs]))
        bottom_level = float(np.mean([p for _, p in recent_lows]))
        pattern_height = top_level - bottom_level
        if pattern_height <= 0:
            return None

        flat_threshold = 0.03

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

        breakout_occurred = (current_price > breakout if direction == "bullish"
                            else current_price < breakout)

        touches_top = sum(1 for _, p in recent_highs if self._prices_are_equal(p, top_level))
        touches_bottom = sum(1 for _, p in recent_lows if self._prices_are_equal(p, bottom_level))
        touch_score = min((touches_top + touches_bottom) * 10, 40)
        convergence = min(abs(slope_highs - slope_lows) * 10, 30)
        confidence = touch_score + convergence + (25 if breakout_occurred else 0)

        return DetectedPattern(
            pattern_type=pattern_type, category=category, direction=direction,
            confidence=min(confidence, 95), breakout_level=breakout,
            invalidation_level=invalidation, target_price=target,
            current_price=current_price, timeframe=tf,
            key_levels={"top": top_level, "bottom": bottom_level,
                       "slope_highs": round(slope_highs, 4),
                       "slope_lows": round(slope_lows, 4)},
            breakout_occurred=breakout_occurred,
            description=f"{pattern_type.replace('_', ' ').title()} en {symbol}/{tf}"
        )

    # ─── MAIN DETECTION ────────────────────────────────────────────────

    def detect_all(self, df: pd.DataFrame, symbol: str, timeframe: str) -> list[DetectedPattern]:
        if len(df) < self.min_bars * 2:
            return []

        patterns = []

        detectors = [
            self.detect_double_top,
            self.detect_double_bottom,
            self.detect_triple_top,
            self.detect_triple_bottom,
            self.detect_head_and_shoulders,
            self.detect_inverse_head_and_shoulders,
            self.detect_cup_and_handle,
            self.detect_bull_flag,
            self.detect_bear_flag,
            self.detect_pennant,
        ]

        for detector in detectors:
            try:
                result = detector(df, symbol, timeframe)
                if result and result.confidence >= 40:
                    result = self._apply_sma50_filter(result, df)
                    if result.confidence >= 30:
                        patterns.append(result)
            except Exception as e:
                logger.debug(f"Error en {detector.__name__} para {symbol}/{timeframe}: {e}")

        # Converging patterns (triangles, wedges, rectangles)
        try:
            result = self._detect_converging_pattern(df, symbol, timeframe)
            if result and result.confidence >= 40:
                result = self._apply_sma50_filter(result, df)
                if result.confidence >= 30:
                    patterns.append(result)
        except Exception as e:
            logger.debug(f"Error en converging pattern para {symbol}/{timeframe}: {e}")

        patterns.sort(key=lambda p: p.confidence, reverse=True)

        if patterns:
            logger.info(
                f"{symbol}/{timeframe} — {len(patterns)} patrón(es): "
                + ", ".join(f"{p.pattern_type}({p.confidence:.0f}%)" for p in patterns)
            )

        return patterns
