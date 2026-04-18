"""
indicators.py — Calculador de indicadores técnicos
Responsabilidad: tomar las velas del colector y calcular
RSI, MACD, Bollinger Bands, volumen y señales institucionales.

Usa la librería 'ta' que es compatible con Python 3.14+
"""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
import ta

try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False


from collector.models import CandleData

logger = logging.getLogger(__name__)


# ─── ESTRUCTURAS DE RESULTADO ─────────────────────────────────────────────────

@dataclass
class RSIResult:
    """Resultado del cálculo de RSI."""
    value: float           # Valor actual 0-100
    prev_value: float      # Valor anterior (para detectar cambios)
    signal: str            # "oversold" | "overbought" | "neutral"
    strength: float        # Qué tan extremo es (0-1)

    @property
    def is_oversold(self) -> bool:
        return self.value < 30

    @property
    def is_overbought(self) -> bool:
        return self.value > 70

    @property
    def is_recovering(self) -> bool:
        """RSI estaba sobrevendido y está subiendo — señal alcista fuerte."""
        return self.prev_value < 30 and self.value > self.prev_value

    @property
    def is_reversing(self) -> bool:
        """RSI estaba sobrecomprado y está bajando — señal bajista fuerte."""
        return self.prev_value > 70 and self.value < self.prev_value


@dataclass
class MACDResult:
    """Resultado del cálculo de MACD."""
    macd_line: float       # Línea MACD
    signal_line: float     # Línea Signal
    histogram: float       # Diferencia MACD - Signal
    prev_histogram: float  # Histograma anterior
    signal: str            # "bullish_cross" | "bearish_cross" | "bullish" | "bearish" | "neutral"

    @property
    def is_bullish_cross(self) -> bool:
        """MACD acaba de cruzar por encima de Signal — señal alcista."""
        return self.prev_histogram < 0 and self.histogram > 0

    @property
    def is_bearish_cross(self) -> bool:
        """MACD acaba de cruzar por debajo de Signal — señal bajista."""
        return self.prev_histogram > 0 and self.histogram < 0

    @property
    def is_bullish(self) -> bool:
        return self.histogram > 0

    @property
    def is_bearish(self) -> bool:
        return self.histogram < 0


@dataclass
class BollingerResult:
    """Resultado del cálculo de Bandas de Bollinger."""
    upper: float           # Banda superior
    middle: float          # Banda media (SMA 20)
    lower: float           # Banda inferior
    current_price: float   # Precio actual
    bandwidth: float       # Ancho de las bandas (volatilidad)
    percent_b: float       # Posición del precio dentro de las bandas (0-1)
    signal: str            # "at_lower" | "at_upper" | "squeeze" | "neutral"

    @property
    def is_at_lower_band(self) -> bool:
        """Precio cerca de la banda inferior — posible rebote alcista."""
        return self.percent_b < 0.05

    @property
    def is_at_upper_band(self) -> bool:
        """Precio cerca de la banda superior — posible corrección bajista."""
        return self.percent_b > 0.95

    @property
    def is_squeeze(self) -> bool:
        """Bandas muy juntas — movimiento explosivo próximo."""
        return self.bandwidth < 0.02


@dataclass
class VolumeResult:
    """Resultado del análisis de volumen."""
    current: float         # Volumen actual
    average_20: float      # Promedio de las últimas 20 velas
    ratio: float           # Ratio actual/promedio (1.5 = 50% arriba del promedio)
    signal: str            # "very_high" | "high" | "normal" | "low"
    is_institutional: bool # True si hay señal de actividad institucional (3x+)

    @property
    def is_confirming(self) -> bool:
        """Volumen suficiente para confirmar una señal (mínimo 20% arriba del promedio)."""
        return self.ratio >= 1.2

    @property
    def is_high(self) -> bool:
        return self.ratio >= 1.5

    @property
    def is_very_high(self) -> bool:
        return self.ratio >= 2.0


