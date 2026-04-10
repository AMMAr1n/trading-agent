"""
binance.py — Conector con Binance API
Responsabilidad: pedir precios actuales y velas historicas de todos los activos.

Usa ccxt que es la libreria estandar para conectarse a exchanges de crypto.
Maneja reintentos automaticos si la conexion falla.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import ccxt.async_support as ccxt

from .models import CandleData, TickerData, CANDLE_TIMEFRAMES, CANDLES_LIMIT, ALL_SYMBOLS

# Logger para este modulo — los mensajes aparecen en los logs del servidor
logger = logging.getLogger(__name__)

# Cuantas veces reintentar si Binance no responde
MAX_RETRIES = 3

# Segundos de espera entre reintentos (se duplica cada vez: 1s, 2s, 4s)
RETRY_BASE_DELAY = 1.0


class BinanceCollector:
    """
    Recopila datos de mercado desde Binance.

    Uso basico:
        collector = BinanceCollector(api_key="...", api_secret="...")
        await collector.connect()
        tickers = await collector.fetch_all_tickers()
        candles = await collector.fetch_candles("BTCUSDT", "1h")
        await collector.disconnect()
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        """
        api_key:    Tu API Key de Binance
        api_secret: Tu API Secret de Binance
        testnet:    True para usar Binance Testnet (dinero simulado)
                    False para operar con dinero real
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.exchange: Optional[ccxt.binance] = None

    async def connect(self):
        """
        Establece la conexion con Binance.
        Debe llamarse antes de cualquier otra operacion.
        """
        options = {
            "defaultType": "future" if not self.testnet else "future",
            "adjustForTimeDifference": True,
        }

        self.exchange = ccxt.binance({
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "options": options,
            "enableRateLimit": True,  # Respeta los limites de la API automaticamente
        })

        # Si estamos en testnet, apuntar al servidor de pruebas
        if self.testnet:
            self.exchange.set_sandbox_mode(True)
            logger.info("Conectado a Binance TESTNET — dinero simulado")
        else:
            logger.info("Conectado a Binance PRODUCCION — dinero real")

        # Cargar los mercados disponibles
        await self.exchange.load_markets()
        logger.info(f"Mercados cargados: {len(self.exchange.markets)} pares disponibles")

    async def disconnect(self):
        """Cierra la conexion con Binance de forma limpia."""
        if self.exchange:
            await self.exchange.close()
            logger.info("Conexion con Binance cerrada")

    async def fetch_ticker(self, symbol: str) -> Optional[TickerData]:
        """
        Obtiene el precio actual y stats de 24h de un activo.

        Retorna None si falla despues de todos los reintentos.
        """
        for attempt in range(MAX_RETRIES):
            try:
                raw = await self.exchange.fetch_ticker(symbol)

                return TickerData(
                    symbol=symbol,
                    price=float(raw["last"]),
                    change_24h_pct=float(raw["percentage"] or 0),
                    volume_24h=float(raw["quoteVolume"] or 0),
                    high_24h=float(raw["high"] or raw["last"]),
                    low_24h=float(raw["low"] or raw["last"]),
                    collected_at=datetime.now(timezone.utc),
                )

            except ccxt.NetworkError as e:
                # Error de red — reintenta con espera exponencial
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"Error de red al obtener ticker {symbol} "
                    f"(intento {attempt + 1}/{MAX_RETRIES}): {e}. "
                    f"Reintentando en {delay}s..."
                )
                await asyncio.sleep(delay)

            except ccxt.ExchangeError as e:
                # Error del exchange (ej. par no disponible) — no tiene sentido reintentar
                logger.error(f"Error del exchange al obtener ticker {symbol}: {e}")
                return None

            except Exception as e:
                logger.error(f"Error inesperado al obtener ticker {symbol}: {e}")
                return None

        logger.error(f"Fallo al obtener ticker {symbol} despues de {MAX_RETRIES} intentos")
        return None

    async def fetch_all_tickers(self) -> dict[str, TickerData]:
        """
        Obtiene el precio actual de TODOS los activos del portafolio en paralelo.

        Usar asyncio.gather permite hacer todas las llamadas al mismo tiempo
        en lugar de una por una — mucho mas rapido.

        Retorna un diccionario: {"BTCUSDT": TickerData(...), "ETHUSDT": TickerData(...), ...}
        """
        logger.info(f"Obteniendo tickers de {len(ALL_SYMBOLS)} activos en paralelo...")

        # Lanza todas las llamadas al mismo tiempo
        tasks = [self.fetch_ticker(symbol) for symbol in ALL_SYMBOLS]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        # Filtra los que fallaron (None) y construye el diccionario
        tickers = {}
        for symbol, result in zip(ALL_SYMBOLS, results):
            if result is not None:
                tickers[symbol] = result
            else:
                logger.warning(f"No se pudo obtener ticker de {symbol} — excluido de este ciclo")

        logger.info(f"Tickers obtenidos: {len(tickers)}/{len(ALL_SYMBOLS)}")
        return tickers

    async def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = CANDLES_LIMIT
    ) -> list[CandleData]:
        """
        Obtiene las ultimas N velas de un activo en un timeframe especifico.

        symbol:    ej. "BTCUSDT"
        timeframe: ej. "1h" — debe ser uno de CANDLE_TIMEFRAMES
        limit:     cuantas velas historicas pedir (default: 200)

        Retorna lista vacia si falla.
        """
        for attempt in range(MAX_RETRIES):
            try:
                # Binance devuelve listas de: [timestamp, open, high, low, close, volume]
                raw_candles = await self.exchange.fetch_ohlcv(
                    symbol,
                    timeframe=timeframe,
                    limit=limit
                )

                candles = []
                for raw in raw_candles:
                    timestamp_ms, open_, high, low, close, volume = raw
                    candle = CandleData(
                        symbol=symbol,
                        timeframe=timeframe,
                        # Binance da el timestamp en milisegundos — convertir a datetime UTC
                        timestamp=datetime.fromtimestamp(
                            timestamp_ms / 1000,
                            tz=timezone.utc
                        ),
                        open=float(open_),
                        high=float(high),
                        low=float(low),
                        close=float(close),
                        volume=float(volume),
                    )
                    candles.append(candle)

                logger.debug(
                    f"Velas obtenidas: {symbol} {timeframe} — {len(candles)} velas"
                )
                return candles

            except ccxt.NetworkError as e:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"Error de red al obtener velas {symbol}/{timeframe} "
                    f"(intento {attempt + 1}/{MAX_RETRIES}): {e}. "
                    f"Reintentando en {delay}s..."
                )
                await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"Error al obtener velas {symbol}/{timeframe}: {e}")
                return []

        logger.error(
            f"Fallo al obtener velas {symbol}/{timeframe} "
            f"despues de {MAX_RETRIES} intentos"
        )
        return []

    async def fetch_all_candles(
        self,
        symbols: list[str] = None,
        timeframes: list[str] = None
    ) -> dict[str, dict[str, list[CandleData]]]:
        """
        Obtiene velas de TODOS los activos en TODOS los timeframes en paralelo.

        Retorna estructura anidada:
        {
            "BTCUSDT": {
                "1m":  [CandleData, CandleData, ...],
                "15m": [CandleData, CandleData, ...],
                "1h":  [CandleData, CandleData, ...],
                "4h":  [CandleData, CandleData, ...],
            },
            "ETHUSDT": { ... },
            ...
        }
        """
        symbols = symbols or ALL_SYMBOLS
        timeframes = timeframes or CANDLE_TIMEFRAMES

        total_calls = len(symbols) * len(timeframes)
        logger.info(
            f"Obteniendo velas: {len(symbols)} activos x {len(timeframes)} timeframes "
            f"= {total_calls} llamadas en paralelo..."
        )

        # Crea una tarea por cada combinacion activo+timeframe
        tasks = []
        task_keys = []
        for symbol in symbols:
            for timeframe in timeframes:
                tasks.append(self.fetch_candles(symbol, timeframe))
                task_keys.append((symbol, timeframe))

        # Ejecuta todo en paralelo
        results = await asyncio.gather(*tasks)

        # Organiza los resultados en la estructura anidada
        all_candles: dict[str, dict[str, list[CandleData]]] = {}
        for (symbol, timeframe), candle_list in zip(task_keys, results):
            if symbol not in all_candles:
                all_candles[symbol] = {}
            if candle_list:
                all_candles[symbol][timeframe] = candle_list
            else:
                logger.warning(f"Sin velas para {symbol}/{timeframe} en este ciclo")

        # Contar cuantos activos tienen datos completos
        complete = sum(
            1 for s in symbols
            if s in all_candles and len(all_candles[s]) == len(timeframes)
        )
        logger.info(f"Velas completas: {complete}/{len(symbols)} activos")

        return all_candles
