"""
targets.py — Calculador de targets basado en geometría de patrones
v0.7.2 — Fix: valida que el precio actual esté cerca del breakout level.
Si el breakout level está a más del 10% del precio actual, el setup
se marca como inválido. Esto evita targets absurdos de patrones macro
que aún no han hecho breakout.

Además:
- Rechaza targets negativos
- Rechaza SL que esté del lado equivocado del precio
- Fuerza R:R mínimo 2:1
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .patterns import DetectedPattern
from .breakout import BreakoutValidation

logger = logging.getLogger(__name__)


@dataclass
class PatternTargets:
    take_profit: float
    stop_loss: float
    risk_reward: float
    entry_price: float
    tp_method: str
    sl_method: str
    pattern_type: str
    is_valid_setup: bool

    def prompt_section(self) -> str:
        valid = "✅ Setup válido" if self.is_valid_setup else "❌ R:R insuficiente"
        return (
            f"TP: ${self.take_profit:,.4f} ({self.tp_method})\n"
            f"SL: ${self.stop_loss:,.4f} ({self.sl_method})\n"
            f"R:R: {self.risk_reward:.1f}:1 — {valid}\n"
            f"Entrada sugerida: ${self.entry_price:,.4f}"
        )


class TargetCalculator:

    def __init__(self, min_rr: float = 2.0, sl_buffer_pct: float = 0.3,
                 max_distance_pct: float = 10.0):
        """
        Args:
            min_rr: Mínimo risk-reward ratio aceptable.
            sl_buffer_pct: Buffer adicional en % sobre el SL.
            max_distance_pct: Máxima distancia % del precio al breakout
                             para considerar targets válidos.
        """
        self.min_rr = min_rr
        self.sl_buffer_pct = sl_buffer_pct / 100
        self.max_distance_pct = max_distance_pct / 100

    def calculate(self, pattern, breakout=None, tp_multiplier=1.0, sl_multiplier=1.0):
        current = pattern.current_price
        direction = pattern.direction

        # ── v0.7.2: Validar proximidad al breakout ───────────────────────
        # Si el precio está a más del max_distance_pct del breakout level,
        # el patrón es macro y aún no ha hecho breakout → targets inválidos
        breakout_level = pattern.breakout_level
        if current > 0 and breakout_level > 0:
            distance_pct = abs(current - breakout_level) / current
            if distance_pct > self.max_distance_pct and not pattern.breakout_occurred:
                logger.warning(
                    f"Targets {pattern.pattern_type}: breakout ${breakout_level:,.4f} "
                    f"está a {distance_pct*100:.1f}% del precio ${current:,.4f} "
                    f"— demasiado lejos, targets inválidos"
                )
                return PatternTargets(
                    take_profit=0, stop_loss=0, risk_reward=0,
                    entry_price=breakout_level,
                    tp_method=f"INVÁLIDO — breakout a {distance_pct*100:.0f}% de distancia",
                    sl_method="INVÁLIDO", pattern_type=pattern.pattern_type,
                    is_valid_setup=False,
                )
        # ─────────────────────────────────────────────────────────────────

        raw_tp = pattern.target_price
        raw_sl = pattern.invalidation_level

        # ── v0.7.2: Rechazar targets negativos ───────────────────────────
        if raw_tp <= 0:
            logger.warning(f"Targets {pattern.pattern_type}: TP negativo ${raw_tp:,.4f} — inválido")
            return PatternTargets(
                take_profit=0, stop_loss=0, risk_reward=0,
                entry_price=current,
                tp_method="INVÁLIDO — target negativo",
                sl_method="INVÁLIDO", pattern_type=pattern.pattern_type,
                is_valid_setup=False,
            )
        # ─────────────────────────────────────────────────────────────────

        # Aplicar buffer al SL
        if direction == "bullish":
            sl = raw_sl * (1 - self.sl_buffer_pct) * sl_multiplier
            tp_distance = abs(raw_tp - current) * tp_multiplier
            tp = current + tp_distance
        else:
            sl = raw_sl * (1 + self.sl_buffer_pct) * (2 - sl_multiplier)
            tp_distance = abs(current - raw_tp) * tp_multiplier
            tp = current - tp_distance

        # ── v0.7.2: Validar que SL esté del lado correcto ───────────────
        if direction == "bullish" and sl >= current:
            logger.warning(f"Targets {pattern.pattern_type}: SL ${sl:,.4f} >= precio ${current:,.4f} (bullish) — inválido")
            return PatternTargets(
                take_profit=0, stop_loss=0, risk_reward=0,
                entry_price=current,
                tp_method="INVÁLIDO — SL arriba del precio",
                sl_method="INVÁLIDO", pattern_type=pattern.pattern_type,
                is_valid_setup=False,
            )
        if direction == "bearish" and sl <= current:
            logger.warning(f"Targets {pattern.pattern_type}: SL ${sl:,.4f} <= precio ${current:,.4f} (bearish) — inválido")
            return PatternTargets(
                take_profit=0, stop_loss=0, risk_reward=0,
                entry_price=current,
                tp_method="INVÁLIDO — SL debajo del precio",
                sl_method="INVÁLIDO", pattern_type=pattern.pattern_type,
                is_valid_setup=False,
            )
        # ─────────────────────────────────────────────────────────────────

        # ── v0.7.2: Validar que TP esté del lado correcto ───────────────
        if direction == "bullish" and tp <= current:
            tp = current + abs(current - sl) * self.min_rr  # Forzar TP válido
        if direction == "bearish" and tp >= current:
            tp = current - abs(sl - current) * self.min_rr
        if tp <= 0:
            logger.warning(f"Targets {pattern.pattern_type}: TP calculado negativo — inválido")
            return PatternTargets(
                take_profit=0, stop_loss=0, risk_reward=0,
                entry_price=current,
                tp_method="INVÁLIDO — TP negativo después de ajuste",
                sl_method="INVÁLIDO", pattern_type=pattern.pattern_type,
                is_valid_setup=False,
            )
        # ─────────────────────────────────────────────────────────────────

        # Si breakout es strong, TP más agresivo
        if breakout and breakout.quality == "strong":
            if direction == "bullish":
                tp *= 1.1
            else:
                tp *= 0.9

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
                if tp <= 0:
                    return PatternTargets(
                        take_profit=0, stop_loss=0, risk_reward=0,
                        entry_price=current,
                        tp_method="INVÁLIDO — ajuste R:R produce TP negativo",
                        sl_method="INVÁLIDO", pattern_type=pattern.pattern_type,
                        is_valid_setup=False,
                    )
            rr = self.min_rr

        is_valid = rr >= self.min_rr

        tp_method = f"Proyección {pattern.pattern_type.replace('_', ' ')}"
        if tp_multiplier != 1.0:
            tp_method += f" x{tp_multiplier:.1f} (régimen)"
        sl_method = "Invalidación del patrón"
        if sl_multiplier != 1.0:
            sl_method += f" x{sl_multiplier:.1f} (régimen)"

        if direction == "bullish":
            entry = pattern.breakout_level * 1.002
        else:
            entry = pattern.breakout_level * 0.998

        targets = PatternTargets(
            take_profit=round(tp, 6),
            stop_loss=round(sl, 6),
            risk_reward=round(rr, 2),
            entry_price=round(entry, 6),
            tp_method=tp_method, sl_method=sl_method,
            pattern_type=pattern.pattern_type,
            is_valid_setup=is_valid,
        )

        logger.info(
            f"Targets {pattern.pattern_type}: "
            f"TP=${tp:,.4f} SL=${sl:,.4f} R:R={rr:.1f}:1 "
            f"{'✅ válido' if is_valid else '❌ inválido'}"
        )

        return targets