@dataclass
class TechnicalIndicators:
    """
    Conjunto completo de indicadores técnicos para un activo y timeframe.
    Este es el objeto que recibe el scorer para calcular el score de confianza.
    """
    symbol: str
    timeframe: str
    current_price: float
    rsi: RSIResult
    macd: MACDResult
    bollinger: BollingerResult
    volume: VolumeResult
    ema_20: float          # Media móvil exponencial 20 períodos
    ema_50: float          # Media móvil exponencial 50 períodos
    ema_200: float         # Media móvil exponencial 200 períodos (tendencia mayor)
    atr_14: float = 0.0    # Average True Range 14 períodos — volatilidad real del activo
    candlestick_patterns: list = None  # Patrones de velas detectados por ta-lib

    @property
    def trend(self) -> str:
        """
        Tendencia general basada en EMAs.
        Si el precio está sobre EMA200 → uptrend
        Si el precio está bajo EMA200 → downtrend
        """
        if self.current_price > self.ema_200:
            if self.ema_20 > self.ema_50:
                return "strong_uptrend"
            return "uptrend"
        else:
            if self.ema_20 < self.ema_50:
                return "strong_downtrend"
            return "downtrend"

    @property
    def suggested_direction(self) -> str:
        """
        Dirección sugerida basada en la combinación de indicadores.
        El scorer usa esto como input principal.
        """
        bullish_signals = 0
        bearish_signals = 0

        # RSI
        if self.rsi.is_oversold or self.rsi.is_recovering:
            bullish_signals += 1
        elif self.rsi.is_overbought or self.rsi.is_reversing:
            bearish_signals += 1

        # MACD
        if self.macd.is_bullish_cross or self.macd.is_bullish:
            bullish_signals += 1
        elif self.macd.is_bearish_cross or self.macd.is_bearish:
            bearish_signals += 1

        # Bollinger
        if self.bollinger.is_at_lower_band:
            bullish_signals += 1
        elif self.bollinger.is_at_upper_band:
            bearish_signals += 1

        # Tendencia
        if "uptrend" in self.trend:
            bullish_signals += 1
        elif "downtrend" in self.trend:
            bearish_signals += 1

        if bullish_signals >= 2:
            return "long"
        elif bearish_signals >= 2:
            return "short"
        return "neutral"


# ─── CALCULADOR PRINCIPAL ─────────────────────────────────────────────────────

