"""
models.py — Estructuras de datos del colector
Todos los datos que recopila el agente tienen una forma definida aqui.
Pydantic garantiza que los tipos sean correctos antes de que lleguen al analizador.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator


# ─── ACTIVOS DEL PORTAFOLIO ────────────────────────────────────────────────────

# Futuros perpetuos USD-M — todos los pares, LONG y SHORT
FUTURES_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT",
    "AVAXUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "TRUMPUSDT", "AAVEUSDT",
    "SUIUSDT",
]

# Spot — deshabilitado, todos operan como futuros perpetuos
SPOT_TIER1 = []
SPOT_TIER2 = []
SPOT_TIER3 = []

# Todos los activos que el colector debe monitorear
ALL_SYMBOLS = FUTURES_SYMBOLS + SPOT_TIER1 + SPOT_TIER2 + SPOT_TIER3

# Timeframes de velas que se recopilan por cada activo
CANDLE_TIMEFRAMES = ["1m", "15m", "1h", "2h", "4h", "1d", "1w"]

# Cuantas velas historicas pedir por timeframe
CANDLES_LIMIT = 200


# ─── MODELO: VELA (OHLCV) ─────────────────────────────────────────────────────

class CandleData(BaseModel):
    """
    Una vela japonesa — la unidad basica del analisis tecnico.
    OHLCV = Open, High, Low, Close, Volume
    """
    symbol: str                  # ej. "BTCUSDT"
    timeframe: str               # ej. "1h"
    timestamp: datetime          # Momento de apertura de la vela (UTC)
    open: float                  # Precio al abrir el periodo
    high: float                  # Precio maximo del periodo
    low: float                   # Precio minimo del periodo
    close: float                 # Precio al cerrar el periodo
    volume: float                # Volumen negociado en USDT

    @field_validator("symbol")
    @classmethod
    def symbol_must_be_known(cls, v):
        if v not in ALL_SYMBOLS:
            raise ValueError(f"Simbolo desconocido: {v}. Debe ser uno de {ALL_SYMBOLS}")
        return v

    @field_validator("timeframe")
    @classmethod
    def timeframe_must_be_valid(cls, v):
        if v not in CANDLE_TIMEFRAMES:
            raise ValueError(f"Timeframe invalido: {v}. Debe ser uno de {CANDLE_TIMEFRAMES}")
        return v

    @field_validator("open", "high", "low", "close")
    @classmethod
    def must_be_positive(cls, v):
        if v <= 0:
            raise ValueError(f"El precio debe ser positivo, se recibio: {v}")
        return v

    @field_validator("volume")
    @classmethod
    def volume_must_be_non_negative(cls, v):
        if v < 0:
            raise ValueError(f"El volumen no puede ser negativo, se recibio: {v}")
        return v


# ─── MODELO: TICKER (PRECIO ACTUAL) ───────────────────────────────────────────

class TickerData(BaseModel):
    """
    Snapshot del precio actual de un activo en este momento.
    Se actualiza en cada ciclo del loop.
    """
    symbol: str                  # ej. "BTCUSDT"
    price: float                 # Precio actual en USDT
    change_24h_pct: float        # Cambio porcentual en las ultimas 24 horas
    volume_24h: float            # Volumen total en USDT en las ultimas 24 horas
    high_24h: float              # Maximo de las ultimas 24 horas
    low_24h: float               # Minimo de las ultimas 24 horas
    collected_at: datetime       # Cuando se recopilo este dato (UTC)

    @field_validator("price", "volume_24h", "high_24h", "low_24h")
    @classmethod
    def must_be_positive(cls, v):
        if v <= 0:
            raise ValueError(f"El valor debe ser positivo, se recibio: {v}")
        return v


# ─── MODELO: CONTEXTO MACRO (COINMARKETCAP) ───────────────────────────────────

class MarketContext(BaseModel):
    """
    Estado global del mercado de criptomonedas.
    Viene de CoinMarketCap y se actualiza cada 5 minutos.
    """
    btc_dominance: float         # % del mercado total que representa BTC (ej. 52.3)
    total_market_cap_usd: float  # Capitalizacion total del mercado en USD
    total_volume_24h_usd: float  # Volumen total del mercado en 24h en USD
    fear_greed_index: int        # Indice miedo/codicia: 0=panico extremo, 100=codicia extrema
    fear_greed_label: str        # "Extreme Fear" | "Fear" | "Neutral" | "Greed" | "Extreme Greed"
    active_cryptocurrencies: int # Numero de criptomonedas activas en el mercado
    collected_at: datetime       # Cuando se recopilo este dato (UTC)

    @field_validator("btc_dominance")
    @classmethod
    def dominance_must_be_percentage(cls, v):
        if not 0 <= v <= 100:
            raise ValueError(f"La dominancia debe estar entre 0 y 100, se recibio: {v}")
        return v

    @field_validator("fear_greed_index")
    @classmethod
    def index_must_be_valid(cls, v):
        if not 0 <= v <= 100:
            raise ValueError(f"El indice Fear & Greed debe estar entre 0 y 100, se recibio: {v}")
        return v

    @property
    def market_sentiment(self) -> str:
        """Resumen legible del sentimiento para incluir en el prompt de Claude."""
        if self.fear_greed_index <= 20:
            return "panico extremo — posible oportunidad de compra contraria"
        elif self.fear_greed_index <= 40:
            return "miedo — mercado cauteloso, operar con precaucion"
        elif self.fear_greed_index <= 60:
            return "neutral — sin sesgo claro, seguir la tecnica"
        elif self.fear_greed_index <= 80:
            return "codicia — mercado optimista, cuidado con sobreextension"
        else:
            return "codicia extrema — alto riesgo de correccion inminente"


# ─── MODELO: ALERTA DE BALLENA ────────────────────────────────────────────────

class WhaleAlert(BaseModel):
    """
    Transaccion grande detectada por Whale Alert.
    Indica movimientos institucionales o de grandes holders.
    """
    symbol: str                  # ej. "BTC"
    amount_usd: float            # Valor de la transaccion en USD
    transaction_type: str        # "transfer" | "exchange_deposit" | "exchange_withdrawal"
    from_wallet: Optional[str]   # Wallet de origen (puede ser anonima)
    to_wallet: Optional[str]     # Wallet de destino (puede ser anonima)
    detected_at: datetime        # Cuando se detecto la transaccion (UTC)

    @property
    def is_bearish_signal(self) -> bool:
        """
        Un deposito grande a un exchange es señal bajista —
        el holder probablemente va a vender.
        """
        return self.transaction_type == "exchange_deposit" and self.amount_usd > 5_000_000

    @property
    def is_bullish_signal(self) -> bool:
        """
        Un retiro grande de un exchange es señal alcista —
        el holder esta sacando sus coins para holdear.
        """
        return self.transaction_type == "exchange_withdrawal" and self.amount_usd > 5_000_000


# ─── MODELO: SNAPSHOT COMPLETO ────────────────────────────────────────────────

class CollectedSnapshot(BaseModel):
    """
    El resultado final del colector en cada ciclo del loop.
    Es lo que recibe la siguiente capa (analizador tecnico).

    Contiene TODOS los datos necesarios para que Claude tome una decision:
    - Precios actuales de todos los activos
    - Velas historicas para calcular indicadores tecnicos
    - Contexto macro del mercado global
    - Alertas de ballenas recientes
    """
    snapshot_at: datetime                          # Timestamp de este ciclo (UTC)
    tickers: dict[str, TickerData]                 # precio actual por simbolo
    candles: dict[str, dict[str, list[CandleData]]] # candles[simbolo][timeframe] = lista de velas
    market_context: MarketContext                   # contexto macro global
    whale_alerts: list[WhaleAlert]                 # alertas de las ultimas 4 horas
    collection_errors: list[str]                   # errores no criticos ocurridos en este ciclo

    @property
    def has_critical_gaps(self) -> bool:
        """
        Retorna True si faltan datos criticos que impiden operar.
        El agente no deberia operar si hay gaps criticos.
        """
        # Sin precios de BTC no podemos operar futuros
        if "BTCUSDT" not in self.tickers:
            return True
        # Sin contexto macro no tenemos contexto global
        if self.market_context is None:
            return True
        # Sin velas de BTC en 1h no podemos calcular indicadores
        btc_candles = self.candles.get("BTCUSDT", {})
        if "1h" not in btc_candles or len(btc_candles["1h"]) < 50:
            return True
        return False

    @property
    def available_symbols(self) -> list[str]:
        """Lista de activos con datos completos disponibles en este ciclo."""
        return [s for s in ALL_SYMBOLS if s in self.tickers and s in self.candles]

    def summary(self) -> str:
        """Resumen legible del snapshot para logs y debugging."""
        btc_price = self.tickers.get("BTCUSDT")
        price_str = f"${btc_price.price:,.2f}" if btc_price else "N/A"
        return (
            f"Snapshot {self.snapshot_at.strftime('%H:%M:%S UTC')} | "
            f"BTC: {price_str} | "
            f"Fear&Greed: {self.market_context.fear_greed_index} "
            f"({self.market_context.fear_greed_label}) | "
            f"Activos disponibles: {len(self.available_symbols)}/{len(ALL_SYMBOLS)} | "
            f"Errores: {len(self.collection_errors)}"
        )

    class Config:
        arbitrary_types_allowed = True
