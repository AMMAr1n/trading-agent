"""
analyzer.py — Orquestador del motor de análisis técnico
v0.7.1 — MTFAligner integrado en analyze_symbol.
Los chart patterns ahora se detectan ANTES de calcular el score,
así los patrones suman al score desde el principio.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

from collector.models import CollectedSnapshot, ALL_SYMBOLS, FUTURES_SYMBOLS
from .indicators import TechnicalIndicatorCalculator, TechnicalIndicators
from .levels import SupportResistanceDetector, SupportResistanceResult
from .scorer import SignalScorer, ScoreBreakdown
from .mtf_alignment import MTFAligner, MTFAlignment

load_dotenv(override=False)
logger = logging.getLogger(__name__)

PRIMARY_TIMEFRAME = "1h"
CONFIRMATION_TIMEFRAME = "2h"
DAILY_TIMEFRAME = "1d"
WEEKLY_TIMEFRAME = "1w"

MIN_SCORE = int(os.getenv("MIN_SCORE", "65"))
LEVERAGE_2X_SCORE = int(os.getenv("LEVERAGE_2X_SCORE", "80"))


@dataclass
class TradingSignal:
    symbol: str
    trading_mode: str
    direction: str
    score: float
    current_price: float
    suggested_sl: float
    suggested_tp: float
    risk_pct: float
    leverage: str
    reasoning: str
    indicators_1h: TechnicalIndicators
    indicators_4h: Optional[TechnicalIndicators]
    indicators_1d: Optional[TechnicalIndicators]
    levels: SupportResistanceResult
    indicators_1w: Optional[TechnicalIndicators] = None
    mtf_alignment: Optional[MTFAlignment] = None

    @property
    def is_autonomous(self) -> bool:
        return True

    @property
    def summary(self) -> str:
        pattern_info = ""
        if self.mtf_alignment and self.mtf_alignment.best_pattern:
            bp = self.mtf_alignment.best_pattern
            pattern_info = f" | Pattern: {bp.pattern_type}({bp.timeframe})"
        return (
            f"{self.symbol} {self.direction.upper()} | "
            f"Score: {self.score:.0f}/100 | "
            f"Precio: ${self.current_price:,.4f} | "
            f"SL: ${self.suggested_sl:,.4f} | "
            f"TP: ${self.suggested_tp:,.4f} | "
            f"Riesgo: {self.risk_pct:.1f}% | "
            f"Apalancamiento: {self.leverage}"
            f"{pattern_info}"
        )


@dataclass
class AnalysisResult:
    signals: list[TradingSignal]
    analyzed_symbols: int
    skipped_symbols: list[str]
    best_signal: Optional[TradingSignal]

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

    def __init__(self):
        self.indicator_calc = TechnicalIndicatorCalculator()
        self.level_detector = SupportResistanceDetector()
        self.scorer = SignalScorer()
        self.mtf_aligner = MTFAligner()

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
        from collector.models import FUTURES_SYMBOLS, SPOT_TIER1, SPOT_TIER2, SPOT_TIER3
        if symbol in FUTURES_SYMBOLS:
            return "futures"
        elif symbol in SPOT_TIER1:
            return "spot_tier1"
        elif symbol in SPOT_TIER2:
            return "spot_tier2"
        else:
            return "spot_tier3"

    def calculate_context_bonus(self, snapshot, direction):
        bonus = 0.0
        fg = snapshot.market_context.fear_greed_index
        if direction == "long":
            if fg <= 20:
                bonus += 5.0
            elif fg <= 35:
                bonus += 2.0
            if snapshot.market_context.btc_dominance > 60:
                bonus += 2.0
        elif direction == "short":
            if fg >= 80:
                bonus += 5.0
            elif fg >= 65:
                bonus += 2.0
        for alert in snapshot.whale_alerts:
            if alert.is_bearish_signal and direction == "short":
                bonus += 3.0
            elif alert.is_bullish_signal and direction == "long":
                bonus += 3.0
        return min(bonus, 10.0)

    def _build_candles_dataframes(self, symbol: str, snapshot: CollectedSnapshot) -> dict:
        """
        Convierte las velas del snapshot en DataFrames para el MTFAligner.
        Retorna dict de {timeframe: pd.DataFrame}.
        """
        candles_by_tf = {}
        if symbol not in snapshot.candles:
            return candles_by_tf

        for tf, candle_list in snapshot.candles[symbol].items():
            if candle_list and len(candle_list) >= 20:
                try:
                    data = {
                        "open": [c.open for c in candle_list],
                        "high": [c.high for c in candle_list],
                        "low": [c.low for c in candle_list],
                        "close": [c.close for c in candle_list],
                        "volume": [c.volume for c in candle_list],
                    }
                    candles_by_tf[tf] = pd.DataFrame(data)
                except Exception as e:
                    logger.debug(f"Error convirtiendo velas {symbol}/{tf}: {e}")

        return candles_by_tf

    def analyze_symbol(self, symbol, snapshot):
        if symbol not in snapshot.candles:
            return None

        candles_by_tf_raw = snapshot.candles[symbol]

        if PRIMARY_TIMEFRAME not in candles_by_tf_raw:
            return None

        candles_1h = candles_by_tf_raw[PRIMARY_TIMEFRAME]
        candles_2h = candles_by_tf_raw.get(CONFIRMATION_TIMEFRAME, [])
        candles_1d = candles_by_tf_raw.get(DAILY_TIMEFRAME, [])
        candles_1w = candles_by_tf_raw.get(WEEKLY_TIMEFRAME, [])

        indicators_1h = self.indicator_calc.calculate(symbol, PRIMARY_TIMEFRAME, candles_1h)
        if not indicators_1h:
            return None

        indicators_4h = None
        if candles_2h:
            indicators_4h = self.indicator_calc.calculate(symbol, CONFIRMATION_TIMEFRAME, candles_2h)

        indicators_1d = None
        if candles_1d and len(candles_1d) >= 50:
            indicators_1d = self.indicator_calc.calculate(symbol, DAILY_TIMEFRAME, candles_1d)

        indicators_1w = None
        if candles_1w and len(candles_1w) >= 20:
            indicators_1w = self.indicator_calc.calculate(symbol, WEEKLY_TIMEFRAME, candles_1w)

        levels = self.level_detector.detect(symbol, candles_1h)

        direction = indicators_1h.suggested_direction
        if direction == "neutral":
            return None

        trading_mode = self.get_trading_mode(symbol)
        context_bonus = self.calculate_context_bonus(snapshot, direction)

        # ── v0.7.1: MTF Alignment ANTES del score ─────────────────────
        mtf_alignment = None
        try:
            candles_dfs = self._build_candles_dataframes(symbol, snapshot)
            if candles_dfs:
                mtf_alignment = self.mtf_aligner.analyze(candles_dfs, symbol)

                # Veto: si el TF mayor tiene un patron fuerte en direccion contraria
                if mtf_alignment.veto_reason and mtf_alignment.consensus_direction != "neutral":
                    if mtf_alignment.consensus_direction != direction:
                        logger.info(
                            f"{symbol} — VETO por MTF: {mtf_alignment.veto_reason} "
                            f"(agente quiere {direction}, MTF dice {mtf_alignment.consensus_direction})"
                        )
                        return None
        except Exception as e:
            logger.warning(f"MTF alignment error para {symbol}: {e}")
        # ──────────────────────────────────────────────────────────────

        # Score con MTF alignment incluido
        score = self.scorer.calculate(indicators_1h, levels, context_bonus,
                                      mtf_alignment=mtf_alignment)

        if not score.is_tradeable:
            logger.info(
                f"{symbol} — Score insuficiente: {score.total:.0f}/{MIN_SCORE} "
                f"(EMA:{score.ema_trend_points:.0f} Vol:{score.volume_points:.0f} "
                f"MACD:{score.macd_points:.0f} RSI:{score.rsi_points:.0f} "
                f"BB:{score.bollinger_points:.0f} "
                f"Pat:{score.chart_pattern_points:.0f} BO:{score.breakout_points:.0f})"
            )
            return None

        # ── Modificador 1D — tendencia mayor ───────────────────────────
        if indicators_1d:
            direction_1d = indicators_1d.suggested_direction
            trend_1d = indicators_1d.trend
            if direction_1d != "neutral" and direction_1d != direction:
                daily_penalty = 25.0 if "strong" in trend_1d else 15.0
                score_ajustado = score.total - daily_penalty
                if score_ajustado < MIN_SCORE:
                    logger.info(
                        f"{symbol} — Score ajustado por tendencia diaria: "
                        f"{score.total:.0f} - {daily_penalty:.0f} = {score_ajustado:.0f} — descartada"
                    )
                    return None
                score.total = round(score_ajustado, 1)
            elif direction_1d == direction:
                score.total = min(round(score.total + 5, 1), 100.0)

        # ── Modificador 1W — tendencia macro ───────────────────────────
        if indicators_1w:
            direction_1w = indicators_1w.suggested_direction
            trend_1w = indicators_1w.trend
            if direction_1w != "neutral" and direction_1w != direction:
                weekly_penalty = 15.0 if "strong" in trend_1w else 10.0
                score_1w = score.total - weekly_penalty
                if score_1w < MIN_SCORE:
                    logger.info(
                        f"{symbol} — Score ajustado por tendencia semanal: "
                        f"{score.total:.0f} - {weekly_penalty:.0f} = {score_1w:.0f} — descartada"
                    )
                    return None
                score.total = round(score_1w, 1)
            elif direction_1w == direction:
                score.total = min(round(score.total + 8, 1), 100.0)

        # ── Confirmacion 2h ────────────────────────────────────────────
        if indicators_4h:
            direction_4h = indicators_4h.suggested_direction
            if direction_4h != "neutral" and direction_4h != direction:
                logger.info(
                    f"{symbol} — Contradiccion 1h/2h ({direction} vs {direction_4h}) — descartada"
                )
                return None

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
            indicators_1w=indicators_1w,
            levels=levels,
            mtf_alignment=mtf_alignment,
        )

        logger.info(f"Senal detectada: {signal.summary}")
        return signal

    def analyze(self, snapshot):
        if snapshot.has_critical_gaps:
            logger.warning("Snapshot con gaps criticos — analisis cancelado")
            return AnalysisResult(
                signals=[], analyzed_symbols=0,
                skipped_symbols=list(snapshot.candles.keys()),
                best_signal=None
            )

        signals = []
        skipped = []
        analyzed = 0

        for symbol in snapshot.available_symbols:
            if symbol in self.hold_symbols:
                continue
            analyzed += 1
            signal = self.analyze_symbol(symbol, snapshot)
            if signal:
                signals.append(signal)
            else:
                skipped.append(symbol)

        signals.sort(key=lambda s: s.score, reverse=True)

        result = AnalysisResult(
            signals=signals,
            analyzed_symbols=analyzed,
            skipped_symbols=skipped,
            best_signal=signals[0] if signals else None
        )

        logger.info(result.summary())
        return result