class TechnicalIndicatorCalculator:
    """
    Calcula todos los indicadores técnicos a partir de las velas del colector.

    Uso:
        calculator = TechnicalIndicatorCalculator()
        indicators = calculator.calculate("BTCUSDT", "1h", candles)
    """

    def candles_to_dataframe(self, candles: list[CandleData]) -> pd.DataFrame:
        """
        Convierte la lista de CandleData en un DataFrame de pandas.
        La librería 'ta' trabaja con DataFrames.
        """
        data = {
            "timestamp": [c.timestamp for c in candles],
            "open":      [c.open for c in candles],
            "high":      [c.high for c in candles],
            "low":       [c.low for c in candles],
            "close":     [c.close for c in candles],
            "volume":    [c.volume for c in candles],
        }
        df = pd.DataFrame(data)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> Optional[RSIResult]:
        """Calcula el RSI de las últimas N velas."""
        try:
            rsi_series = ta.momentum.RSIIndicator(
                close=df["close"],
                window=period
            ).rsi()

            current = float(rsi_series.iloc[-1])
            previous = float(rsi_series.iloc[-2])

            if current < 30:
                signal = "oversold"
                strength = (30 - current) / 30
            elif current > 70:
                signal = "overbought"
                strength = (current - 70) / 30
            else:
                signal = "neutral"
                strength = 0.0

            return RSIResult(
                value=current,
                prev_value=previous,
                signal=signal,
                strength=min(strength, 1.0)
            )

        except Exception as e:
            logger.error(f"Error calculando RSI: {e}")
            return None

    def calculate_macd(
        self,
        df: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9
    ) -> Optional[MACDResult]:
        """Calcula el MACD y detecta cruces de señal."""
        try:
            macd_indicator = ta.trend.MACD(
                close=df["close"],
                window_fast=fast,
                window_slow=slow,
                window_sign=signal
            )

            macd_line = float(macd_indicator.macd().iloc[-1])
            signal_line = float(macd_indicator.macd_signal().iloc[-1])
            histogram = float(macd_indicator.macd_diff().iloc[-1])
            prev_histogram = float(macd_indicator.macd_diff().iloc[-2])

            # Determinar señal
            if prev_histogram < 0 and histogram > 0:
                sig = "bullish_cross"
            elif prev_histogram > 0 and histogram < 0:
                sig = "bearish_cross"
            elif histogram > 0:
                sig = "bullish"
            elif histogram < 0:
                sig = "bearish"
            else:
                sig = "neutral"

            return MACDResult(
                macd_line=macd_line,
                signal_line=signal_line,
                histogram=histogram,
                prev_histogram=prev_histogram,
                signal=sig
            )

        except Exception as e:
            logger.error(f"Error calculando MACD: {e}")
            return None

    def calculate_bollinger(
        self,
        df: pd.DataFrame,
        period: int = 20,
        std: float = 2.0
    ) -> Optional[BollingerResult]:
        """Calcula las Bandas de Bollinger."""
        try:
            bb = ta.volatility.BollingerBands(
                close=df["close"],
                window=period,
                window_dev=std
            )

            upper = float(bb.bollinger_hband().iloc[-1])
            middle = float(bb.bollinger_mavg().iloc[-1])
            lower = float(bb.bollinger_lband().iloc[-1])
            current_price = float(df["close"].iloc[-1])

            # Posición del precio dentro de las bandas (0=banda inferior, 1=banda superior)
            band_range = upper - lower
            percent_b = (current_price - lower) / band_range if band_range > 0 else 0.5

            # Ancho de las bandas normalizado (para detectar squeeze)
            bandwidth = band_range / middle if middle > 0 else 0

            if percent_b < 0.05:
                signal = "at_lower"
            elif percent_b > 0.95:
                signal = "at_upper"
            elif bandwidth < 0.02:
                signal = "squeeze"
            else:
                signal = "neutral"

            return BollingerResult(
                upper=upper,
                middle=middle,
                lower=lower,
                current_price=current_price,
                bandwidth=bandwidth,
                percent_b=percent_b,
                signal=signal
            )

        except Exception as e:
            logger.error(f"Error calculando Bollinger Bands: {e}")
            return None

    def calculate_volume(self, df: pd.DataFrame, period: int = 20) -> Optional[VolumeResult]:
        """Analiza el volumen y detecta actividad institucional."""
        try:
            current_volume = float(df["volume"].iloc[-1])
            avg_volume = float(df["volume"].tail(period).mean())
            ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

            if ratio >= 3.0:
                signal = "very_high"
                is_institutional = True
            elif ratio >= 2.0:
                signal = "high"
                is_institutional = False
            elif ratio >= 0.8:
                signal = "normal"
                is_institutional = False
            else:
                signal = "low"
                is_institutional = False

            return VolumeResult(
                current=current_volume,
                average_20=avg_volume,
                ratio=ratio,
                signal=signal,
                is_institutional=is_institutional
            )

        except Exception as e:
            logger.error(f"Error calculando volumen: {e}")
            return None

    def calculate_emas(self, df: pd.DataFrame) -> tuple[float, float, float]:
        """Calcula las EMAs de 20, 50 y 200 períodos."""
        try:
            ema_20 = float(
                ta.trend.EMAIndicator(close=df["close"], window=20).ema_indicator().iloc[-1]
            )
            ema_50 = float(
                ta.trend.EMAIndicator(close=df["close"], window=50).ema_indicator().iloc[-1]
            )
            ema_200 = float(
                ta.trend.EMAIndicator(close=df["close"], window=200).ema_indicator().iloc[-1]
            )
            return ema_20, ema_50, ema_200

        except Exception as e:
            logger.error(f"Error calculando EMAs: {e}")
            return 0.0, 0.0, 0.0

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calcula el ATR (Average True Range) — volatilidad real del activo."""
        try:
            atr = ta.volatility.AverageTrueRange(
                high=df["high"],
                low=df["low"],
                close=df["close"],
                window=period
            ).average_true_range()
            return float(atr.iloc[-1])
        except Exception as e:
            logger.error(f"Error calculando ATR: {e}")
            return 0.0

    def detect_candlestick_patterns(self, df: pd.DataFrame) -> list[str]:
        """
        Detecta patrones de velas usando TA-Lib (+50 patrones) si está disponible,
        con fallback a detección manual con numpy si no lo está.
        """
        patterns = []

        if TALIB_AVAILABLE:
            try:
                import numpy as np
                o = df["open"].values.astype(float)
                h = df["high"].values.astype(float)
                l = df["low"].values.astype(float)
                c = df["close"].values.astype(float)

                # Diccionario: función talib → nombre legible → tipo (bullish/bearish/neutral)
                TALIB_PATTERNS = {
                    "CDL2CROWS":         ("Two Crows",             "bearish"),
                    "CDL3BLACKCROWS":    ("Three Black Crows",     "bearish"),
                    "CDL3INSIDE":        ("Three Inside Up/Down",  "neutral"),
                    "CDL3LINESTRIKE":    ("Three-Line Strike",     "neutral"),
                    "CDL3OUTSIDE":       ("Three Outside Up/Down", "neutral"),
                    "CDL3WHITESOLDIERS": ("Three White Soldiers",  "bullish"),
                    "CDLABANDONEDBABY":  ("Abandoned Baby",        "neutral"),
                    "CDLADVANCEBLOCK":   ("Advance Block",         "bearish"),
                    "CDLDOJI":           ("Doji",                  "neutral"),
                    "CDLDOJISTAR":       ("Doji Star",             "neutral"),
                    "CDLDRAGONFLYDOJI":  ("Dragonfly Doji",        "bullish"),
                    "CDLENGULFING":      ("Engulfing",             "neutral"),
                    "CDLEVENINGDOJISTAR":("Evening Doji Star",     "bearish"),
                    "CDLEVENINGSTAR":    ("Evening Star",          "bearish"),
                    "CDLGRAVESTONEDOJI": ("Gravestone Doji",       "bearish"),
                    "CDLHAMMER":         ("Hammer",                "bullish"),
                    "CDLHANGINGMAN":     ("Hanging Man",           "bearish"),
                    "CDLHARAMI":         ("Harami",                "neutral"),
                    "CDLHARAMICROSS":    ("Harami Cross",          "neutral"),
                    "CDLHIGHWAVE":       ("High-Wave Candle",      "neutral"),
                    "CDLHIKKAKE":        ("Hikkake Pattern",       "neutral"),
                    "CDLINVERTEDHAMMER": ("Inverted Hammer",       "bullish"),
                    "CDLKICKING":        ("Kicking",               "neutral"),
                    "CDLLONGLEGGEDDOJI": ("Long Legged Doji",      "neutral"),
                    "CDLLONGLINE":       ("Long Line Candle",      "neutral"),
                    "CDLMARUBOZU":       ("Marubozu",              "neutral"),
                    "CDLMATCHINGLOW":    ("Matching Low",          "bullish"),
                    "CDLMORNINGDOJISTAR":("Morning Doji Star",     "bullish"),
                    "CDLMORNINGSTAR":    ("Morning Star",          "bullish"),
                    "CDLONNECK":         ("On-Neck Pattern",       "bearish"),
                    "CDLPIERCING":       ("Piercing Pattern",      "bullish"),
                    "CDLRICKSHAWMAN":    ("Rickshaw Man",          "neutral"),
                    "CDLRISEFALL3METHODS":("Rise/Fall Three Methods","neutral"),
                    "CDLSEPARATINGLINES":("Separating Lines",      "neutral"),
                    "CDLSHOOTINGSTAR":   ("Shooting Star",         "bearish"),
                    "CDLSHORTLINE":      ("Short Line Candle",     "neutral"),
                    "CDLSPINNINGTOP":    ("Spinning Top",          "neutral"),
                    "CDLSTALLEDPATTERN": ("Stalled Pattern",       "bearish"),
                    "CDLSTICKSANDWICH":  ("Stick Sandwich",        "bullish"),
                    "CDLTAKURI":         ("Takuri",                "bullish"),
                    "CDLTASUKIGAP":      ("Tasuki Gap",            "neutral"),
                    "CDLTHRUSTING":      ("Thrusting Pattern",     "bearish"),
                    "CDLTRISTAR":        ("Tristar Pattern",       "neutral"),
                    "CDLUNIQUE3RIVER":   ("Unique 3 River",        "bullish"),
                    "CDLUPSIDEGAP2CROWS":("Upside Gap Two Crows",  "bearish"),
                    "CDLXSIDEGAP3METHODS":("Upside/Downside Gap Three Methods","neutral"),
                }

                for func_name, (label, bias) in TALIB_PATTERNS.items():
                    try:
                        func = getattr(talib, func_name)
                        result = func(o, h, l, c)
                        val = int(result[-1])
                        if val > 0:
                            patterns.append(f"{label} ({bias} bullish)")
                        elif val < 0:
                            patterns.append(f"{label} ({bias} bearish)")
                    except Exception:
                        continue

                logger.debug(f"TA-Lib detectó {len(patterns)} patrones de velas")
                return patterns[:8]  # máximo 8 patrones para no saturar el prompt

            except Exception as e:
                logger.warning(f"Error en ta-lib patterns: {e} — usando fallback")

        # ── Fallback: detección manual con numpy ──────────────────────────
        if len(df) < 3:
            return patterns

        c = df["close"].iloc[-1]
        o = df["open"].iloc[-1]
        h = df["high"].iloc[-1]
        l = df["low"].iloc[-1]
        c1 = df["close"].iloc[-2]
        o1 = df["open"].iloc[-2]
        c2 = df["close"].iloc[-3]

        body = abs(c - o)
        body1 = abs(c1 - o1)
        range_ = h - l
        upper_shadow = h - max(c, o)
        lower_shadow = min(c, o) - l

        if range_ > 0 and body / range_ < 0.1:
            patterns.append("Doji (neutral)")
        if range_ > 0 and body / range_ < 0.35 and lower_shadow >= body * 2 and upper_shadow <= body * 0.5 and c > o:
            patterns.append("Hammer (bullish)")
        if range_ > 0 and body / range_ < 0.35 and upper_shadow >= body * 2 and lower_shadow <= body * 0.5 and c < o:
            patterns.append("Shooting Star (bearish)")
        if c > o and range_ > 0 and body / range_ > 0.9:
            patterns.append("Marubozu alcista (bullish)")
        if c < o and range_ > 0 and body / range_ > 0.9:
            patterns.append("Marubozu bajista (bearish)")
        if c > o and c1 < o1 and c > o1 and o < c1 and body > body1 * 1.2:
            patterns.append("Engulfing alcista (bullish)")
        if c < o and c1 > o1 and c < o1 and o > c1 and body > body1 * 1.2:
            patterns.append("Engulfing bajista (bearish)")
        if (c2 < df["open"].iloc[-3]
                and body1 / (df["high"].iloc[-2] - df["low"].iloc[-2] + 0.0001) < 0.3
                and c > o and c > (df["close"].iloc[-3] + df["open"].iloc[-3]) / 2):
            patterns.append("Morning Star (bullish)")
        if (c2 > df["open"].iloc[-3]
                and body1 / (df["high"].iloc[-2] - df["low"].iloc[-2] + 0.0001) < 0.3
                and c < o and c < (df["close"].iloc[-3] + df["open"].iloc[-3]) / 2):
            patterns.append("Evening Star (bearish)")

        return patterns

    def calculate(
        self,
        symbol: str,
        timeframe: str,
        candles: list[CandleData]
    ) -> Optional[TechnicalIndicators]:
        """
        Calcula todos los indicadores técnicos para un activo y timeframe.

        Retorna None si no hay suficientes velas o si ocurre un error.
        """
        # Necesitamos mínimo 200 velas para EMA200 y MACD confiable
        if len(candles) < 50:
            logger.warning(
                f"Pocas velas para {symbol}/{timeframe}: "
                f"{len(candles)} — mínimo 50 requeridas"
            )
            return None

        try:
            df = self.candles_to_dataframe(candles)
            current_price = float(df["close"].iloc[-1])

            rsi = self.calculate_rsi(df)
            macd = self.calculate_macd(df)
            bollinger = self.calculate_bollinger(df)
            volume = self.calculate_volume(df)
            ema_20, ema_50, ema_200 = self.calculate_emas(df)
            atr_14 = self.calculate_atr(df)
            candlestick_patterns = self.detect_candlestick_patterns(df)

            if not all([rsi, macd, bollinger, volume]):
                missing = []
                if not rsi: missing.append("RSI")
                if not macd: missing.append("MACD")
                if not bollinger: missing.append("Bollinger")
                if not volume: missing.append("Volume")
                logger.warning(f"Indicadores incompletos para {symbol}/{timeframe}: falta {', '.join(missing)}")
                return None

            indicators = TechnicalIndicators(
                symbol=symbol,
                timeframe=timeframe,
                current_price=current_price,
                rsi=rsi,
                macd=macd,
                bollinger=bollinger,
                volume=volume,
                ema_20=ema_20,
                ema_50=ema_50,
                ema_200=ema_200,
                atr_14=atr_14,
                candlestick_patterns=candlestick_patterns,
            )

            logger.debug(
                f"{symbol}/{timeframe} — "
                f"RSI: {rsi.value:.1f} ({rsi.signal}) | "
                f"MACD: {macd.signal} | "
                f"BB: {bollinger.signal} | "
                f"Vol: {volume.ratio:.1f}x | "
                f"Trend: {indicators.trend} | "
                f"Direction: {indicators.suggested_direction}"
            )

            return indicators

        except Exception as e:
            logger.error(f"Error calculando indicadores para {symbol}/{timeframe}: {e}")
            return None
