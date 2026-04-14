"""
breakout.py — Validador de breakouts
Responde la pregunta más importante: ¿el breakout es real o es una trampa?

Tres validaciones:
1. Volume confirmation — ¿hay volumen suficiente en la ruptura?
2. False breakout filter — ¿el cuerpo de la vela confirma o es pura mecha?
3. Retest detection — ¿el precio retestó el nivel roto y rebotó?

Sin validación de breakout, los chart patterns generan muchos falsos positivos.

v0.7.0
"""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .patterns import DetectedPattern

logger = logging.getLogger(__name__)


@dataclass
class BreakoutValidation:
    """Resultado de la validación de un breakout."""
    is_valid: bool                  # ¿Breakout confirmado?
    quality: str                    # "strong" | "moderate" | "weak" | "failed"
    quality_score: float            # 0-100

    # Detalles
    volume_confirmed: bool          # Volumen > 1.5x promedio en ruptura
    volume_ratio_at_breakout: float # Ratio de volumen en la vela de ruptura
    body_ratio: float               # Ratio cuerpo/rango total de la vela (>0.5 = bueno)
    consecutive_closes: int         # Velas consecutivas cerrando fuera del nivel
    retest_detected: bool           # ¿Retestó el nivel roto?
    retest_held: bool               # ¿El retest respetó el nivel? (el mejor confirmador)

    def prompt_section(self) -> str:
        """Genera texto para el prompt de Claude."""
        emoji = {"strong": "💪", "moderate": "👍", "weak": "⚠️", "failed": "❌"}
        lines = [
            f"Calidad: {self.quality.upper()} {emoji.get(self.quality, '')} "
            f"(score: {self.quality_score:.0f}/100)",
            f"  Volumen en ruptura: {self.volume_ratio_at_breakout:.1f}x "
            f"{'✅' if self.volume_confirmed else '❌'}",
            f"  Cuerpo vs mecha: {self.body_ratio:.0%} "
            f"{'✅' if self.body_ratio > 0.5 else '⚠️ mucha mecha'}",
            f"  Cierres consecutivos fuera: {self.consecutive_closes} "
            f"{'✅' if self.consecutive_closes >= 2 else '⚠️ aún no confirmado'}",
        ]
        if self.retest_detected:
            lines.append(
                f"  Retest: {'✅ nivel respetado — mejor confirmación' if self.retest_held else '❌ nivel perdido — breakout falló'}"
            )
        return "\n".join(lines)


