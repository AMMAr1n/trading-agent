"""
analyzer.py — Orquestador del motor de análisis técnico
Responsabilidad: coordinar indicadores, niveles y scorer para producir
señales concretas listas para que Claude las evalúe.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

from collector.models import CollectedSnapshot, ALL_SYMBOLS, FUTURES_SYMBOLS
from .indicators import TechnicalIndicatorCalculator, TechnicalIndicators
from .levels import SupportResistanceDetector, SupportResistanceResult
from .scorer import SignalScorer, ScoreBreakdown

load_dotenv(override=False)
logger = logging.getLogger(__name__)

# Timeframe principal para el análisis — balance entre señal y ruido
PRIMARY_TIMEFRAME = "1h"
CONFIRMATION_TIMEFRAME = "2h"  # Confirma la tendencia intermedia
DAILY_TIMEFRAME = "1d"         # Tendencia mayor — filtro direccional

# Score mínimo para considerar una señal válida
MIN_SCORE = int(os.getenv("MIN_SCORE", "65"))
LEVERAGE_2X_SCORE = int(os.getenv("LEVERAGE_2X_SCORE", "80"))


@dataclass
class TradingSignal:
    """
    Señal de trading completa — el output final del analizador.
    Es lo que recibe Claude para tomar su decisión.
    """
    symbol: str
    trading_mode: str          # "futures" | "spot_tier1" | "spot_tier2" | "spot_tier3"
    direction: str             # "long" | "short" (spot solo genera "long")
    score: float               # Score de confianza 0-100
    current_price: float
    suggested_sl: float        # Stop-loss dinámico
    suggested_tp: float        # Take-profit (ratio 1:2 mínimo)
    risk_pct: float            # % de riesgo
    leverage: str              # "1x" | "2x"
    reasoning: str             # Explicación para Claude
    indicators_1h: TechnicalIndicators
    indicators_4h: Optional[TechnicalIndicators]
    indicators_1d: Optional[TechnicalIndicators]
    levels: SupportResistanceResult

    @property
    def is_autonomous(self) -> bool:
        """
        True si esta operación puede ejecutarse sin VoBo del operador.
        Depende del monto calculado vs VOBO_MIN_PCT — se evalúa en el executor.
        """
        return True  # El executor decide basado en el monto real

    @property
    def summary(self) -> str:
        return (
            f"{self.symbol} {self.direction.upper()} | "
            f"Score: {self.score:.0f}/100 | "
            f"Precio: ${self.current_price:,.4f} | "
            f"SL: ${self.suggested_sl:,.4f} | "
            f"TP: ${self.suggested_tp:,.4f} | "
            f"Riesgo: {self.risk_pct:.1f}% | "
            f"Apalancamiento: {self.leverage}"
        )


@dataclass
class AnalysisResult:
    """
    Resultado completo del ciclo de análisis.
    Contiene todas las señales detectadas ordenadas por score.
    """
    signals: list[TradingSignal]      # Señales válidas (score >= MIN_SCORE)
    analyzed_symbols: int             # Cuántos activos se analizaron
    skipped_symbols: list[str]        # Activos sin datos suficientes
    best_signal: Optional[TradingSignal]  # La señal con mayor score

    @property
    def has_signals(self) -> bool:
        return len(self.signals) > 0

    def summary(self) -> str:
        if not self.has_signals:
            return f"Sin señales válidas en este ciclo | Analizados: {self.analyzed_symbols}"
        return (
            f"{len(self.signals)} señal(es) detectada(s) | "
            f"Mejor: {self.best_signal.summary if self.best_signal else 'N/A'}"
        )


class TechnicalAnalyzer:
    """
    Motor de análisis técnico completo.

    Toma el CollectedSnapshot del colector y produce señales
    de trading concretas listas para Claude.

    Uso:
        analyzer = TechnicalAnalyzer()
        result = analyzer.analyze(snapshot)
        for signal in result.signals:
            print(signal.summary)
    """

    def __init__(self):
        self.indicator_calc = TechnicalIndicatorCalculator()
        self.level_detector = SupportResistanceDetector()
        self.scorer = SignalScorer()

        # Activos protegidos en HOLD — nunca generar señales para estos
        hold_symbols_env = os.getenv("HOLD_SYMBOLS", "")
        self.hold_symbols = [
            s.strip() for s in hold_symbols_env.split(",")
            if s.strip()
        ]

        logger.info(
            f"TechnicalAnalyzer inicializado | "
            f"Score mínimo: {MIN_SCORE} | "
            f"Score 2x: {LEVERAGE_2X_SCORE} | "
            f"HOLD: {self.hold_symbols}"
        )

    def get_trading_mode(self, symbol: str) -> str:
        """Determina el modo de trading para cada activo."""
        from collector.models import FUTURES_SYMBOLS, SPOT_TIER1, SPOT_TIER2, SPOT_TIER3
        if symbol in FUTURES_SYMBOLS:
            return "futures"
        elif symbol in SPOT_TIER1:
            return "spot_tier1"
        elif symbol in SPOT_TIER2:
            return "spot_tier2"
        else:
            return "spot_tier3"

    def calculate_context_bonus(
        self,
        snapshot: CollectedSnapshot,
        direction: str
    ) -> float:
        """
        Calcula puntos bonus basados en el contexto macro.
        Máximo +10 puntos adicionales al score base.
        """
        bonus = 0.0
        fg = snapshot.market_context.fear_greed_index

        if direction == "long":
            # Fear & Greed muy bajo → mercado con miedo → oportunidad de compra contrarian
            if fg <= 20:
                bonus += 5.0
            elif fg <= 35:
                bonus += 2.0
            # BTC dominance alta → el mercado está en modo defensivo → altcoins caen más
            if snapshot.market_context.btc_dominance > 60:
                bonus += 2.0

        elif direction == "short":
            # Fear & Greed muy alto → mercado codicioso → posible corrección
            if fg >= 80:
                bonus += 5.0
            elif fg >= 65:
                bonus += 2.0

        # Whale alerts recientes pueden añadir o restar
        for alert in snapshot.whale_alerts:
            if alert.is_bearish_signal and direction == "short":
                bonus += 3.0
            elif alert.is_bullish_signal and direction == "long":
                bonus += 3.0

        return min(bonus, 10.0)

    def analyze_symbol(
        self,
        symbol: str,
        snapshot: CollectedSnapshot
    ) -> Optional[TradingSignal]:
        """
        Analiza un activo específico y retorna una señal si el score es suficiente.
        Retorna None si no hay señal válida.
        """
        # Verificar que hay datos disponibles
        if symbol not in snapshot.candles:
            return None

        candles_by_tf = snapshot.candles[symbol]

        # Necesitamos las velas del timeframe principal
        if PRIMARY_TIMEFRAME not in candles_by_tf:
            return None

        candles_1h = candles_by_tf[PRIMARY_TIMEFRAME]
        candles_2h = candles_by_tf.get(CONFIRMATION_TIMEFRAME, [])
        candles_1d = candles_by_tf.get(DAILY_TIMEFRAME, [])

        # Calcular indicadores en timeframe principal
        indicators_1h = self.indicator_calc.calculate(symbol, PRIMARY_TIMEFRAME, candles_1h)
        if not indicators_1h:
            return None

        # Calcular indicadores en timeframe de confirmación (opcional)
        indicators_4h = None
        if candles_2h:
            indicators_4h = self.indicator_calc.calculate(symbol, CONFIRMATION_TIMEFRAME, candles_2h)

        # Calcular indicadores diarios — tendencia mayor
        indicators_1d = None
        if candles_1d and len(candles_1d) >= 50:
            indicators_1d = self.indicator_calc.calculate(symbol, DAILY_TIMEFRAME, candles_1d)

        # Detectar soportes y resistencias
        levels = self.level_detector.detect(symbol, candles_1h)

        # Calcular bonus de contexto macro
        direction = indicators_1h.suggested_direction
        if direction == "neutral":
            return None

        # Spot solo puede ser LONG
        trading_mode = self.get_trading_mode(symbol)
        if trading_mode != "futures" and direction == "short":
            return None

        context_bonus = self.calculate_context_bonus(snapshot, direction)

        # Calcular score final
        score = self.scorer.calculate(indicators_1h, levels, context_bonus)

        # Si el score no supera el mínimo, no hay señal
        if not score.is_tradeable:
            logger.info(
                f"{symbol} — Score insuficiente: {score.total:.0f}/{MIN_SCORE} "
            f"(EMA:{score.ema_trend_points:.0f} Vol:{score.volume_points:.0f} "
            f"MACD:{score.macd_points:.0f} RSI:{score.rsi_points:.0f} BB:{score.bollinger_points:.0f})"
            )
            return None

        # ── Modificador 1D — tendencia mayor ajusta score y tamaño ─────────
        # En vez de descartar, penaliza el score según la contradicción
        daily_penalty = 0.0
        daily_context = ""
        if indicators_1d:
            direction_1d = indicators_1d.suggested_direction
            trend_1d     = indicators_1d.trend

            if direction_1d != "neutral" and direction_1d != direction:
                # Contradicción mayor: reducir score significativamente
                if "strong" in trend_1d:
                    daily_penalty = 25.0  # strong_downtrend contra LONG → -25 pts
                    daily_context = f"strong_downtrend diario"
                else:
                    daily_penalty = 15.0  # downtrend moderado → -15 pts
                    daily_context = f"downtrend diario"

                score_ajustado = score.total - daily_penalty
                if score_ajustado < MIN_SCORE:
                    logger.info(
                        f"{symbol} — Score ajustado por {daily_context}: "
                        f"{score.total:.0f} - {daily_penalty:.0f} = {score_ajustado:.0f} "
                        f"(mínimo {MIN_SCORE}) — señal descartada"
                    )
                    return None

                logger.info(
                    f"{symbol} — Penalización por {daily_context}: "
                    f"score {score.total:.0f} → {score_ajustado:.0f}"
                )
                # Reducir score para que Claude sepa que hay contradicción
                score.total = round(score_ajustado, 1)
            elif direction_1d == direction:
                # Alineación diaria — bonus +5 pts
                score.total = min(round(score.total + 5, 1), 100.0)
                logger.info(f"{symbol} — Bonus por alineación diaria: score → {score.total:.0f}")
        # ──────────────────────────────────────────────────────────────────

        # Confirmación con timeframe 2h
        if indicators_4h:
            direction_4h = indicators_4h.suggested_direction
            if direction_4h != "neutral" and direction_4h != direction:
                logger.info(
                    f"{symbol} — Contradicción entre 1h ({direction}) "
                    f"y 2h ({direction_4h}) — señal descartada"
                )
                return None

        # Determinar apalancamiento
        leverage = score.leverage_recommended if trading_mode == "futures" else "1x"

        signal = TradingSignal(
            symbol=symbol,
            trading_mode=trading_mode,
            direction=direction,
            score=score.total,
            current_price=indicators_1h.current_price,
            suggested_sl=score.suggested_sl,
            suggested_tp=score.suggested_tp,
            risk_pct=score.risk_pct,
            leverage=leverage,
            reasoning=score.reasoning,
            indicators_1h=indicators_1h,
            indicators_4h=indicators_4h,
            indicators_1d=indicators_1d,
            levels=levels,
        )

        logger.info(f"Señal detectada: {signal.summary}")
        return signal

    def analyze(self, snapshot: CollectedSnapshot) -> AnalysisResult:
        """
        Analiza todos los activos del portafolio y retorna las señales válidas.
        """
        if snapshot.has_critical_gaps:
            logger.warning("Snapshot con gaps críticos — análisis cancelado")
            return AnalysisResult(
                signals=[],
                analyzed_symbols=0,
                skipped_symbols=list(snapshot.candles.keys()),
                best_signal=None
            )

        signals = []
        skipped = []
        analyzed = 0

        for symbol in snapshot.available_symbols:
            # No analizar activos en HOLD
            if symbol in self.hold_symbols:
                logger.debug(f"{symbol} en HOLD — saltando análisis")
                continue

            analyzed += 1

            signal = self.analyze_symbol(symbol, snapshot)
            if signal:
                signals.append(signal)
            else:
                skipped.append(symbol)

        # Ordenar señales por score descendente
        signals.sort(key=lambda s: s.score, reverse=True)

        result = AnalysisResult(
            signals=signals,
            analyzed_symbols=analyzed,
            skipped_symbols=skipped,
            best_signal=signals[0] if signals else None
        )

        logger.info(result.summary())
        return result
