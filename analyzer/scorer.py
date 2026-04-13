"""
scorer.py — Calculador del score de confianza
Responsabilidad: combinar todos los indicadores técnicos en un
score de 0 a 100 que determina si el agente debe proponer una operación.

Ponderación basada en Technical Analysis Framework:
- Tendencia/EMAs:  25 puntos — alineación de medias móviles (framework: MA alignment)
- Volumen:         25 puntos — confirmación del movimiento (framework: volume confirms price)
- MACD:            20 puntos — momentum y cruces (framework: continuation patterns)
- RSI:             15 puntos — condición del momentum (tendencial Y reversión)
- Bollinger:       15 puntos — contexto de volatilidad y squeeze

El RSI ahora puntúa tanto en mercados tendenciales (RSI 50-65 = momentum alcista)
como en mercados de reversión (RSI < 30 = sobrevendido), siguiendo el framework.
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

# ─── PESOS ────────────────────────────────────────────────────────────────────
WEIGHTS = {
    "ema_trend":  25,   # Alineación de EMAs — estructura de tendencia
    "volume":     25,   # Confirmación de volumen
    "macd":       20,   # Momentum y cruces
    "rsi":        15,   # Condición del momentum
    "bollinger":  15,   # Volatilidad y contexto
}
MAX_SCORE = sum(WEIGHTS.values())  # 100


@dataclass
class ScoreBreakdown:
    total: float
    direction: str

    ema_trend_points: float    # Hasta 25 puntos
    volume_points: float       # Hasta 25 puntos
    macd_points: float         # Hasta 20 puntos
    rsi_points: float          # Hasta 15 puntos
    bollinger_points: float    # Hasta 15 puntos

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
        """
        Score 30-44 → 1x (mercado quieto, aprendiendo)
        Score 45-59 → 1x (mercado normal)
        Score 60-74 → 2x (mercado activo)
        Score 75+   → 3x (mercado fuerte)
        """
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
        """
        Puntúa la alineación de EMAs — hasta 25 puntos.

        Basado en el framework: 'Price above all MAs = strong bullish structure'
        La alineación EMA20 > EMA50 > EMA200 con precio encima es la señal
        más fuerte de tendencia alcista establecida.
        """
        price = indicators.current_price
        ema20 = indicators.ema_20
        ema50 = indicators.ema_50
        ema200 = indicators.ema_200

        if direction == "long":
            # Alineación perfecta alcista: precio > EMA20 > EMA50 > EMA200
            if price > ema20 > ema50 > ema200:
                return 25.0  # Estructura alcista perfecta
            # Precio sobre todas las EMAs pero no en orden perfecto
            elif price > ema20 and price > ema50 and price > ema200:
                return 18.0
            # Precio sobre EMA20 y EMA50 (tendencia de corto/medio plazo)
            elif price > ema20 and price > ema50:
                return 12.0
            # Precio solo sobre EMA20 (tendencia de corto plazo)
            elif price > ema20:
                return 6.0
            # Precio bajo todas las EMAs — contra tendencia
            else:
                return 0.0

        elif direction == "short":
            # Alineación perfecta bajista: precio < EMA20 < EMA50 < EMA200
            if price < ema20 < ema50 < ema200:
                return 25.0
            elif price < ema20 and price < ema50 and price < ema200:
                return 18.0
            elif price < ema20 and price < ema50:
                return 12.0
            elif price < ema20:
                return 6.0
            else:
                return 0.0

        return 0.0

    def score_volume(self, indicators: TechnicalIndicators) -> float:
        """
        Puntúa el volumen — hasta 25 puntos.

        Framework: 'Rising prices + Rising volume = healthy uptrend'
        'Low volume breakout often leads to failed breakout'
        """
        ratio = indicators.volume.ratio

        if ratio >= 3.0:
            return 25.0   # Actividad institucional confirmada
        elif ratio >= 2.0:
            return 20.0   # Volumen muy alto
        elif ratio >= 1.5:
            return 16.0   # Volumen alto — buena confirmación
        elif ratio >= 1.2:
            return 12.0   # Volumen moderado
        elif ratio >= 0.8:
            return 8.0    # Volumen normal
        elif ratio >= 0.5:
            return 5.0    # Volumen bajo — cautela
        else:
            return 2.0    # Volumen muy bajo

    def score_macd(
        self,
        indicators: TechnicalIndicators,
        direction: str
    ) -> float:
        """
        Puntúa el MACD — hasta 20 puntos.

        Framework: cruces recientes valen más que tendencia establecida.
        Un cruce alcista con histograma positivo es la señal más fuerte.
        """
        macd = indicators.macd

        if direction == "long":
            if macd.is_bullish_cross:
                return 20.0   # Cruce alcista reciente — señal más fuerte
            elif macd.is_bullish and macd.histogram > 0:
                return 14.0   # Tendencia alcista con momentum positivo
            elif macd.is_bullish:
                return 7.0    # Alcista pero sin confirmación de histograma
            else:
                return 0.0

        elif direction == "short":
            if macd.is_bearish_cross:
                return 20.0
            elif macd.is_bearish and macd.histogram < 0:
                return 14.0
            elif macd.is_bearish:
                return 7.0
            else:
                return 0.0

        return 0.0

    def score_rsi(
        self,
        indicators: TechnicalIndicators,
        direction: str
    ) -> float:
        """
        Puntúa el RSI — hasta 15 puntos.

        DOBLE MODO según el framework:
        1. TENDENCIAL: RSI en zona 50-65 para long = momentum alcista saludable
           (framework: 'Rising prices + indicators in healthy range')
        2. REVERSIÓN: RSI < 30 para long = sobrevendido, posible rebote
           (framework: 'Bullish divergence: price making new lows but RSI declining')

        El RSI en 60-70 en un uptrend es POSITIVO (tendencia fuerte),
        no negativo como lo tenía antes.
        """
        rsi = indicators.rsi.value
        trend = indicators.trend  # "strong_uptrend", "uptrend", "sideways", etc.

        is_uptrend = "uptrend" in trend.lower() if trend else False
        is_downtrend = "downtrend" in trend.lower() if trend else False

        if direction == "long":
            # ── Modo Reversión (sobrevendido) ─────────────────────────────
            if rsi < 25:
                return 15.0   # Extremo sobrevendido — rebote muy probable
            elif rsi < 35:
                return 12.0   # Sobrevendido — buena oportunidad de reversión

            # ── Modo Tendencial (momentum alcista) ─────────────────────────
            elif rsi < 45:
                return 5.0    # Recuperándose desde zona baja
            elif rsi < 55:
                return 8.0    # Zona neutral — ok para operar en tendencia
            elif rsi < 65:
                return 12.0   # ★ Momentum alcista saludable — ideal en uptrend
            elif rsi < 70:
                return 8.0    # Momentum fuerte, aún no sobrecomprado
            elif rsi < 75:
                return 4.0    # Acercándose a sobrecompra — precaución
            else:
                return 0.0    # Sobrecomprado — no entrar long

        elif direction == "short":
            if rsi > 75:
                return 15.0   # Extremo sobrecomprado
            elif rsi > 65:
                return 12.0   # Sobrecomprado
            elif rsi > 55:
                return 5.0    # Recuperándose desde zona alta
            elif rsi > 45:
                return 8.0    # Zona neutral
            elif rsi > 35:
                return 12.0   # ★ Momentum bajista saludable
            elif rsi > 30:
                return 8.0    # Momentum bajista fuerte
            elif rsi > 25:
                return 4.0    # Acercándose a sobrevendido
            else:
                return 0.0    # Sobrevendido — no entrar short

        return 0.0

    def score_bollinger(
        self,
        indicators: TechnicalIndicators,
        direction: str
    ) -> float:
        """
        Puntúa las Bandas de Bollinger — hasta 15 puntos.

        Framework: Squeeze precede movimiento explosivo.
        En tendencia: precio en tercio medio-superior es normal y positivo.
        En reversión: precio en banda inferior es señal de rebote.
        """
        bb = indicators.bollinger

        if direction == "long":
            if bb.is_squeeze:
                return 15.0   # Squeeze — explosión alcista probable
            elif bb.is_at_lower_band:
                return 12.0   # Precio en banda inferior — rebote probable
            elif bb.percent_b < 0.3:
                return 8.0    # Precio en tercio inferior
            elif bb.percent_b < 0.5:
                return 6.0    # Precio en zona media — neutral ok
            elif bb.percent_b < 0.7:
                return 4.0    # Precio en tercio superior — ok en uptrend
            else:
                return 0.0    # Precio en banda superior — sobreextendido

        elif direction == "short":
            if bb.is_squeeze:
                return 15.0
            elif bb.is_at_upper_band:
                return 12.0
            elif bb.percent_b > 0.7:
                return 8.0
            elif bb.percent_b > 0.5:
                return 6.0
            elif bb.percent_b > 0.3:
                return 4.0
            else:
                return 0.0

        return 0.0

    def score_candlestick_patterns(
        self,
        indicators: TechnicalIndicators,
        direction: str
    ) -> float:
        """
        Puntúa patrones de velas detectados — hasta 10 puntos bonus.
        Patrones alineados con la dirección suman, contrarios restan.
        """
        patterns = getattr(indicators, 'candlestick_patterns', []) or []
        if not patterns:
            return 0.0

        BULLISH_PATTERNS = {
            "hammer_bullish": 8,
            "engulfing_bullish": 10,
            "morning_star_bullish": 10,
            "marubozu_bullish": 6,
            "doji": 3,  # neutral — indica indecisión
        }
        BEARISH_PATTERNS = {
            "shooting_star_bearish": 8,
            "engulfing_bearish": 10,
            "evening_star_bearish": 10,
            "marubozu_bearish": 6,
            "doji": 3,
        }

        score = 0.0
        for p in patterns:
            if direction == "long" and p in BULLISH_PATTERNS:
                score += BULLISH_PATTERNS[p]
            elif direction == "short" and p in BEARISH_PATTERNS:
                score += BEARISH_PATTERNS[p]
            elif direction == "long" and p in BEARISH_PATTERNS and p != "doji":
                score -= BEARISH_PATTERNS[p] * 0.5  # penalización parcial
            elif direction == "short" and p in BULLISH_PATTERNS and p != "doji":
                score -= BULLISH_PATTERNS[p] * 0.5

        return max(min(score, 10.0), -10.0)  # clamp entre -10 y +10

    def score_candlestick_patterns(
        self,
        indicators: TechnicalIndicators,
        direction: str
    ) -> float:
        """
        Puntúa patrones de velas — hasta 10 puntos bonus.
        Funciona con el formato de ta-lib "Nombre (bias bullish/bearish)"
        y con el formato legacy "nombre_bullish/bearish".
        Patrones de confirmación suman, patrones contrarios restan.
        """
        patterns = indicators.candlestick_patterns or []
        if not patterns:
            return 0.0

        # Patrones fuertes (mayor peso)
        STRONG_KEYWORDS = {
            "engulfing", "morning star", "evening star",
            "three white soldiers", "three black crows",
            "abandoned baby", "morning doji star", "evening doji star",
            "marubozu", "kicking"
        }

        score = 0.0
        for p in patterns:
            p_lower = p.lower()

            # Determinar sesgo del patrón
            if "bullish" in p_lower:
                bias = "bullish"
            elif "bearish" in p_lower:
                bias = "bearish"
            else:
                continue  # neutral — no suma ni resta

            # Determinar si es fuerte o débil
            is_strong = any(kw in p_lower for kw in STRONG_KEYWORDS)
            pts_confirm = 10.0 if is_strong else 5.0
            pts_contra   = -8.0 if is_strong else -4.0

            if direction == "long":
                score += pts_confirm if bias == "bullish" else pts_contra
            elif direction == "short":
                score += pts_confirm if bias == "bearish" else pts_contra

        return max(min(score, 15.0), -10.0)

    def calculate(
        self,
        indicators: TechnicalIndicators,
        levels: SupportResistanceResult,
        context_bonus: float = 0.0
    ) -> ScoreBreakdown:
        """
        Calcula el score de confianza completo usando el framework técnico.
        """
        direction = indicators.suggested_direction

        if direction == "neutral":
            return ScoreBreakdown(
                total=0.0,
                direction="neutral",
                ema_trend_points=0.0,
                volume_points=0.0,
                macd_points=0.0,
                rsi_points=0.0,
                bollinger_points=0.0,
                trend=indicators.trend,
                risk_pct=0.0,
                suggested_sl=0.0,
                suggested_tp=0.0,
                reasoning="Señales contradictorias — sin dirección clara"
            )

        # Calcular puntos por componente
        ema_pts = self.score_ema_trend(indicators, direction)
        vol_pts = self.score_volume(indicators)
        macd_pts = self.score_macd(indicators, direction)
        rsi_pts = self.score_rsi(indicators, direction)
        bb_pts = self.score_bollinger(indicators, direction)

        candle_pts = self.score_candlestick_patterns(indicators, direction)
        base_score = ema_pts + vol_pts + macd_pts + rsi_pts + bb_pts + candle_pts
        context_bonus = min(context_bonus, 10.0)
        total_score = min(base_score + context_bonus, 100.0)

        # Stop-loss y take-profit dinámicos
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
            total_score, risk_pct
        )

        score = ScoreBreakdown(
            total=round(total_score, 1),
            direction=direction,
            ema_trend_points=ema_pts,
            volume_points=vol_pts,
            macd_points=macd_pts,
            rsi_points=rsi_pts,
            bollinger_points=bb_pts,
            trend=indicators.trend,
            risk_pct=round(risk_pct, 2),
            suggested_sl=sl,
            suggested_tp=round(tp, 2),
            reasoning=reasoning
        )

        patterns_str = ",".join(getattr(indicators, 'candlestick_patterns', []) or [])
        logger.info(
            f"{indicators.symbol}/{indicators.timeframe} — "
            f"Score: {total_score:.0f}/100 | "
            f"Direction: {direction} | "
            f"EMA: {ema_pts:.0f} | Vol: {vol_pts:.0f} | "
            f"MACD: {macd_pts:.0f} | RSI: {rsi_pts:.0f} | "
            f"BB: {bb_pts:.0f} | Candle: {candle_pts:.0f}"
            + (f" | Patrones: {patterns_str}" if patterns_str else "") +
            f" | Tradeable: {score.is_tradeable}"
        )

        return score

    def _build_reasoning(
        self,
        direction: str,
        indicators: TechnicalIndicators,
        ema_pts: float,
        vol_pts: float,
        macd_pts: float,
        rsi_pts: float,
        bb_pts: float,
        total: float,
        risk_pct: float
    ) -> str:
        parts = []
        price = indicators.current_price

        direction_str = "LONG (precio sube)" if direction == "long" else "SHORT (precio baja)"
        parts.append(f"Dirección: {direction_str}")
        parts.append(f"Tendencia: {indicators.trend}")

        # EMAs
        ema20, ema50, ema200 = indicators.ema_20, indicators.ema_50, indicators.ema_200
        if ema_pts >= 25:
            parts.append(f"EMAs: alineación alcista perfecta (precio > EMA20 > EMA50 > EMA200) — estructura fuerte")
        elif ema_pts >= 18:
            parts.append(f"EMAs: precio sobre todas las medias — tendencia confirmada")
        elif ema_pts >= 12:
            parts.append(f"EMAs: precio sobre EMA20 y EMA50 — tendencia de corto/medio plazo")
        elif ema_pts >= 6:
            parts.append(f"EMAs: precio sobre EMA20 — tendencia de corto plazo solamente")
        else:
            parts.append(f"EMAs: precio bajo las medias — señal débil o contra tendencia")

        # Volumen
        ratio = indicators.volume.ratio
        if vol_pts >= 20:
            parts.append(f"Volumen: MUY ALTO ({ratio:.1f}x) — confirma fuertemente el movimiento")
        elif vol_pts >= 16:
            parts.append(f"Volumen: ALTO ({ratio:.1f}x) — buena confirmación")
        elif vol_pts >= 8:
            parts.append(f"Volumen: normal ({ratio:.1f}x)")
        else:
            parts.append(f"Volumen: bajo ({ratio:.1f}x) — señal sin convicción")

        # RSI
        rsi = indicators.rsi.value
        if rsi_pts >= 12:
            if rsi < 35:
                parts.append(f"RSI: {rsi:.1f} — sobrevendido, rebote probable")
            else:
                parts.append(f"RSI: {rsi:.1f} — momentum alcista saludable")
        elif rsi_pts >= 6:
            parts.append(f"RSI: {rsi:.1f} — zona neutral")
        elif rsi_pts > 0:
            parts.append(f"RSI: {rsi:.1f} — acercándose a zona de precaución")
        else:
            parts.append(f"RSI: {rsi:.1f} — sobrecomprado, no favorece long")

        # MACD
        if macd_pts >= 20:
            parts.append(f"MACD: cruce alcista reciente — señal fuerte")
        elif macd_pts >= 14:
            parts.append(f"MACD: tendencia alcista con histograma positivo")
        elif macd_pts >= 7:
            parts.append(f"MACD: alcista débil")
        else:
            parts.append(f"MACD: bajista — no confirma")

        # Bollinger
        pct_b = indicators.bollinger.percent_b
        if bb_pts >= 15:
            parts.append(f"BB: squeeze detectado — movimiento explosivo próximo")
        elif bb_pts >= 12:
            parts.append(f"BB: precio en banda inferior — posible rebote")
        elif bb_pts >= 6:
            parts.append(f"BB: precio en zona media-inferior — ok para entrada")
        elif bb_pts >= 4:
            parts.append(f"BB: precio en tercio superior — precaución")
        else:
            parts.append(f"BB: precio sobreextendido en banda superior — no entrar")

        parts.append(f"Riesgo: {risk_pct:.1f}% | Score: {total:.0f}/100")

        return " | ".join(parts)
