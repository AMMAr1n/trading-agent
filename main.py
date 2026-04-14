"""
main.py — Loop principal del agente de trading
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone

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
        # Pasar referencia de DB al executor para el reporte periódico
        self.executor.db = self.db
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

        # Consultar historial de aprendizaje desde BD
        learning_context = None
        try:
            ind_1d = getattr(signal, 'indicators_1d', None)
            learning_context = self.db.get_learning_context(
                symbol=signal.symbol,
                direction=signal.direction,
                trend_1d=ind_1d.trend if ind_1d else None,
                volume_ratio=signal.indicators_1h.volume.ratio,
                score=signal.score,
            )
        except Exception as e:
            logger.warning(f"No se pudo obtener contexto de aprendizaje: {e}")

        decision = self.brain.decide(
            signal, snapshot, balance.operable,
            coingecko_sentiment=coingecko_sentiment,
            rss_headlines=rss_headlines,
            learning_context=learning_context,
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
            # Extraer contexto de la señal para el pipeline de aprendizaje
            ind_1h  = signal.indicators_1h
            ind_1d  = getattr(signal, 'indicators_1d', None)
            ind_1w  = getattr(signal, 'indicators_1w', None)
            patterns_list = getattr(ind_1h, 'candlestick_patterns', None) or []
            score_breakdown = (
                f"EMA:{signal.score:.0f} Vol:{ind_1h.volume.ratio:.2f}x "
                f"RSI:{ind_1h.rsi.value:.1f} MACD:{ind_1h.macd.signal}"
            )
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
                close_reason=None, order_id=result.order_id,
                volume_ratio=ind_1h.volume.ratio,
                trend_1h=ind_1h.trend,
                trend_1d=ind_1d.trend if ind_1d else None,
                trend_1w=ind_1w.trend if ind_1w else None,
                patterns=",".join(patterns_list[:5]) if patterns_list else None,
                hour_opened=datetime.now(timezone.utc).hour,
                fear_greed=snapshot.market_context.fear_greed_index,
                score_breakdown=score_breakdown,
                balance_total=balance.usdt_total,
                balance_reserve=balance.reserve,
                balance_operable=balance.operable,
                sl_tp_method="algo_api",
                version="v0.6.0",
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
        Si una posición ya no está en Binance, la cierra en DB.
        Si hay posiciones en Binance que no están en DB, las sincroniza automáticamente.
        """
        try:
            raw_positions = await self.collector.binance.exchange.fetch_positions()
            binance_positions = {}
            for p in raw_positions:
                if p.get("contracts") and float(p["contracts"]) > 0:
                    base = p["symbol"].split("/")[0] if "/" in p["symbol"] else p["symbol"].replace("USDT", "")
                    symbol = base + "USDT"
                    binance_positions[symbol] = p

            open_in_binance = set(binance_positions.keys())

            open_trades = self.db.get_open_trades()
            db_symbols  = {t["symbol"] for t in open_trades}

            # ── Sincronizar posiciones de Binance que no están en DB ──────────
            synced = 0
            for symbol, p in binance_positions.items():
                if symbol not in db_symbols:
                    try:
                        entry_price = float(p.get("entryPrice") or p.get("entry_price") or 0)
                        contracts   = float(p.get("contracts", 0) or 0)
                        notional    = float(p.get("notional", 0) or 0)
                        leverage    = float(p.get("leverage", 1) or 1)
                        amount_usd  = abs(notional) / leverage if leverage > 0 else entry_price * contracts
                        direction   = "long" if float(p.get("contracts", 0)) > 0 else "short"

                        # Consultar SL/TP desde órdenes condicionales (Algo API)
                        stop_loss   = 0.0
                        take_profit = 0.0
                        try:
                            raw_sym = symbol.replace("USDT", "")
                            algo_orders = await self.collector.binance.exchange.fapiPrivateGetOpenAlgoOrders(
                                {"symbol": symbol}
                            )
                            orders = algo_orders if isinstance(algo_orders, list) else algo_orders.get("orders", [])
                            for o in orders:
                                trigger = float(o.get("triggerPrice", 0) or 0)
                                order_type = str(o.get("type", "")).lower()
                                side = str(o.get("side", "")).lower()
                                if trigger > 0:
                                    if "stop" in order_type or side == "sell" and trigger < entry_price:
                                        stop_loss = trigger
                                    elif "take_profit" in order_type or side == "sell" and trigger > entry_price:
                                        take_profit = trigger
                            if stop_loss > 0 or take_profit > 0:
                                logger.info(f"SL/TP obtenidos desde Algo API para {symbol}: SL=${stop_loss} TP=${take_profit}")
                        except Exception as e:
                            logger.warning(f"No se pudo obtener SL/TP para {symbol}: {e}")

                        balance  = await self.executor.balance_checker.get_balance()
                        trade_id = self.db.open_trade(TradeRecord(
                            id=None, symbol=symbol,
                            direction=direction,
                            trading_mode="futures",
                            amount_usd=round(amount_usd, 2),
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            leverage=f"{int(leverage)}x",
                            score=0.0,
                            reasoning="Sincronizado desde Binance al iniciar",
                            status="open",
                            opened_at=datetime.now(),
                            closed_at=None, exit_price=None,
                            pnl_usd=None, pnl_pct=None,
                            close_reason=None,
                            order_id=None,
                            balance_total=balance.usdt_total if balance else 0,
                            balance_reserve=balance.reserve if balance else 0,
                            balance_operable=balance.operable if balance else 0,
                            sl_tp_method="algo_api",
                            version="v0.6.0",
                        ))
                        open_trades.append({"id": trade_id, "symbol": symbol,
                                            "direction": direction,
                                            "entry_price": entry_price,
                                            "amount_usd": amount_usd,
                                            "stop_loss": stop_loss,
                                            "take_profit": take_profit})
                        synced += 1
                        logger.info(f"Posición sincronizada desde Binance: {symbol} | DB ID={trade_id}")
                    except Exception as e:
                        logger.error(f"Error sincronizando {symbol} desde Binance: {e}")
            # ──────────────────────────────────────────────────────────────────

            if not open_trades:
                logger.info("No hay posiciones abiertas en DB para restaurar")
                return

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
                    # Intentar obtener P&L real desde historial de Binance
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
                    if pnl_usd != 0 and self.executor.notifications_enabled:
                        entry_p = trade.get("entry_price", 0) or 0
                        amount_usd = trade.get("amount_usd", 0) or 0
                        import asyncio as _ai
                        _ai.ensure_future(self.executor.notify_trade_closed(
                            symbol=symbol,
                            direction=trade.get("direction", "long"),
                            pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                            duration_min=0,
                            close_reason="cerrada_por_binance",
                            amount_usd=amount_usd,
                            entry_price=entry_p,
                            exit_price=exit_price,
                        ))

            logger.info(f"Sincronizadas: {synced} | Restauradas: {restored}/{len(open_trades)}")

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
