"""
main.py — Loop principal del agente de trading
Responsabilidad: coordinar todas las capas y ejecutar
el ciclo de análisis cada 5 minutos.

Flujo de cada ciclo:
1. Verificar saldo en Binance
2. Si no hay saldo → notificar y esperar
3. Recopilar datos del mercado
4. Analizar indicadores técnicos
5. Para cada señal válida → consultar a Claude
6. Ejecutar decisiones (autónomas o con VoBo)
7. Registrar en SQLite
8. Enviar resumen diario a las 10pm

Uso:
    python3.11 main.py
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from loguru import logger

load_dotenv(override=False)

# Configurar logging
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector import DataCollector
from analyzer import TechnicalAnalyzer
from analyzer.analyzer import TradingSignal
from brain import ClaudeBrain
from executor import TradingExecutor
from database import TradingDatabase, TradeRecord, SignalRecord

# Configuración
LOOP_INTERVAL_MIN = int(os.getenv("LOOP_INTERVAL_MIN", "5"))
DAILY_REPORT_TIME = os.getenv("DAILY_REPORT_TIME", "22:00")
DAILY_REPORT_TZ = os.getenv("DAILY_REPORT_TIMEZONE", "America/Mexico_City")


class TradingAgent:
    """
    Agente de trading algorítmico completo.
    Coordina todas las capas del sistema.
    """

    def __init__(self):
        self.collector = DataCollector()
        self.analyzer = TechnicalAnalyzer()
        self.brain = ClaudeBrain()
        self.executor: TradingExecutor = None
        self.db = TradingDatabase()
        self.scheduler = AsyncIOScheduler(timezone=DAILY_REPORT_TZ)
        self.running = False

        logger.info("TradingAgent inicializado")

    async def initialize(self):
        """Inicializa todas las conexiones."""
        logger.info("Inicializando agente...")

        # Inicializar base de datos
        self.db.initialize()

        # Conectar con Binance
        await self.collector.initialize()

        # Inicializar executor con el exchange del colector
        testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
        self.executor = TradingExecutor(
            exchange=self.collector.binance.exchange,
            testnet=testnet
        )

        # Programar resumen diario
        hour, minute = DAILY_REPORT_TIME.split(":")
        self.scheduler.add_job(
            self.send_daily_report,
            "cron",
            hour=int(hour),
            minute=int(minute),
            id="daily_report"
        )
        self.scheduler.start()

        logger.info(
            f"Agente listo | Loop: cada {LOOP_INTERVAL_MIN} min | "
            f"Reporte: {DAILY_REPORT_TIME} ({DAILY_REPORT_TZ})"
        )

    async def run_cycle(self):
        """
        Ejecuta un ciclo completo de análisis y trading.
        Se llama cada 5 minutos.
        """
        cycle_start = datetime.now()
        logger.info(f"─── Inicio de ciclo: {cycle_start.strftime('%H:%M:%S')} ───")

        try:
            # Paso 1: Verificar saldo
            balance = await self.executor.check_balance()
            if balance is None:
                logger.warning("Sin saldo o error de conexión — ciclo saltado")
                return

            logger.info(f"Saldo: {balance.summary}")

            # Paso 2: Verificar operaciones abiertas
            open_trades = self.db.get_open_trades_count()
            max_trades = int(os.getenv("MAX_OPEN_TRADES", "10"))

            if open_trades >= max_trades:
                logger.info(
                    f"Máximo de operaciones alcanzado: {open_trades}/{max_trades}"
                )
                return

            # Paso 3: Recopilar datos del mercado
            snapshot = await self.collector.collect()
            if not snapshot or snapshot.has_critical_gaps:
                logger.warning("Datos insuficientes — ciclo saltado")
                return

            # Paso 4: Analizar indicadores técnicos
            analysis = self.analyzer.analyze(snapshot)

            # Registrar todas las señales en la base de datos
            for signal in analysis.signals:
                self.db.record_signal(SignalRecord(
                    id=None,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    score=signal.score,
                    was_traded=False,  # Se actualiza si Claude decide operar
                    reason_not_traded=None,
                    detected_at=datetime.now(),
                    rsi=signal.indicators_1h.rsi.value,
                    macd_signal=signal.indicators_1h.macd.signal,
                    volume_ratio=signal.indicators_1h.volume.ratio,
                    trend=signal.indicators_1h.trend
                ))

            if not analysis.has_signals:
                logger.info("Sin señales válidas en este ciclo")
                return

            logger.info(f"{len(analysis.signals)} señal(es) detectada(s)")

            # Paso 5: Para cada señal, consultar a Claude
            for signal in analysis.signals[:3]:  # Máximo 3 por ciclo
                # Verificar que aún hay espacio para más operaciones
                open_count = self.db.get_open_trades_count()
                if open_count >= max_trades:
                    logger.info("Máximo de operaciones alcanzado durante el ciclo")
                    break

                await self.process_signal(signal, balance, snapshot)

        except Exception as e:
            logger.error(f"Error en ciclo de trading: {e}")
            if self.executor and self.executor.notifications_enabled:
                self.executor.notifier.notify_critical_error(str(e))

        finally:
            duration = (datetime.now() - cycle_start).total_seconds()
            logger.info(f"─── Ciclo completado en {duration:.1f}s ───")

    async def process_signal(self, signal: TradingSignal, balance, snapshot):
        """Procesa una señal individual: consulta a Claude y ejecuta."""
        logger.info(
            f"Procesando: {signal.symbol} {signal.direction.upper()} "
            f"(score: {signal.score:.0f})"
        )

        # Consultar a Claude
        decision = self.brain.decide(signal, snapshot, balance.operable)

        if not decision:
            logger.warning(f"Claude no pudo decidir para {signal.symbol}")
            return

        if not decision.should_trade:
            logger.info(f"Claude no opera {signal.symbol}: {decision.reason_not_trade}")
            return

        # Ejecutar la decisión
        result = await self.executor.execute_decision(decision, balance)

        if result and result.success:
            # Registrar en base de datos
            trade_id = self.db.open_trade(TradeRecord(
                id=None,
                symbol=decision.symbol,
                direction=decision.direction,
                trading_mode=decision.trading_mode,
                amount_usd=decision.amount_usd,
                entry_price=result.entry_price,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                leverage=decision.leverage,
                score=signal.score,
                reasoning=decision.reasoning,
                status="open",
                opened_at=datetime.now(),
                closed_at=None,
                exit_price=None,
                pnl_usd=None,
                pnl_pct=None,
                close_reason=None,
                order_id=result.order_id
            ))

            logger.info(
                f"Operación registrada en DB: ID {trade_id} | "
                f"{decision.symbol} {decision.direction.upper()} "
                f"${decision.amount_usd:.2f}"
            )

    async def send_daily_report(self):
        """Genera y envía el resumen diario a las 10pm."""
        logger.info("Generando resumen diario...")

        try:
            balance = await self.executor.check_balance()
            current_balance = balance.usdt_free if balance else 0

            today = datetime.now().strftime("%Y-%m-%d")
            summary = self.db.get_daily_summary(today)

            self.db.save_daily_summary(
                date=today,
                starting_balance=self.executor._daily_starting_balance or current_balance,
                ending_balance=current_balance
            )

            await self.executor.send_daily_report(current_balance)

            logger.info(
                f"Resumen enviado: {summary['total_trades']} operaciones | "
                f"P&L: ${summary['total_pnl_usd']:.2f}"
            )

        except Exception as e:
            logger.error(f"Error generando resumen diario: {e}")

    async def run(self):
        """Loop principal — corre indefinidamente."""
        self.running = True

        # Notificar arranque
        try:
            balance = await self.executor.check_balance()
            if balance and self.executor.notifications_enabled:
                self.executor.notifier.notify_agent_started(balance.usdt_free)
        except Exception:
            pass

        logger.info(f"Agente corriendo — ciclo cada {LOOP_INTERVAL_MIN} minutos")

        while self.running:
            await self.run_cycle()
            # Esperar al siguiente ciclo
            await asyncio.sleep(LOOP_INTERVAL_MIN * 60)

    async def shutdown(self):
        """Cierra todas las conexiones de forma limpia."""
        logger.info("Deteniendo agente...")
        self.running = False
        self.scheduler.shutdown()
        await self.collector.shutdown()
        logger.info("Agente detenido correctamente")


async def main():
    agent = TradingAgent()

    # Manejo de señales del sistema para cierre limpio
    loop = asyncio.get_event_loop()

    def handle_shutdown():
        logger.info("Señal de cierre recibida")
        asyncio.create_task(agent.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown)

    try:
        await agent.initialize()
        await agent.run()
    except KeyboardInterrupt:
        logger.info("Interrumpido por el usuario")
    finally:
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
