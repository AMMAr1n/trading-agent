"""
coingecko.py — Colector de noticias y sentimiento de CoinGecko
Responsabilidad: obtener noticias recientes y sentiment votes por crypto.
Llamada condicional — solo cuando el scorer detecta una señal válida.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Mapeo de símbolo Binance → ID de CoinGecko
COINGECKO_IDS = {
    "BTCUSDT":  "bitcoin",
    "ETHUSDT":  "ethereum",
    "SOLUSDT":  "solana",
    "BNBUSDT":  "binancecoin",
    "DOGEUSDT": "dogecoin",
    "XRPUSDT":  "ripple",
    "ADAUSDT":  "cardano",
}

BASE_URL = "https://api.coingecko.com/api/v3"


class CoinGeckoCollector:
    """
    Obtiene noticias y sentimiento de CoinGecko.
    No requiere API key — usa el endpoint público gratuito.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10)
        logger.info("CoinGeckoCollector inicializado")

    async def get_news_and_sentiment(self, symbol: str) -> Optional[dict]:
        """
        Obtiene noticias recientes y sentiment votes para un par.
        Retorna un dict con noticias y sentimiento, o None si falla.
        """
        coin_id = COINGECKO_IDS.get(symbol)
        if not coin_id:
            logger.warning(f"CoinGecko: ID no encontrado para {symbol}")
            return None

        try:
            # Obtener datos del coin — incluye sentiment votes
            response = await self.client.get(
                f"{BASE_URL}/coins/{coin_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "false",
                    "community_data": "true",
                    "developer_data": "false",
                    "sparkline": "false",
                }
            )

            if response.status_code == 429:
                logger.warning("CoinGecko: rate limit alcanzado")
                return None

            if response.status_code != 200:
                logger.warning(f"CoinGecko: error {response.status_code} para {symbol}")
                return None

            data = response.json()

            # Extraer sentiment votes
            sentiment_up   = data.get("sentiment_votes_up_percentage", 50.0) or 50.0
            sentiment_down = data.get("sentiment_votes_down_percentage", 50.0) or 50.0

            # Determinar sentimiento dominante
            if sentiment_up >= 65:
                sentiment_label = "🟢 BULLISH"
            elif sentiment_down >= 65:
                sentiment_label = "🔴 BEARISH"
            else:
                sentiment_label = "⚪ NEUTRAL"

            # Obtener descripción/resumen si está disponible
            description = ""
            desc_data = data.get("description", {})
            if isinstance(desc_data, dict):
                description = desc_data.get("en", "")[:200] if desc_data.get("en") else ""

            result = {
                "symbol":          symbol,
                "coin_id":         coin_id,
                "sentiment_up":    round(sentiment_up, 1),
                "sentiment_down":  round(sentiment_down, 1),
                "sentiment_label": sentiment_label,
                "collected_at":    datetime.now(timezone.utc),
            }

            logger.info(
                f"CoinGecko {symbol}: Sentiment {sentiment_label} "
                f"(↑{sentiment_up:.0f}% / ↓{sentiment_down:.0f}%)"
            )
            return result

        except Exception as e:
            logger.error(f"CoinGecko: error obteniendo datos para {symbol}: {e}")
            return None

    async def close(self):
        await self.client.aclose()
