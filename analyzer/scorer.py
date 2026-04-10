"""
scorer.py — Calculador del score de confianza
Responsabilidad: combinar todos los indicadores técnicos en un
score de 0 a 100 que determina si el agente debe proponer una operación.

La lógica de ponderación refleja las decisiones estratégicas de Adrian:
- Volumen es el indicador más importante (30 puntos)
- RSI y MACD son los indicadores de momentum (25 puntos cada uno)
- Bollinger Bands confirman los extremos (20 puntos)

Solo se propone operación si el score supera MIN_SCORE (65 por defecto).
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .indicators import TechnicalIndicators
from .levels import SupportResistanceResult

logger = logging.getLogger(__name__)


# ─── PESOS DE CADA INDICADOR ──────────────────────────────────────────────────

WEIGHTS = {
    "volume":   30,   # El más importante — confirma si el movimiento es real
    "rsi":      25,   # Momentum — sobrecomprado/sobrevendido
    "macd":     25,   # Tendencia — dirección y fuerza del movimiento
    "bollinger": 20,  # Volatilidad — extremos estadísticos del precio
}

# Score máximo posible
MAX_SCORE = sum(WEIGHTS.values())  # 100


@dataclass
class ScoreBreakdown:
    """
    Desglose detallado del score de confianza.
    Permite entender exactamente por qué el agente decidió operar o no.
    """
    total: float               # Score total 0-100
    direction: str             # "long" | "short" | "neutral"

    # Puntos por indicador
    volume_points: float       # Hasta 30 puntos
    rsi_points: float          # Hasta 25 puntos
    macd_points: float         # Hasta 25 puntos
    bollinger_points: float    # Hasta 20 puntos

    # Contexto adicional
    trend: str                 # Tendencia general
    risk_pct: float            # % de riesgo si entra ahora
    suggested_sl: float        # Stop-loss dinámico sugerido
    suggested_tp: float        # Take-profit sugerido (ratio 1:2 mínimo)

    # Explicación en lenguaje natural para el prompt de Claude
    reasoning: str

    @property
    def is_tradeable(self) -> bool:
        """True si el score supera el umbral mínimo para operar."""
        return self.total >= 65 and self.direction != "neutral"

    @property
    def leverage_recommended(self) -> str:
        """
        Recomienda el apalancamiento basado en el score.
        Score 45-59 → 1x
        Score 60-74 → 2x
        Score 75+   → 3x
        """
        if self.total >= 75:
            return "3x"
        elif self.total >= 60:
            return "2x"
        return "1x"


class SignalScorer:
    """
    Calcula el score de confianza combinando indicadores técnicos y niveles.

    Uso:
        scorer = SignalScorer()
        score = scorer.calculate(indicators_1h, levels)
    """

    def score_volume(self, indicators: TechnicalIndicators) -> float:
        """
        Puntúa el volumen — hasta 30 puntos.

        El volumen es el indicador más importante porque sin volumen
        cualquier movimiento de precio puede ser una trampa.
        """
        ratio = indicators.volume.ratio

        if ratio >= 3.0:
            # Actividad institucional — señal muy fuerte
            return 30.0
        elif ratio >= 2.0:
            # Volumen muy alto
            return 24.0
        elif ratio >= 1.5:
            # Volumen alto — buena confirmación
            return 18.0
        elif ratio >= 1.2:
            # Volumen moderado — confirmación básica
            return 12.0
        elif ratio >= 0.8:
            # Volumen normal — no confirma ni niega
            return 6.0
        else:
            # Volumen bajo — señal poco confiable
            return 0.0

    def score_rsi(
        self,
        indicators: TechnicalIndicators,
        direction: str
    ) -> float:
        """
        Puntúa el RSI según la dirección propuesta — hasta 25 puntos.

        Para LONG: RSI bajo es bueno (sobrevendido = posible rebote)
        Para SHORT: RSI alto es bueno (sobrecomprado = posible caída)
        """
        rsi = indicators.rsi.value

        if direction == "long":
            if rsi < 20:
                return 25.0  # Extremo sobrevendido
            elif rsi < 30:
                return 20.0  # Sobrevendido
            elif rsi < 40:
                return 12.0  # Tendencia a la baja pero no extremo
            elif rsi < 50:
                return 6.0   # Neutral tirando a bajista
            else:
                return 0.0   # RSI alto no favorece long

        elif direction == "short":
            if rsi > 80:
                return 25.0  # Extremo sobrecomprado
            elif rsi > 70:
                return 20.0  # Sobrecomprado
            elif rsi > 60:
                return 12.0  # Tendencia alcista pero no extremo
            elif rsi > 50:
                return 6.0   # Neutral tirando a alcista
            else:
                return 0.0   # RSI bajo no favorece short

        return 0.0

    def score_macd(
        self,
        indicators: TechnicalIndicators,
        direction: str
    ) -> float:
        """
        Puntúa el MACD según la dirección propuesta — hasta 25 puntos.

        Un cruce reciente vale más que una tendencia ya establecida.
        """
        macd = indicators.macd

        if direction == "long":
            if macd.is_bullish_cross:
                return 25.0  # Cruce alcista reciente — señal más fuerte
            elif macd.is_bullish and macd.histogram > 0:
                return 15.0  # Tendencia alcista establecida
            elif macd.is_bullish:
                return 8.0   # Apenas alcista
            else:
                return 0.0

        elif direction == "short":
            if macd.is_bearish_cross:
                return 25.0  # Cruce bajista reciente — señal más fuerte
            elif macd.is_bearish and macd.histogram < 0:
                return 15.0  # Tendencia bajista establecida
            elif macd.is_bearish:
                return 8.0   # Apenas bajista
            else:
                return 0.0

        return 0.0

    def score_bollinger(
        self,
        indicators: TechnicalIndicators,
        direction: str
    ) -> float:
        """
        Puntúa las Bandas de Bollinger según la dirección — hasta 20 puntos.
        """
        bb = indicators.bollinger

        if direction == "long":
            if bb.is_at_lower_band:
                return 20.0  # Precio en banda inferior — rebote probable
            elif bb.is_squeeze:
                return 12.0  # Squeeze — movimiento explosivo próximo
            elif bb.percent_b < 0.3:
                return 8.0   # Precio en tercio inferior
            else:
                return 0.0

        elif direction == "short":
            if bb.is_at_upper_band:
                return 20.0  # Precio en banda superior — corrección probable
            elif bb.is_squeeze:
                return 12.0  # Squeeze — movimiento explosivo próximo
            elif bb.percent_b > 0.7:
                return 8.0   # Precio en tercio superior
            else:
                return 0.0

        return 0.0

    def calculate(
        self,
        indicators: TechnicalIndicators,
        levels: SupportResistanceResult,
        context_bonus: float = 0.0  # Bonus por contexto macro favorable
    ) -> ScoreBreakdown:
        """
        Calcula el score de confianza completo.

        context_bonus: puntos adicionales por contexto macro
                       (Fear & Greed favorable, noticias positivas, etc.)
                       Máximo +10 puntos desde capas superiores
        """
        # Determinar dirección basada en los indicadores
        direction = indicators.suggested_direction

        # Si no hay dirección clara, score 0
        if direction == "neutral":
            return ScoreBreakdown(
                total=0.0,
                direction="neutral",
                volume_points=0.0,
                rsi_points=0.0,
                macd_points=0.0,
                bollinger_points=0.0,
                trend=indicators.trend,
                risk_pct=0.0,
                suggested_sl=0.0,
                suggested_tp=0.0,
                reasoning="Señales contradictorias — sin dirección clara"
            )

        # Calcular puntos por indicador
        volume_pts = self.score_volume(indicators)
        rsi_pts = self.score_rsi(indicators, direction)
        macd_pts = self.score_macd(indicators, direction)
        bb_pts = self.score_bollinger(indicators, direction)

        # Score base
        base_score = volume_pts + rsi_pts + macd_pts + bb_pts

        # Aplicar bonus de contexto (máximo 10 puntos extra)
        context_bonus = min(context_bonus, 10.0)
        total_score = min(base_score + context_bonus, 100.0)

        # Stop-loss y take-profit dinámicos
        if direction == "long":
            sl = levels.dynamic_stop_loss_long
            risk_pct = levels.risk_pct_long
            # Take-profit: ratio mínimo 1:2
            tp = indicators.current_price + (indicators.current_price - sl) * 2
        else:
            sl = levels.dynamic_stop_loss_short
            risk_pct = levels.risk_pct_short
            tp = indicators.current_price - (sl - indicators.current_price) * 2

        # Generar explicación en lenguaje natural
        reasoning = self._build_reasoning(
            direction, indicators, volume_pts, rsi_pts,
            macd_pts, bb_pts, total_score, risk_pct
        )

        score = ScoreBreakdown(
            total=round(total_score, 1),
            direction=direction,
            volume_points=volume_pts,
            rsi_points=rsi_pts,
            macd_points=macd_pts,
            bollinger_points=bb_pts,
            trend=indicators.trend,
            risk_pct=round(risk_pct, 2),
            suggested_sl=sl,
            suggested_tp=round(tp, 2),
            reasoning=reasoning
        )

        logger.info(
            f"{indicators.symbol}/{indicators.timeframe} — "
            f"Score: {total_score:.0f}/100 | "
            f"Direction: {direction} | "
            f"Vol: {volume_pts:.0f} | RSI: {rsi_pts:.0f} | "
            f"MACD: {macd_pts:.0f} | BB: {bb_pts:.0f} | "
            f"Tradeable: {score.is_tradeable}"
        )

        return score

    def _build_reasoning(
        self,
        direction: str,
        indicators: TechnicalIndicators,
        volume_pts: float,
        rsi_pts: float,
        macd_pts: float,
        bb_pts: float,
        total: float,
        risk_pct: float
    ) -> str:
        """
        Construye una explicación en lenguaje natural del score.
        Esta explicación va al prompt de Claude para que entienda el contexto.
        """
        parts = []

        direction_str = "LONG (precio sube)" if direction == "long" else "SHORT (precio baja)"
        parts.append(f"Dirección sugerida: {direction_str}")
        parts.append(f"Tendencia general: {indicators.trend}")

        # Volumen
        if volume_pts >= 24:
            parts.append(f"Volumen: MUY ALTO ({indicators.volume.ratio:.1f}x el promedio) — posible actividad institucional")
        elif volume_pts >= 18:
            parts.append(f"Volumen: ALTO ({indicators.volume.ratio:.1f}x el promedio) — confirma la señal")
        elif volume_pts >= 12:
            parts.append(f"Volumen: moderado ({indicators.volume.ratio:.1f}x el promedio)")
        else:
            parts.append(f"Volumen: bajo ({indicators.volume.ratio:.1f}x el promedio) — señal débil")

        # RSI
        parts.append(
            f"RSI: {indicators.rsi.value:.1f} ({indicators.rsi.signal}) — "
            f"{'favorece ' + direction if rsi_pts > 0 else 'no favorece ' + direction}"
        )

        # MACD
        parts.append(
            f"MACD: {indicators.macd.signal} — "
            f"{'cruce reciente' if 'cross' in indicators.macd.signal else 'tendencia establecida'}"
        )

        # Bollinger
        parts.append(f"Bollinger: precio al {indicators.bollinger.percent_b * 100:.0f}% de las bandas ({indicators.bollinger.signal})")

        # Riesgo
        parts.append(f"Riesgo estimado: {risk_pct:.1f}% del capital por operación")
        parts.append(f"Score final: {total:.0f}/100")

        return " | ".join(parts)
