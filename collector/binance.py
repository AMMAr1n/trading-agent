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

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


class BinanceCollector:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.exchange: Optional[ccxt.binance] = None

    async def connect(self):
        self.exchange = ccxt.binance({
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "options": {
                "defaultType": "future",
                "adjustForTimeDifference": True,
                # Solo cargar USD-M futures — evita timeout de COIN-M
                "fetchMarkets": ["linear"],
            },
            "enableRateLimit": True,
        })

        if self.testnet:
            self.exchange.set_sandbox_mode(True)
            logger.info("Conectado a Binance TESTNET — dinero simulado")
        else:
            logger.info("Conectado a Binance PRODUCCION — dinero real")

        await self.exchange.load_markets()
        logger.info(f"Mercados cargados: {len(self.exchange.markets)} pares disponibles")

    async def disconnect(self):
        if self.exchange:
            await self.exchange.close()
            logger.info("Conexion con Binance cerrada")

    async def fetch_ticker(self, symbol: str) -> Optional[TickerData]:
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
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"Error de red al obtener ticker {symbol} "
                    f"(intento {attempt + 1}/{MAX_RETRIES}): {e}. "
                    f"Reintentando en {delay}s..."
                )
                await asyncio.sleep(delay)
            except ccxt.ExchangeError as e:
                logger.error(f"Error del exchange al obtener ticker {symbol}: {e}")
                return None
            except Exception as e:
                logger.error(f"Error inesperado al obtener ticker {symbol}: {e}")
                return None

        logger.error(f"Fallo al obtener ticker {symbol} despues de {MAX_RETRIES} intentos")
        return None

    async def fetch_all_tickers(self) -> dict[str, TickerData]:
        logger.info(f"Obteniendo tickers de {len(ALL_SYMBOLS)} activos en paralelo...")
        tasks = [self.fetch_ticker(symbol) for symbol in ALL_SYMBOLS]
        results = await asyncio.gather(*tasks, return_exceptions=False)
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
        for attempt in range(MAX_RETRIES):
            try:
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
                logger.debug(f"Velas obtenidas: {symbol} {timeframe} — {len(candles)} velas")
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

        logger.error(f"Fallo al obtener velas {symbol}/{timeframe} despues de {MAX_RETRIES} intentos")
        return []

    async def fetch_all_candles(
        self,
        symbols: list[str] = None,
        timeframes: list[str] = None
    ) -> dict[str, dict[str, list[CandleData]]]:
        symbols = symbols or ALL_SYMBOLS
        timeframes = timeframes or CANDLE_TIMEFRAMES

        total_calls = len(symbols) * len(timeframes)
        logger.info(
            f"Obteniendo velas: {len(symbols)} activos x {len(timeframes)} timeframes "
            f"= {total_calls} llamadas en paralelo..."
        )

        tasks = []
        task_keys = []
        for symbol in symbols:
            for timeframe in timeframes:
                tasks.append(self.fetch_candles(symbol, timeframe))
                task_keys.append((symbol, timeframe))

        results = await asyncio.gather(*tasks)

        all_candles: dict[str, dict[str, list[CandleData]]] = {}
        for (symbol, timeframe), candle_list in zip(task_keys, results):
            if symbol not in all_candles:
                all_candles[symbol] = {}
            if candle_list:
                all_candles[symbol][timeframe] = candle_list
            else:
                logger.warning(f"Sin velas para {symbol}/{timeframe} en este ciclo")

        complete = sum(
            1 for s in symbols
            if s in all_candles and len(all_candles[s]) == len(timeframes)
        )
        logger.info(f"Velas completas: {complete}/{len(symbols)} activos")
        return all_candles
