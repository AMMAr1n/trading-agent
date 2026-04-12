"""
coinmarketcap.py — Conector con CoinMarketCap API
Responsabilidad: obtener el contexto macro global del mercado.

Incluye: dominancia BTC, Fear & Greed Index, capitalizacion total,
volumen global y numero de criptomonedas activas.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from .models import MarketContext

logger = logging.getLogger(__name__)

# URL base de la API de CoinMarketCap
CMC_BASE_URL = "https://pro-api.coinmarketcap.com"

# Timeout en segundos para las llamadas HTTP
REQUEST_TIMEOUT = 10.0

# Fear & Greed thresholds para asignar etiqueta
FEAR_GREED_LABELS = {
    (0, 20):  "Extreme Fear",
    (21, 40): "Fear",
    (41, 60): "Neutral",
    (61, 80): "Greed",
    (81, 100): "Extreme Greed",
}


def _get_fear_greed_label(index: int) -> str:
    """Convierte el numero del Fear & Greed Index en su etiqueta descriptiva."""
    for (low, high), label in FEAR_GREED_LABELS.items():
        if low <= index <= high:
            return label
    return "Unknown"


class CoinMarketCapCollector:
    """
    Recopila contexto macro del mercado desde CoinMarketCap.

    Uso basico:
        cmc = CoinMarketCapCollector(api_key="...")
        context = await cmc.fetch_market_context()
    """

    def __init__(self, api_key: str):
        """
        api_key: Tu API Key de CoinMarketCap (plan Basic gratuito es suficiente)
        """
        self.api_key = api_key
        self.headers = {
            "X-CMC_PRO_API_KEY": self.api_key,
            "Accept": "application/json",
        }

    async def fetch_global_metrics(self) -> Optional[dict]:
        """
        Llama al endpoint de metricas globales de CoinMarketCap.
        Retorna el JSON crudo de la API, o None si falla.
        """
        url = f"{CMC_BASE_URL}/v1/global-metrics/quotes/latest"

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(url, headers=self.headers)
                response.raise_for_status()
                data = response.json()

                if data.get("status", {}).get("error_code") != 0:
                    error_msg = data.get("status", {}).get("error_message", "Error desconocido")
                    logger.error(f"CoinMarketCap API error: {error_msg}")
                    return None

                return data.get("data", {})

        except httpx.TimeoutException:
            logger.error("Timeout al conectar con CoinMarketCap — superado el limite de 10s")
            return None

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error("CoinMarketCap: API Key invalida o expirada")
            elif e.response.status_code == 429:
                logger.warning("CoinMarketCap: limite de llamadas alcanzado — espera antes de reintentar")
            else:
                logger.error(f"CoinMarketCap HTTP error {e.response.status_code}: {e}")
            return None

        except Exception as e:
            logger.error(f"Error inesperado al llamar CoinMarketCap: {e}")
            return None

    async def fetch_fear_greed_index(self) -> Optional[int]:
        """
        Obtiene el Fear & Greed Index actual.

        CoinMarketCap lo incluye en sus metricas globales.
        Rango: 0 (panico extremo) — 100 (codicia extrema).
        """
        url = f"{CMC_BASE_URL}/v3/fear-and-greed/latest"

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(url, headers=self.headers)
                response.raise_for_status()
                data = response.json()

                # El valor viene en data.value
                value = data.get("data", {}).get("value")
                if value is not None:
                    return int(float(value))

                logger.warning("Fear & Greed Index no disponible en la respuesta")
                return None

        except httpx.HTTPStatusError as e:
            # Si este endpoint falla, usamos un valor neutral por defecto
            logger.warning(
                f"No se pudo obtener Fear & Greed Index (HTTP {e.response.status_code}). "
                f"Se usara valor neutral (50)."
            )
            return 50

        except Exception as e:
            logger.warning(f"Error al obtener Fear & Greed Index: {e}. Se usara valor neutral (50).")
            return 50

    async def fetch_market_context(self) -> Optional[MarketContext]:
        """
        Obtiene el contexto macro completo del mercado.

        Combina metricas globales + Fear & Greed Index en un solo objeto.
        Retorna None solo si falla la llamada principal de metricas.
        """
        logger.info("Obteniendo contexto macro de CoinMarketCap...")

        # Llamar a ambos endpoints en paralelo para ser mas rapidos
        import asyncio
        global_data, fear_greed = await asyncio.gather(
            self.fetch_global_metrics(),
            self.fetch_fear_greed_index(),
            return_exceptions=False
        )

        # Si no tenemos datos globales, no podemos construir el contexto
        if global_data is None:
            logger.error("No se pudo obtener metricas globales de CoinMarketCap")
            return None

        # Extraer valores del JSON — con fallbacks seguros si falta algun campo
        quote = global_data.get("quote", {}).get("USD", {})
        btc_dominance = global_data.get("btc_dominance", 0.0)
        total_market_cap = quote.get("total_market_cap", 0.0)
        total_volume_24h = quote.get("total_volume_24h", 0.0)
        active_cryptos = global_data.get("active_cryptocurrencies", 0)

        # Si el Fear & Greed fallo, usar valor neutral
        fg_index = fear_greed if fear_greed is not None else 50
        fg_label = _get_fear_greed_label(fg_index)

        context = MarketContext(
            btc_dominance=float(btc_dominance),
            total_market_cap_usd=float(total_market_cap),
            total_volume_24h_usd=float(total_volume_24h),
            fear_greed_index=fg_index,
            fear_greed_label=fg_label,
            active_cryptocurrencies=int(active_cryptos),
            collected_at=datetime.now(timezone.utc),
        )

        logger.info(
            f"Contexto macro obtenido — "
            f"BTC Dominance: {context.btc_dominance:.1f}% | "
            f"Fear & Greed: {context.fear_greed_index} ({context.fear_greed_label}) | "
            f"Market Cap: ${context.total_market_cap_usd / 1e12:.2f}T"
        )

        return context
