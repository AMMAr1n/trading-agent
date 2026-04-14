"""
targets.py — Calculador de targets basado en geometría de patrones
Calcula TP y SL usando la estructura del patrón, no ATR genérico.

Cada patrón tiene su propia fórmula de proyección:
- Double top/bottom: distancia soporte-resistencia
- Triangles: apertura del triángulo
- H&S: altura de la cabeza sobre neckline
- Wedges: apertura del wedge
- Diamond: altura del diamond

Además, fuerza un mínimo R:R de 1:2 y posiciona el SL
en el punto de invalidación del patrón.

v0.7.0
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .patterns import DetectedPattern
from .breakout import BreakoutValidation

logger = logging.getLogger(__name__)


@dataclass
class PatternTargets:
    """Targets calculados desde la geometría del patrón."""
    take_profit: float
    stop_loss: float
    risk_reward: float
    entry_price: float        # Precio de entrada sugerido

    # Contexto
    tp_method: str            # Descripción de cómo se calculó el TP
    sl_method: str            # Descripción de cómo se calculó el SL
    pattern_type: str
    is_valid_setup: bool      # True si el R:R cumple mínimo

    def prompt_section(self) -> str:
        """Genera texto para el prompt de Claude."""
        valid = "✅ Setup válido" if self.is_valid_setup else "❌ R:R insuficiente"
        return (
            f"TP: ${self.take_profit:,.4f} ({self.tp_method})\n"
            f"SL: ${self.stop_loss:,.4f} ({self.sl_method})\n"
            f"R:R: {self.risk_reward:.1f}:1 — {valid}\n"
            f"Entrada sugerida: ${self.entry_price:,.4f}"
        )


class TargetCalculator:
    """
    Calcula TP y SL basados en la geometría del patrón detectado.

    El SL siempre va en el punto de invalidación del patrón.
    El TP se calcula con la proyección geométrica.
    Si el R:R no cumple el mínimo, el setup se descarta.

    Usage:
        calc = TargetCalculator()
        targets = calc.calculate(pattern, breakout_validation, regime)
    """

    def __init__(self, min_rr: float = 2.0, sl_buffer_pct: float = 0.3):
        """
        Args:
            min_rr: Mínimo risk-reward ratio aceptable.
            sl_buffer_pct: Buffer adicional en % sobre el SL para evitar
                          ser sacado por wicks (0.3% por defecto).
        """
        self.min_rr = min_rr
        self.sl_buffer_pct = sl_buffer_pct / 100

    def calculate(
        self,
        pattern: DetectedPattern,
        breakout: Optional[BreakoutValidation] = None,
        tp_multiplier: float = 1.0,
        sl_multiplier: float = 1.0,
    ) -> PatternTargets:
        """
        Calcula targets usando la geometría del patrón.

        Args:
            pattern: Patrón detectado con sus niveles clave.
            breakout: Validación del breakout (para ajustar agresividad).
            tp_multiplier: Multiplicador del TP (del régimen de mercado).
            sl_multiplier: Multiplicador del SL (del régimen de mercado).

        Returns:
            PatternTargets con TP, SL, R:R, y si el setup es válido.
        """
        current = pattern.current_price
        direction = pattern.direction

        # TP base = proyección geométrica del patrón
        raw_tp = pattern.target_price

        # SL base = punto de invalidación del patrón
        raw_sl = pattern.invalidation_level

        # Aplicar buffer al SL
        if direction == "bullish":
            sl = raw_sl * (1 - self.sl_buffer_pct) * sl_multiplier
            # Ajustar TP con multiplicador de régimen
            tp_distance = abs(raw_tp - current) * tp_multiplier
            tp = current + tp_distance
        else:
            sl = raw_sl * (1 + self.sl_buffer_pct) * (2 - sl_multiplier)
            tp_distance = abs(current - raw_tp) * tp_multiplier
            tp = current - tp_distance

        # Si breakout es strong, podemos ser un poco más agresivos en TP
        if breakout and breakout.quality == "strong":
            if direction == "bullish":
                tp *= 1.1  # 10% más de TP
            else:
                tp *= 0.9  # 10% más de TP (precio más bajo)

        # Calcular R:R
        if direction == "bullish":
            risk = abs(current - sl)
            reward = abs(tp - current)
        else:
            risk = abs(sl - current)
            reward = abs(current - tp)

        rr = reward / risk if risk > 0 else 0

        # Si R:R es malo, intentar ajustar TP
        if 0 < rr < self.min_rr and risk > 0:
            needed_reward = risk * self.min_rr
            if direction == "bullish":
                tp = current + needed_reward
            else:
                tp = current - needed_reward
            rr = self.min_rr

        is_valid = rr >= self.min_rr

        # Métodos descriptivos
        tp_method = f"Proyección {pattern.pattern_type.replace('_', ' ')}"
        if tp_multiplier != 1.0:
            tp_method += f" x{tp_multiplier:.1f} (régimen)"

        sl_method = f"Invalidación del patrón"
        if sl_multiplier != 1.0:
            sl_method += f" x{sl_multiplier:.1f} (régimen)"

        # Entry price: idealmente en un pullback, no en la ruptura
        if direction == "bullish":
            entry = pattern.breakout_level * 1.002  # Justo arriba del breakout
        else:
            entry = pattern.breakout_level * 0.998  # Justo debajo del breakout

        targets = PatternTargets(
            take_profit=round(tp, 6),
            stop_loss=round(sl, 6),
            risk_reward=round(rr, 2),
            entry_price=round(entry, 6),
            tp_method=tp_method,
            sl_method=sl_method,
            pattern_type=pattern.pattern_type,
            is_valid_setup=is_valid,
        )

        logger.info(
            f"Targets {pattern.pattern_type}: "
            f"TP=${tp:,.4f} SL=${sl:,.4f} R:R={rr:.1f}:1 "
            f"{'✅ válido' if is_valid else '❌ R:R insuficiente'}"
        )

        return targets