class BreakoutValidator:
    """
    Valida breakouts usando 3 criterios independientes.

    Usage:
        validator = BreakoutValidator()
        result = validator.validate(df, pattern)
    """

    def __init__(
        self,
        min_volume_ratio: float = 1.5,
        min_body_ratio: float = 0.4,
        min_consecutive: int = 2,
        retest_window: int = 10,
    ):
        self.min_volume_ratio = min_volume_ratio
        self.min_body_ratio = min_body_ratio
        self.min_consecutive = min_consecutive
        self.retest_window = retest_window

    def _check_volume(self, df: pd.DataFrame, breakout_level: float, direction: str) -> tuple:
        """
        Verifica si la vela que rompió el nivel tuvo volumen significativo.
        Returns: (confirmed: bool, ratio: float)
        """
        avg_volume = df["volume"].rolling(20).mean()

        # Encontrar la vela que rompió el nivel
        for i in range(len(df) - 1, max(len(df) - 20, 0), -1):
            close = df["close"].iloc[i]
            prev_close = df["close"].iloc[i - 1] if i > 0 else close

            broke_up = direction == "bullish" and prev_close <= breakout_level and close > breakout_level
            broke_down = direction == "bearish" and prev_close >= breakout_level and close < breakout_level

            if broke_up or broke_down:
                vol = df["volume"].iloc[i]
                avg = avg_volume.iloc[i] if not np.isnan(avg_volume.iloc[i]) else vol
                ratio = vol / avg if avg > 0 else 1.0
                return ratio >= self.min_volume_ratio, float(ratio)

        # Si no encontramos la vela exacta, usar la última
        vol = df["volume"].iloc[-1]
        avg = avg_volume.iloc[-1] if not np.isnan(avg_volume.iloc[-1]) else vol
        ratio = vol / avg if avg > 0 else 1.0
        return ratio >= self.min_volume_ratio, float(ratio)

    def _check_body_ratio(self, df: pd.DataFrame, breakout_level: float, direction: str) -> float:
        """
        Verifica el ratio cuerpo/rango de la vela de breakout.
        Un cuerpo grande = convicción. Mucha mecha = indecisión/trampa.
        """
        for i in range(len(df) - 1, max(len(df) - 10, 0), -1):
            close = df["close"].iloc[i]
            open_p = df["open"].iloc[i]
            high = df["high"].iloc[i]
            low = df["low"].iloc[i]

            candle_range = high - low
            if candle_range == 0:
                continue

            body = abs(close - open_p)
            ratio = body / candle_range

            # Verificar si esta vela cruzó el nivel
            if direction == "bullish" and close > breakout_level and low <= breakout_level:
                return float(ratio)
            elif direction == "bearish" and close < breakout_level and high >= breakout_level:
                return float(ratio)

        return 0.5  # Default neutral

    def _check_consecutive_closes(self, df: pd.DataFrame, breakout_level: float, direction: str) -> int:
        """
        Cuenta cuántas velas consecutivas cerraron fuera del nivel
        desde la ruptura. Más = más fuerte.
        """
        count = 0
        for i in range(len(df) - 1, max(len(df) - 15, 0), -1):
            close = df["close"].iloc[i]
            if direction == "bullish" and close > breakout_level:
                count += 1
            elif direction == "bearish" and close < breakout_level:
                count += 1
            else:
                break  # Rompió la racha
        return count

    def _check_retest(self, df: pd.DataFrame, breakout_level: float, direction: str) -> tuple:
        """
        Detecta si el precio retestó el nivel roto después del breakout.
        El retest es la mejor confirmación posible:
        - En breakout alcista: el precio baja hasta el nivel roto y rebota
        - En breakout bajista: el precio sube hasta el nivel roto y rechaza

        Returns: (retest_detected: bool, retest_held: bool)
        """
        tolerance = abs(breakout_level * 0.005)  # 0.5% de tolerancia

        # Buscar en las últimas N velas post-breakout
        broke = False
        retest_detected = False
        retest_held = False

        for i in range(len(df) - self.retest_window, len(df)):
            if i < 0:
                continue

            close = df["close"].iloc[i]
            low = df["low"].iloc[i]
            high = df["high"].iloc[i]

            if direction == "bullish":
                if close > breakout_level and not broke:
                    broke = True
                elif broke and low <= breakout_level + tolerance:
                    retest_detected = True
                    # ¿Rebotó? El cierre está arriba del nivel
                    if close > breakout_level:
                        retest_held = True
            else:
                if close < breakout_level and not broke:
                    broke = True
                elif broke and high >= breakout_level - tolerance:
                    retest_detected = True
                    if close < breakout_level:
                        retest_held = True

        return retest_detected, retest_held

    def validate(self, df: pd.DataFrame, pattern: DetectedPattern) -> BreakoutValidation:
        """
        Valida un breakout usando los 3 criterios.

        Args:
            df: DataFrame OHLCV.
            pattern: Patrón detectado con breakout potencial.

        Returns:
            BreakoutValidation con el veredicto.
        """
        if not pattern.breakout_occurred:
            return BreakoutValidation(
                is_valid=False, quality="failed", quality_score=0,
                volume_confirmed=False, volume_ratio_at_breakout=0,
                body_ratio=0, consecutive_closes=0,
                retest_detected=False, retest_held=False,
            )

        level = pattern.breakout_level
        direction = pattern.direction

        vol_confirmed, vol_ratio = self._check_volume(df, level, direction)
        body_ratio = self._check_body_ratio(df, level, direction)
        consec = self._check_consecutive_closes(df, level, direction)
        retest_det, retest_held = self._check_retest(df, level, direction)

        # Scoring
        score = 0
        score += 35 if vol_confirmed else max(vol_ratio / self.min_volume_ratio * 15, 0)
        score += 25 if body_ratio >= self.min_body_ratio else body_ratio * 25
        score += min(consec * 10, 20)
        if retest_det and retest_held:
            score += 20  # Mejor confirmación posible
        elif retest_det and not retest_held:
            score -= 15  # Retest falló — señal de debilidad

        score = max(0, min(score, 100))

        if score >= 70:
            quality = "strong"
        elif score >= 45:
            quality = "moderate"
        elif score >= 25:
            quality = "weak"
        else:
            quality = "failed"

        is_valid = quality in ("strong", "moderate")

        result = BreakoutValidation(
            is_valid=is_valid,
            quality=quality,
            quality_score=score,
            volume_confirmed=vol_confirmed,
            volume_ratio_at_breakout=round(vol_ratio, 2),
            body_ratio=round(body_ratio, 3),
            consecutive_closes=consec,
            retest_detected=retest_det,
            retest_held=retest_held,
        )

        logger.info(
            f"Breakout {pattern.pattern_type}: {quality} ({score:.0f}/100) | "
            f"Vol: {vol_ratio:.1f}x | Body: {body_ratio:.0%} | "
            f"Closes: {consec} | Retest: {'held' if retest_held else 'no'}"
        )

        return result
