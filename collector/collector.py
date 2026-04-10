"""
collector.py — Orquestador principal del colector
Responsabilidad: coordinar todas las fuentes de datos y producir
el CollectedSnapshot que recibe la siguiente capa.

Este es el archivo que llama el loop principal del agente cada 5 minutos.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from .binance import BinanceCollector
from .coinmarketcap import CoinMarketCapCollector
from .models import CollectedSnapshot, WhaleAlert, ALL_SYMBOLS

# Cargar variables de entorno desde el archivo .env
load_dotenv(override=False)

logger = logging.getLogger(__name__)


class DataCollector:
    """
    Orquestador principal — coordina Binance y CoinMarketCap
    y produce un CollectedSnapshot limpio en cada ciclo.

    Uso desde el loop principal:
        collector = DataCollector()
        await collector.initialize()

        # En cada ciclo del loop:
        snapshot = await collector.collect()
        if not snapshot.has_critical_gaps:
            # pasar snapshot al analizador
            ...

        await collector.shutdown()
    """

    def __init__(self):
        # Leer credenciales del archivo .env
        self.binance_api_key = os.getenv("BINANCE_API_KEY")
        self.binance_api_secret = os.getenv("BINANCE_API_SECRET")
        self.cmc_api_key = os.getenv("CMC_API_KEY")
        self.testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

        # Validar que esten todas las credenciales antes de arrancar
        self._validate_credentials()

        # Instancias de los conectores
        self.binance = BinanceCollector(
            api_key=self.binance_api_key,
            api_secret=self.binance_api_secret,
            testnet=self.testnet,
        )
        self.cmc = CoinMarketCapCollector(api_key=self.cmc_api_key)

        # Cache de noticias — se actualiza cada 30 minutos
        self._news_cache = None
        self._news_cache_expires_at = None

        # Cache de whale alerts — guarda las ultimas 4 horas
        self._whale_alerts: list[WhaleAlert] = []

        # Contador de ciclos con error consecutivos
        self._consecutive_errors = 0
        self.MAX_CONSECUTIVE_ERRORS = 3

        logger.info(
            f"DataCollector inicializado | "
            f"Activos: {len(ALL_SYMBOLS)} | "
            f"Modo: {'TESTNET' if self.testnet else 'PRODUCCION'}"
        )

    def _validate_credentials(self):
        """Verifica que todas las credenciales necesarias esten en el .env"""
        missing = []
        if not self.binance_api_key:
            missing.append("BINANCE_API_KEY")
        if not self.binance_api_secret:
            missing.append("BINANCE_API_SECRET")
        if not self.cmc_api_key:
            missing.append("CMC_API_KEY")

        if missing:
            raise EnvironmentError(
                f"Faltan las siguientes variables en el archivo .env: {', '.join(missing)}\n"
                f"Revisa que el archivo .env exista y tenga todas las credenciales."
            )

    async def initialize(self):
        """
        Conecta con todos los servicios externos.
        Debe llamarse una vez al arrancar el agente.
        """
        logger.info("Inicializando conexiones...")
        await self.binance.connect()
        logger.info("Colector listo para operar")

    async def shutdown(self):
        """
        Cierra todas las conexiones de forma limpia.
        Debe llamarse al detener el agente.
        """
        logger.info("Cerrando conexiones...")
        await self.binance.disconnect()
        logger.info("Colector detenido correctamente")

    async def collect(self) -> Optional[CollectedSnapshot]:
        """
        Recopila todos los datos del mercado en este ciclo.

        Llama a Binance y CoinMarketCap en paralelo para ser eficiente.
        Si algo falla, registra el error pero intenta continuar con los datos disponibles.

        Retorna None solo si ocurre un error critico que impide construir el snapshot.
        """
        cycle_start = datetime.now(timezone.utc)
        errors: list[str] = []

        logger.info(f"--- Inicio ciclo de recoleccion {cycle_start.strftime('%H:%M:%S UTC')} ---")

        try:
            # ── Paso 1: Lanzar todas las llamadas en paralelo ─────────────────
            # Binance tickers + Binance candles + CoinMarketCap al mismo tiempo
            tickers_task = self.binance.fetch_all_tickers()
            candles_task = self.binance.fetch_all_candles()
            context_task = self.cmc.fetch_market_context()

            tickers, candles, market_context = await asyncio.gather(
                tickers_task,
                candles_task,
                context_task,
                return_exceptions=False
            )

            # ── Paso 2: Registrar que fallo (sin detener el ciclo) ────────────
            missing_tickers = [s for s in ALL_SYMBOLS if s not in tickers]
            if missing_tickers:
                error_msg = f"Sin ticker para: {', '.join(missing_tickers)}"
                errors.append(error_msg)
                logger.warning(error_msg)

            if market_context is None:
                error_msg = "Sin contexto macro de CoinMarketCap"
                errors.append(error_msg)
                logger.warning(error_msg)
                # Usar contexto neutral para no bloquear el ciclo
                from .models import MarketContext
                market_context = MarketContext(
                    btc_dominance=50.0,
                    total_market_cap_usd=0.0,
                    total_volume_24h_usd=0.0,
                    fear_greed_index=50,
                    fear_greed_label="Neutral",
                    active_cryptocurrencies=0,
                    collected_at=cycle_start,
                )

            # ── Paso 3: Obtener whale alerts del cache ────────────────────────
            # (los whale alerts llegan por webhook — aqui solo los leemos del cache)
            whale_alerts = self._get_recent_whale_alerts()

            # ── Paso 4: Construir el snapshot final ───────────────────────────
            snapshot = CollectedSnapshot(
                snapshot_at=cycle_start,
                tickers=tickers,
                candles=candles,
                market_context=market_context,
                whale_alerts=whale_alerts,
                collection_errors=errors,
            )

            # ── Paso 5: Log del resultado ─────────────────────────────────────
            duration_ms = (datetime.now(timezone.utc) - cycle_start).total_seconds() * 1000
            logger.info(f"{snapshot.summary()} | Duracion: {duration_ms:.0f}ms")

            if snapshot.has_critical_gaps:
                logger.warning(
                    "GAPS CRITICOS en el snapshot — el agente NO operara en este ciclo"
                )

            self._consecutive_errors = 0
            return snapshot

        except Exception as e:
            self._consecutive_errors += 1
            logger.error(
                f"Error critico en ciclo de recoleccion: {e} | "
                f"Errores consecutivos: {self._consecutive_errors}/{self.MAX_CONSECUTIVE_ERRORS}"
            )

            if self._consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                logger.critical(
                    f"Se alcanzaron {self.MAX_CONSECUTIVE_ERRORS} errores consecutivos. "
                    f"El agente necesita atencion. Notificando por WhatsApp..."
                )
                # La notificacion de WhatsApp se implementa en el modulo executor
                # Por ahora solo logueamos el error critico

            return None

    def add_whale_alert(self, alert: WhaleAlert):
        """
        Agrega una alerta de ballena al cache.
        Este metodo es llamado por el webhook de Whale Alert cuando llega una alerta.
        """
        self._whale_alerts.append(alert)
        logger.info(
            f"Whale Alert recibida: {alert.symbol} "
            f"${alert.amount_usd / 1e6:.1f}M — {alert.transaction_type}"
        )

    def _get_recent_whale_alerts(self) -> list[WhaleAlert]:
        """
        Retorna solo las whale alerts de las ultimas 4 horas.
        Las mas antiguas se descartan automaticamente.
        """
        now = datetime.now(timezone.utc)
        cutoff_hours = 4

        # Filtrar las que tienen menos de 4 horas
        recent = [
            alert for alert in self._whale_alerts
            if (now - alert.detected_at).total_seconds() < cutoff_hours * 3600
        ]

        # Actualizar el cache descartando las viejas
        self._whale_alerts = recent

        if recent:
            logger.info(f"Whale alerts activas: {len(recent)} en las ultimas {cutoff_hours}h")

        return recent
