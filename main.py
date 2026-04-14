"""
main.py — Loop principal del agente de trading
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
from executor.position_monitor import PositionMonitor
from database import TradingDatabase, TradeRecord, SignalRecord

LOOP_INTERVAL_MIN = int(os.getenv("LOOP_INTERVAL_MIN", "5"))
DAILY_REPORT_TZ   = os.getenv("DAILY_REPORT_TIMEZONE", "America/Mexico_City")


class TradingAgent:
    def __init__(self):
        self.collector = DataCollector()
        self.analyzer  = TechnicalAnalyzer()
        self.brain     = ClaudeBrain()
        self.executor: TradingExecutor = None
        self.monitor:  PositionMonitor  = None
        self.db        = TradingDatabase()
        self.scheduler = AsyncIOScheduler(timezone=DAILY_REPORT_TZ)
        self.running   = False
        logger.info("TradingAgent inicializado")

    async def initialize(self):
        logger.info("Inicializando agente...")
        self.db.initialize()
        await self.collector.initialize()

        testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
        self.executor = TradingExecutor(
            exchange=self.collector.binance.exchange,
            testnet=testnet
        )

        # ── Position Monitor ──────────────────────────────────────────────
        self.monitor = PositionMonitor(
            exchange=self.collector.binance.exchange,
            order_executor=self.executor.order_executor,
            notifier=self.executor.notifier if self.executor.notifications_enabled else None,
            trading_executor=self.executor,
            db=self.db,
        )
        # ──────────────────────────────────────────────────────────────────

        for hour in [0, 6, 12, 18]:
            self.scheduler.add_job(
                self.send_periodic_report,
                "cron", hour=hour, minute=0,
                id=f"report_{hour:02d}h"
            )

        # ── Cargar posiciones abiertas de DB al iniciar ──────────────────
        await self._restore_tracked_positions()
        # ──────────────────────────────────────────────────────────────────

        self.scheduler.start()
        logger.info(
            f"Agente listo | Loop: cada {LOOP_INTERVAL_MIN} min | "
            f"Reportes: 12am, 6am, 12pm, 6pm ({DAILY_REPORT_TZ})"
        )

    async def run_cycle(self):
        cycle_start = datetime.now()
        logger.info(f"─── Inicio de ciclo: {cycle_start.strftime('%H:%M:%S')} ───")

        try:
            # ── 1. Monitorear posiciones abiertas primero ─────────────────
            await self.monitor.run()
            # ─────────────────────────────────────────────────────────────

            balance = await self.executor.check_balance()
            if balance is None:
                logger.warning("Sin saldo o error de conexión — ciclo saltado")
                return

            logger.info(f"Saldo: {balance.summary}")

            # Sincronizar con Binance — contar posiciones reales abiertas
            max_trades = int(os.getenv("MAX_OPEN_TRADES", "3"))
            try:
                positions    = await self.collector.binance.exchange.fetch_positions()
                open_trades  = sum(
                    1 for p in positions
                    if p.get("contracts") and float(p["contracts"]) > 0
                )
            except Exception:
                open_trades = self.db.get_open_trades_count()  # fallback a DB

            if open_trades >= max_trades:
                logger.info(f"Máximo de operaciones alcanzado: {open_trades}/{max_trades} (Binance)")
                return

            snapshot = await self.collector.collect()
            if not snapshot or snapshot.has_critical_gaps:
                logger.warning("Datos insuficientes — ciclo saltado")
                return

            analysis = self.analyzer.analyze(snapshot)

            for signal in analysis.signals:
                self.db.record_signal(SignalRecord(
                    id=None, symbol=signal.symbol,
                    direction=signal.direction, score=signal.score,
                    was_traded=False, reason_not_traded=None,
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

            # Obtener pares ya abiertos en Binance (evitar duplicados)
            try:
                raw_pos = await self.collector.binance.exchange.fetch_positions()
                open_symbols_binance = set()
                for p in raw_pos:
                    if p.get("contracts") and float(p["contracts"]) > 0:
                        # Normalizar símbolo: XRP/USDT:USDT -> XRPUSDT
                        sym = p["symbol"]
                        base = sym.split("/")[0] if "/" in sym else sym
                        open_symbols_binance.add(base + "USDT")
            except Exception:
                open_symbols_binance = set()

            for signal in analysis.signals[:3]:
                open_count = open_trades  # usar conteo de Binance
                if open_count >= max_trades:
                    break
                # Saltar si ya hay posición abierta de este par en Binance
                if signal.symbol in open_symbols_binance:
                    logger.info(f"Saltando {signal.symbol} — ya hay posición abierta en Binance")
                    continue
                await self.process_signal(signal, balance, snapshot)
                open_trades += 1  # actualizar conteo local

        except Exception as e:
            logger.error(f"Error en ciclo de trading: {e}")
            if self.executor and self.executor.notifications_enabled:
                self.executor.notifier.notify_critical_error(str(e))

        finally:
            duration = (datetime.now() - cycle_start).total_seconds()
            logger.info(f"─── Ciclo completado en {duration:.1f}s ───")

    async def process_signal(self, signal: TradingSignal, balance, snapshot):
        logger.info(
            f"Procesando: {signal.symbol} {signal.direction.upper()} "
            f"(score: {signal.score:.0f})"
        )

        # Obtener sentimiento de CoinGecko y noticias RSS condicionalmente
        coingecko_sentiment = None
        rss_headlines = []
        try:
            coingecko_sentiment = await self.collector.coingecko.get_news_and_sentiment(signal.symbol)
        except Exception as e:
            logger.warning(f"CoinGecko no disponible para {signal.symbol}: {e}")
        try:
            rss_headlines = await self.collector.rss.get_news_for_symbol(signal.symbol)
        except Exception as e:
            logger.warning(f"RSS no disponible para {signal.symbol}: {e}")

        decision = self.brain.decide(
            signal, snapshot, balance.operable,
            coingecko_sentiment=coingecko_sentiment,
            rss_headlines=rss_headlines
        )
        if not decision:
            logger.warning(f"Claude no pudo decidir para {signal.symbol}")
            return

        if not decision.should_trade:
            logger.info(f"Claude no opera {signal.symbol}: {decision.reason_not_trade}")
            return

        # Pasar score al decision para mensajes de error informativos
        decision.score = signal.score

        result = await self.executor.execute_decision(decision, balance)

        if result and result.success:
            # ── Registrar en DB primero para obtener trade_id ─────────────
            trade_id = self.db.open_trade(TradeRecord(
                id=None, symbol=decision.symbol,
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
                closed_at=None, exit_price=None,
                pnl_usd=None, pnl_pct=None,
                close_reason=None, order_id=result.order_id
            ))
            logger.info(f"Operación registrada en DB: ID {trade_id}")

            # ── Registrar en monitor con trade_id correcto ────────────────
            self.monitor.register(
                symbol=result.symbol,
                direction=result.direction,
                quantity=result.quantity,
                entry_price=result.entry_price,
                stop_loss=result.stop_loss,
                take_profit=result.take_profit,
                amount_usd=decision.amount_usd,
                trade_id=trade_id,
            )


    async def _restore_tracked_positions(self):
        """
        Al iniciar, carga posiciones abiertas de DB y las registra en el monitor.
        Si una posición ya no está en Binance, la cierra en DB automáticamente.
        """
        try:
            open_trades = self.db.get_open_trades()
            if not open_trades:
                logger.info("No hay posiciones abiertas en DB para restaurar")
                return

            raw_positions = await self.collector.binance.exchange.fetch_positions()
            open_in_binance = set()
            for p in raw_positions:
                if p.get("contracts") and float(p["contracts"]) > 0:
                    # Guardar tanto el símbolo ccxt como el símbolo limpio
                    open_in_binance.add(p["symbol"])
                    base = p["symbol"].split("/")[0] if "/" in p["symbol"] else p["symbol"].replace("USDT", "")
                    open_in_binance.add(base + "USDT")

            restored = 0
            for trade in open_trades:
                symbol = trade["symbol"]
                if symbol in open_in_binance:
                    entry_price = trade.get("entry_price", 0) or 0
                    amount_usd  = trade.get("amount_usd", 0) or 0
                    quantity    = amount_usd / entry_price if entry_price > 0 else 0
                    self.monitor.register(
                        symbol=symbol,
                        direction=trade.get("direction", "long"),
                        quantity=quantity,
                        entry_price=entry_price,
                        stop_loss=trade.get("stop_loss", 0) or 0,
                        take_profit=trade.get("take_profit", 0) or 0,
                        amount_usd=amount_usd,
                        trade_id=trade["id"],
                    )
                    restored += 1
                    logger.info(f"Posición restaurada: {symbol} | DB ID={trade['id']}")
                else:
                    logger.info(f"Posición {symbol} (ID {trade['id']}) no está en Binance — cerrando en DB")
                    # Intentar obtener precio real de cierre desde historial de Binance
                    exit_price = 0.0
                    pnl_usd = 0.0
                    pnl_pct = 0.0
                    try:
                        raw_sym = symbol.replace("USDT", "/USDT:USDT")
                        trades_history = await self.collector.binance.exchange.fetch_my_trades(
                            raw_sym, limit=5
                        )
                        if trades_history:
                            last_trade = trades_history[-1]
                            exit_price = float(last_trade.get("price", 0) or 0)
                            entry_p = trade.get("entry_price", 0) or 0
                            qty = trade.get("amount_usd", 0) / entry_p if entry_p > 0 else 0
                            direction = trade.get("direction", "long")
                            if exit_price > 0 and entry_p > 0 and qty > 0:
                                diff = (exit_price - entry_p) if direction == "long" else (entry_p - exit_price)
                                pnl_usd = round(diff * qty, 2)
                                pnl_pct = round((diff / entry_p) * 100, 2)
                    except Exception as e:
                        logger.warning(f"No se pudo obtener P&L real para {symbol}: {e}")
                    self.db.close_trade(
                        trade_id=trade["id"],
                        exit_price=exit_price, pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                        close_reason="cerrada_por_binance"
                    )
                    # Notificar cierre con P&L real si hay trading_executor
                    if pnl_usd != 0 and hasattr(self, 'executor') and self.executor.notifications_enabled:
                        entry_p = trade.get("entry_price", 0) or 0
                        amount_usd = trade.get("amount_usd", 0) or 0
                        opened_at_str = trade.get("opened_at", "")
                        try:
                            opened_at = datetime.fromisoformat(opened_at_str) if opened_at_str else datetime.now(timezone.utc)
                            dur_min = int((datetime.now(timezone.utc) - opened_at.replace(tzinfo=timezone.utc)).total_seconds() / 60)
                        except Exception:
                            dur_min = 0
                        import asyncio as _ai
                        _ai.ensure_future(self.executor.notify_trade_closed(
                            symbol=symbol,
                            direction=trade.get("direction", "long"),
                            pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                            duration_min=dur_min,
                            close_reason="cerrada_por_binance",
                            amount_usd=amount_usd,
                            entry_price=entry_p,
                            exit_price=exit_price,
                        ))

            logger.info(f"Posiciones restauradas: {restored}/{len(open_trades)}")

        except Exception as e:
            logger.error(f"Error restaurando posiciones: {e}")

    async def send_periodic_report(self):
        now = datetime.now().strftime("%H:%M")
        logger.info(f"Generando reporte periódico ({now})...")
        try:
            balance         = await self.executor.check_balance()
            current_balance = balance.usdt_free if balance else 0
            today           = datetime.now().strftime("%Y-%m-%d")
            summary         = self.db.get_daily_summary(today)
            self.db.save_daily_summary(
                date=today,
                starting_balance=self.executor._daily_starting_balance or current_balance,
                ending_balance=current_balance
            )
            open_positions = self.db.get_open_trades()
            # Enriquecer con precio actual de Binance
            if open_positions:
                try:
                    for pos in open_positions:
                        ticker = await self.collector.binance.exchange.fetch_ticker(pos["symbol"])
                        pos["current_price"] = float(ticker["last"])
                except Exception:
                    pass
            await self.executor.send_daily_report(current_balance, open_positions=open_positions)
            logger.info(
                f"Reporte enviado ({now}): {summary['total_trades']} operaciones | "
                f"P&L: ${summary['total_pnl_usd']:.2f}"
            )
        except Exception as e:
            logger.error(f"Error generando reporte: {e}")

    async def run(self):
        self.running = True
        try:
            balance = await self.executor.check_balance()
            if balance and self.executor.notifications_enabled:
                self.executor.notifier.notify_agent_started(
                    balance=balance.usdt_total,
                    operable=balance.operable,
                    margin_in_use=balance.margin_in_use,
                    reserve=balance.reserve,
                )
        except Exception as e:
            logger.error(f"Error enviando mensaje de inicio: {e}")

        logger.info(f"Agente corriendo — ciclo cada {LOOP_INTERVAL_MIN} minutos")
        while self.running:
            await self.run_cycle()
            await asyncio.sleep(LOOP_INTERVAL_MIN * 60)

    async def shutdown(self):
        logger.info("Deteniendo agente...")
        self.running = False
        self.scheduler.shutdown()
        await self.collector.shutdown()
        logger.info("Agente detenido correctamente")


async def main():
    agent = TradingAgent()
    loop  = asyncio.get_event_loop()

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
