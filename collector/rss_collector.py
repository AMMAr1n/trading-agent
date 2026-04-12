"""
rss_collector.py — Colector de noticias via RSS feeds
Responsabilidad: obtener titulares recientes de medios crypto
y filtrarlos por activo relevante para enriquecer el contexto de Claude.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

# RSS feeds de medios crypto confiables
RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

# Mapeo de símbolo a palabras clave de búsqueda
SYMBOL_KEYWORDS = {
    "BTCUSDT":  ["bitcoin", "btc"],
    "ETHUSDT":  ["ethereum", "eth"],
    "SOLUSDT":  ["solana", "sol"],
    "BNBUSDT":  ["binance", "bnb"],
    "DOGEUSDT": ["dogecoin", "doge"],
    "XRPUSDT":  ["xrp", "ripple"],
    "ADAUSDT":  ["cardano", "ada"],
}

# Cache global — se actualiza cada 15 minutos
_cache: dict = {}
_cache_expires_at: Optional[datetime] = None
CACHE_MINUTES = 15


class RSSCollector:
    """
    Obtiene titulares recientes de RSS feeds crypto.
    Filtra por palabras clave del par analizado.
    Cache de 15 minutos para no saturar los feeds.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=8)
        logger.info("RSSCollector inicializado")

    async def get_news_for_symbol(self, symbol: str, max_items: int = 5) -> list[str]:
        """
        Retorna lista de titulares recientes relevantes para el símbolo.
        """
        global _cache, _cache_expires_at

        # Actualizar cache si expiró
        now = datetime.now(timezone.utc)
        if _cache_expires_at is None or now > _cache_expires_at:
            await self._refresh_cache()
            _cache_expires_at = now + timedelta(minutes=CACHE_MINUTES)

        keywords = SYMBOL_KEYWORDS.get(symbol, [])
        if not keywords:
            return []

        # Filtrar noticias por palabras clave
        relevant = []
        for item in _cache.get("items", []):
            title = item.get("title", "").lower()
            if any(kw in title for kw in keywords):
                relevant.append(item["title"])
                if len(relevant) >= max_items:
                    break

        logger.info(f"RSS {symbol}: {len(relevant)} noticias relevantes encontradas")
        return relevant

    async def _refresh_cache(self):
        """Actualiza el cache con los últimos titulares de todos los feeds."""
        global _cache
        all_items = []

        for feed_url in RSS_FEEDS:
            try:
                response = await self.client.get(feed_url)
                if response.status_code != 200:
                    continue

                root = ET.fromstring(response.text)
                channel = root.find("channel")
                if channel is None:
                    continue

                for item in channel.findall("item")[:20]:
                    title = item.findtext("title", "").strip()
                    pub_date = item.findtext("pubDate", "")
                    if title:
                        all_items.append({
                            "title":    title,
                            "pub_date": pub_date,
                            "source":   feed_url.split("/")[2],
                        })

            except Exception as e:
                logger.warning(f"RSS: error leyendo {feed_url}: {e}")

        _cache = {"items": all_items}
        logger.info(f"RSS cache actualizado: {len(all_items)} titulares de {len(RSS_FEEDS)} fuentes")

    async def close(self):
        await self.client.aclose()
