"""
main.py — Loop principal del agente de trading
v0.7.1 — MTFAligner integrado en analyzer. process_signal simplificado.
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
from analyzer.learning import LearningEngine
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
        self.learning_engine = None
        logger.info("TradingAgent v0.7.2 inicializado")

    async def initialize(self):
        logger.info("Inicializando agente...")
        self.db.initialize()
        await self.collector.initialize()
        self.learning_engine = LearningEngine(self.db)
        testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
        self.executor = TradingExecutor(exchange=self.collector.binance.exchange, testnet=testnet)
        self.monitor = PositionMonitor(
            exchange=self.collector.binance.exchange,
            order_executor=self.executor.order_executor,
            notifier=self.executor.notifier if self.executor.notifications_enabled else None,
            trading_executor=self.executor, db=self.db,
        )
        for hour in [0, 6, 12, 18]:
            self.scheduler.add_job(self.send_periodic_report, "cron", hour=hour, minute=0, id=f"report_{hour:02d}h")
        self.executor.db = self.db
        await self._restore_tracked_positions()
        self.scheduler.start()
        logger.info(f"Agente listo | Loop: cada {LOOP_INTERVAL_MIN} min | Reportes: 12am, 6am, 12pm, 6pm ({DAILY_REPORT_TZ})")

    async def run_cycle(self):
        cycle_start = datetime.now()
        logger.info(f"─── Inicio de ciclo: {cycle_start.strftime('%H:%M:%S')} ───")
        try:
            await self.monitor.run()
            balance = await self.executor.check_balance()
            if balance is None:
                logger.warning("Sin saldo o error de conexión — ciclo saltado")
                return
            logger.info(f"Saldo: {balance.summary}")
            max_trades = int(os.getenv("MAX_OPEN_TRADES", "3"))
            try:
                positions = await self.collector.binance.exchange.fetch_positions()
                open_trades = sum(1 for p in positions if p.get("contracts") and float(p["contracts"]) > 0)
            except Exception:
                open_trades = self.db.get_open_trades_count()
            if open_trades >= max_trades:
                logger.info(f"Máximo de operaciones alcanzado: {open_trades}/{max_trades} (Binance)")
                return
            snapshot = await self.collector.collect()
            if not snapshot or snapshot.has_critical_gaps:
                logger.warning("Datos insuficientes — ciclo saltado")
                return
            analysis = self.analyzer.analyze(snapshot)
            for sig in analysis.signals:
                self.db.record_signal(SignalRecord(
                    id=None, symbol=sig.symbol, direction=sig.direction, score=sig.score,
                    was_traded=False, reason_not_traded=None, detected_at=datetime.now(),
                    rsi=sig.indicators_1h.rsi.value, macd_signal=sig.indicators_1h.macd.signal,
                    volume_ratio=sig.indicators_1h.volume.ratio, trend=sig.indicators_1h.trend
                ))
            if not analysis.has_signals:
                logger.info("Sin señales válidas en este ciclo")
                return
            logger.info(f"{len(analysis.signals)} señal(es) detectada(s)")
            # v0.7.2: Guardar symbol+direction para permitir dirección opuesta
            try:
                raw_pos = await self.collector.binance.exchange.fetch_positions()
                open_positions_binance = set()
                for p in raw_pos:
                    if p.get("contracts") and float(p["contracts"]) > 0:
                        sym = p["symbol"]
                        base = sym.split("/")[0] if "/" in sym else sym
                        pair = base + "USDT"
                        contracts = float(p.get("contracts", 0))
                        direction = "long" if contracts > 0 else "short"
                        open_positions_binance.add(f"{pair}_{direction}")
            except Exception:
                open_positions_binance = set()
            for sig in analysis.signals[:3]:
                if open_trades >= max_trades:
                    break
                sig_key = f"{sig.symbol}_{sig.direction}"
                if sig_key in open_positions_binance:
                    logger.info(f"Saltando {sig.symbol} {sig.direction} — ya hay posición en misma dirección")
                    continue
                await self.process_signal(sig, balance, snapshot)
                open_trades += 1
        except Exception as e:
            logger.error(f"Error en ciclo de trading: {e}")
            if self.executor and self.executor.notifications_enabled:
                self.executor.notifier.notify_critical_error(str(e))
        finally:
            duration = (datetime.now() - cycle_start).total_seconds()
            # v0.7.2: Registrar cycle_summary
            try:
                btc_price = 0.0
                btc_dom = 0.0
                fg = 0
                regime_str = None
                balance_total = 0.0
                patterns_count = 0
                signals_count = 0
                trades_count = 0
                symbols_count = 0

                if 'snapshot' in dir() and snapshot:
                    btc_price = snapshot.market_context.btc_price if hasattr(snapshot.market_context, 'btc_price') else 0
                    btc_dom = snapshot.market_context.btc_dominance if hasattr(snapshot.market_context, 'btc_dominance') else 0
                    fg = snapshot.market_context.fear_greed_index if hasattr(snapshot.market_context, 'fear_greed_index') else 0
                if 'analysis' in dir() and analysis:
                    signals_count = len(analysis.signals)
                    symbols_count = analysis.analyzed_symbols
                if 'balance' in dir() and balance:
                    balance_total = balance.usdt_total

                self.db.record_cycle_summary({
                    "symbols_analyzed": symbols_count,
                    "patterns_detected": patterns_count,
                    "signals_generated": signals_count,
                    "trades_opened": trades_count,
                    "regime": regime_str,
                    "fear_greed": fg,
                    "btc_price": btc_price,
                    "btc_dominance": btc_dom,
                    "total_balance": balance_total,
                    "cycle_duration_sec": duration,
                })
            except Exception as e2:
                logger.debug(f"Error registrando cycle_summary: {e2}")

            logger.info(f"─── Ciclo completado en {duration:.1f}s ───")

    async def process_signal(self, signal: TradingSignal, balance, snapshot):
        logger.info(f"Procesando: {signal.symbol} {signal.direction.upper()} (score: {signal.score:.0f})")
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
        learning_context = None
        try:
            if self.learning_engine:
                learning_context = self.learning_engine.get_context()
        except Exception as e:
            logger.warning(f"Learning context no disponible: {e}")
        if learning_context is None:
            try:
                ind_1d = getattr(signal, 'indicators_1d', None)
                learning_context = self.db.get_learning_context(
                    symbol=signal.symbol, direction=signal.direction,
                    trend_1d=ind_1d.trend if ind_1d else None,
                    volume_ratio=signal.indicators_1h.volume.ratio, score=signal.score,
                )
            except Exception as e:
                logger.warning(f"Fallback learning context falló: {e}")
        mtf_alignment = getattr(signal, 'mtf_alignment', None)
        decision = self.brain.decide(
            signal, snapshot, balance.operable,
            coingecko_sentiment=coingecko_sentiment, rss_headlines=rss_headlines,
            learning_context=learning_context, mtf_alignment=mtf_alignment,
        )
        if not decision:
            logger.warning(f"Claude no pudo decidir para {signal.symbol}")
            return
        if not decision.should_trade:
            logger.info(f"Claude no opera {signal.symbol}: {decision.reason_not_trade}")
            return
        decision.score = signal.score
        result = await self.executor.execute_decision(decision, balance)
        if result and result.success:
            ind_1h = signal.indicators_1h
            ind_1d = getattr(signal, 'indicators_1d', None)
            ind_1w = getattr(signal, 'indicators_1w', None)
            patterns_list = getattr(ind_1h, 'candlestick_patterns', None) or []
            score_breakdown = f"EMA:{signal.score:.0f} Vol:{ind_1h.volume.ratio:.2f}x RSI:{ind_1h.rsi.value:.1f} MACD:{ind_1h.macd.signal}"
            pattern_type = None
            pattern_confidence = None
            breakout_quality = None
            breakout_score_val = None
            regime = None
            regime_adx = None
            projected_rr = None
            mtf_alignment_score = None
            mtf_consensus = None
            agent_stage = int(os.getenv("AGENT_STAGE", "1"))
            if mtf_alignment:
                mtf_alignment_score = mtf_alignment.alignment_score
                mtf_consensus = mtf_alignment.consensus_direction
                if mtf_alignment.best_pattern:
                    pattern_type = mtf_alignment.best_pattern.pattern_type
                    pattern_confidence = mtf_alignment.best_pattern.confidence
                if mtf_alignment.best_breakout:
                    breakout_quality = mtf_alignment.best_breakout.quality
                    breakout_score_val = mtf_alignment.best_breakout.quality_score
                if mtf_alignment.best_targets:
                    projected_rr = mtf_alignment.best_targets.risk_reward
                if mtf_alignment.regime:
                    regime = mtf_alignment.regime.regime
                    regime_adx = mtf_alignment.regime.adx
            trade_id = self.db.open_trade(TradeRecord(
                id=None, symbol=decision.symbol, direction=decision.direction,
                trading_mode=decision.trading_mode, amount_usd=decision.amount_usd,
                entry_price=result.entry_price, stop_loss=decision.stop_loss,
                take_profit=decision.take_profit, leverage=decision.leverage,
                score=signal.score, reasoning=decision.reasoning, status="open",
                opened_at=datetime.now(), closed_at=None, exit_price=None,
                pnl_usd=None, pnl_pct=None, close_reason=None, order_id=result.order_id,
                volume_ratio=ind_1h.volume.ratio, trend_1h=ind_1h.trend,
                trend_1d=ind_1d.trend if ind_1d else None,
                trend_1w=ind_1w.trend if ind_1w else None,
                patterns=",".join(patterns_list[:5]) if patterns_list else None,
                hour_opened=datetime.now(timezone.utc).hour,
                fear_greed=snapshot.market_context.fear_greed_index,
                score_breakdown=score_breakdown, balance_total=balance.usdt_total,
                balance_reserve=balance.reserve, balance_operable=balance.operable,
                sl_tp_method="algo_api", version="v0.7.1",
                pattern_type=pattern_type, pattern_confidence=pattern_confidence,
                breakout_quality=breakout_quality, breakout_score=breakout_score_val,
                regime=regime, regime_adx=regime_adx, projected_rr=projected_rr,
                mtf_alignment_score=mtf_alignment_score, mtf_consensus=mtf_consensus,
                agent_stage=agent_stage,
            ))
            logger.info(f"Operación registrada en DB: ID {trade_id}")
            self.monitor.register(
                symbol=result.symbol, direction=result.direction,
                quantity=result.quantity, entry_price=result.entry_price,
                stop_loss=result.stop_loss, take_profit=result.take_profit,
                amount_usd=decision.amount_usd, trade_id=trade_id,
            )

    async def _restore_tracked_positions(self):
        """
        Sincroniza posiciones entre Binance y BD al iniciar.
        v0.7.2 fix: 
        - Usa executor exchange para algo orders (no collector)
        - Solo cierra en BD trades que NO están en Binance
        - Compara por entry_price para distinguir trades del mismo símbolo
        - No envía notificaciones de cierre durante restore
        """
        try:
            # 1. Obtener posiciones reales de Binance
            raw_positions = await self.collector.binance.exchange.fetch_positions()
            binance_positions = {}
            for p in raw_positions:
                if p.get("contracts") and float(p["contracts"]) > 0:
                    base = p["symbol"].split("/")[0] if "/" in p["symbol"] else p["symbol"].replace("USDT", "")
                    symbol = base + "USDT"
                    binance_positions[symbol] = p

            open_in_binance = set(binance_positions.keys())
            logger.info(f"Restore: Binance tiene {len(open_in_binance)} posiciones: {open_in_binance}")

            # 2. Obtener trades abiertos en BD
            open_trades = self.db.get_open_trades()
            logger.info(f"Restore: BD tiene {len(open_trades)} trades abiertos")

            # 3. Posiciones en Binance que NO están en BD → sincronizar
            db_symbols = {t["symbol"] for t in open_trades}
            synced = 0
            for symbol, p in binance_positions.items():
                if symbol not in db_symbols:
                    try:
                        entry_price = float(p.get("entryPrice") or p.get("entry_price") or 0)
                        contracts = float(p.get("contracts", 0) or 0)
                        notional = float(p.get("notional", 0) or 0)
                        leverage = float(p.get("leverage", 1) or 1)
                        amount_usd = abs(notional) / leverage if leverage > 0 else entry_price * contracts
                        direction = "long" if float(p.get("contracts", 0)) > 0 else "short"

                        # Obtener SL/TP usando executor exchange
                        stop_loss = 0.0
                        take_profit = 0.0
                        try:
                            raw_sym = symbol.replace("USDT", "")
                            algo_result = await self.executor.order_executor.exchange.fapiPrivateGetOpenAlgoOrders({"symbol": raw_sym + "USDT"})
                            orders = algo_result if isinstance(algo_result, list) else algo_result.get("orders", [])
                            for o in orders:
                                trigger = float(o.get("triggerPrice", 0) or 0)
                                order_type = str(o.get("type", "")).lower()
                                if trigger > 0:
                                    if "stop" in order_type:
                                        stop_loss = trigger
                                    elif "take_profit" in order_type:
                                        take_profit = trigger
                        except Exception as e:
                            logger.warning(f"Restore: no se pudo obtener SL/TP para {symbol}: {e}")

                        bal = await self.executor.balance_checker.get_balance()
                        trade_id = self.db.open_trade(TradeRecord(
                            id=None, symbol=symbol, direction=direction, trading_mode="futures",
                            amount_usd=round(amount_usd, 2), entry_price=entry_price,
                            stop_loss=stop_loss, take_profit=take_profit,
                            leverage=f"{int(leverage)}x", score=0.0,
                            reasoning="Sincronizado desde Binance al iniciar",
                            status="open", opened_at=datetime.now(),
                            closed_at=None, exit_price=None, pnl_usd=None, pnl_pct=None,
                            close_reason=None, order_id=None,
                            balance_total=bal.usdt_total if bal else 0,
                            balance_reserve=bal.reserve if bal else 0,
                            balance_operable=bal.operable if bal else 0,
                            sl_tp_method="algo_api", version="v0.7.2",
                        ))
                        open_trades.append({"id": trade_id, "symbol": symbol, "direction": direction,
                                            "entry_price": entry_price, "amount_usd": amount_usd,
                                            "stop_loss": stop_loss, "take_profit": take_profit})
                        synced += 1
                        logger.info(f"Restore: {symbol} sincronizada desde Binance | DB ID={trade_id}")
                    except Exception as e:
                        logger.error(f"Restore: error sincronizando {symbol}: {e}")

            # 4. Trades abiertos en BD → registrar en monitor o cerrar
            if not open_trades:
                logger.info("Restore: no hay posiciones para restaurar")
                return

            restored = 0
            for trade in open_trades:
                symbol = trade["symbol"]

                if symbol in open_in_binance:
                    # Posición existe en Binance → registrar en monitor
                    entry_price = trade.get("entry_price", 0) or 0
                    amount_usd = trade.get("amount_usd", 0) or 0
                    quantity = amount_usd / entry_price if entry_price > 0 else 0
                    self.monitor.register(
                        symbol=symbol, direction=trade.get("direction", "long"),
                        quantity=quantity, entry_price=entry_price,
                        stop_loss=trade.get("stop_loss", 0) or 0,
                        take_profit=trade.get("take_profit", 0) or 0,
                        amount_usd=amount_usd, trade_id=trade["id"],
                    )
                    restored += 1
                    logger.info(f"Restore: {symbol} registrada en monitor | DB ID={trade['id']}")
                else:
                    # Posición NO existe en Binance → cerrar en BD silenciosamente
                    # (sin notificación — el cierre ya pasó antes del reinicio)
                    logger.info(f"Restore: {symbol} (DB ID={trade['id']}) no está en Binance — cerrando en BD")

                    # Cancelar órdenes huérfanas usando executor exchange
                    try:
                        raw_sym = symbol.replace("USDT", "")
                        algo_result = await self.executor.order_executor.exchange.fapiPrivateGetOpenAlgoOrders({"symbol": raw_sym + "USDT"})
                        orders = algo_result if isinstance(algo_result, list) else algo_result.get("orders", [])
                        cancelled = 0
                        for order in orders:
                            algo_id = order.get("algoId") or order.get("orderId")
                            if algo_id:
                                try:
                                    await self.executor.order_executor.exchange.fapiPrivateDeleteAlgoOrder({"algoId": algo_id})
                                    cancelled += 1
                                except Exception:
                                    pass
                        if cancelled > 0:
                            logger.info(f"Restore: {cancelled} órdenes huérfanas canceladas para {symbol}")
                    except Exception as e:
                        logger.warning(f"Restore: no se pudieron cancelar órdenes de {symbol}: {e}")

                    # Obtener precio real y determinar razón
                    exit_price = 0.0
                    pnl_usd = 0.0
                    pnl_pct = 0.0
                    close_reason = "cerrada_por_binance"
                    try:
                        raw_sym = symbol.replace("USDT", "/USDT:USDT")
                        trades_history = await self.collector.binance.exchange.fetch_my_trades(raw_sym, limit=10)
                        if trades_history:
                            # Buscar el trade de cierre que coincida con el entry price
                            entry_p = trade.get("entry_price", 0) or 0
                            dir_ = trade.get("direction", "long")
                            close_side = "sell" if dir_ == "long" else "buy"

                            # Filtrar trades que sean del lado de cierre
                            close_trades = [t for t in trades_history if t.get("side", "").lower() == close_side]
                            if close_trades:
                                last_close = close_trades[-1]
                                exit_price = float(last_close.get("price", 0) or 0)
                            else:
                                exit_price = float(trades_history[-1].get("price", 0) or 0)

                            qty = trade.get("amount_usd", 0) / entry_p if entry_p > 0 else 0
                            if exit_price > 0 and entry_p > 0 and qty > 0:
                                diff = (exit_price - entry_p) if dir_ == "long" else (entry_p - exit_price)
                                pnl_usd = round(diff * qty, 2)
                                pnl_pct = round((diff / entry_p) * 100, 2)

                            sl = trade.get("stop_loss", 0) or 0
                            tp = trade.get("take_profit", 0) or 0
                            if exit_price > 0 and sl > 0 and tp > 0:
                                dist_to_sl = abs(exit_price - sl)
                                dist_to_tp = abs(exit_price - tp)
                                close_reason = "stop_loss" if dist_to_sl < dist_to_tp else "take_profit"
                            elif pnl_usd > 0:
                                close_reason = "take_profit"
                            elif pnl_usd < 0:
                                close_reason = "stop_loss"
                    except Exception as e:
                        logger.warning(f"Restore: no se pudo obtener P&L para {symbol}: {e}")

                    self.db.close_trade(trade_id=trade["id"], exit_price=exit_price,
                                       pnl_usd=pnl_usd, pnl_pct=pnl_pct, close_reason=close_reason)
                    logger.info(f"Restore: {symbol} cerrada en BD — {close_reason} | P&L: ${pnl_usd:.2f}")

            logger.info(f"Restore: Sincronizadas: {synced} | Restauradas: {restored}/{len(open_trades)}")
        except Exception as e:
            logger.error(f"Error restaurando posiciones: {e}")

    async def send_periodic_report(self):
        now = datetime.now().strftime("%H:%M")
        logger.info(f"Generando reporte periódico ({now})...")
        try:
            balance = await self.executor.check_balance()
            current_balance = balance.usdt_free if balance else 0
            today = datetime.now().strftime("%Y-%m-%d")
            summary = self.db.get_daily_summary(today)
            self.db.save_daily_summary(date=today, starting_balance=self.executor._daily_starting_balance or current_balance, ending_balance=current_balance)

            # Obtener posiciones: primero BD, si vacío consultar Binance directamente
            open_positions = self.db.get_open_trades()
            if not open_positions:
                # Fallback: consultar Binance directamente
                try:
                    raw_positions = await self.collector.binance.exchange.fetch_positions()
                    for p in raw_positions:
                        if p.get("contracts") and float(p["contracts"]) > 0:
                            base = p["symbol"].split("/")[0] if "/" in p["symbol"] else p["symbol"].replace("USDT", "")
                            symbol = base + "USDT"
                            entry_price = float(p.get("entryPrice") or 0)
                            contracts = float(p.get("contracts", 0) or 0)
                            notional = float(p.get("notional", 0) or 0)
                            leverage = float(p.get("leverage", 1) or 1)
                            amount_usd = abs(notional) / leverage if leverage > 0 else entry_price * contracts
                            direction = "long" if contracts > 0 else "short"
                            open_positions.append({
                                "symbol": symbol, "direction": direction,
                                "entry_price": entry_price, "amount_usd": amount_usd,
                                "stop_loss": 0, "take_profit": 0,
                            })
                except Exception as e:
                    logger.warning(f"Reporte: no se pudieron obtener posiciones de Binance: {e}")

            if open_positions:
                try:
                    for pos in open_positions:
                        ticker = await self.collector.binance.exchange.fetch_ticker(pos["symbol"])
                        pos["current_price"] = float(ticker["last"])
                except Exception:
                    pass

            await self.executor.send_daily_report(current_balance, open_positions=open_positions)
            logger.info(f"Reporte enviado ({now}): {summary['total_trades']} operaciones | P&L: ${summary['total_pnl_usd']:.2f}")
        except Exception as e:
            logger.error(f"Error generando reporte: {e}")

    async def run(self):
        self.running = True
        try:
            balance = await self.executor.check_balance()
            if balance and self.executor.notifications_enabled:
                from collector.models import FUTURES_SYMBOLS
                self.executor.notifier.notify_agent_started(balance=balance.usdt_total, operable=balance.operable, margin_in_use=balance.margin_in_use, reserve=balance.reserve, symbols=FUTURES_SYMBOLS)
        except Exception as e:
            logger.error(f"Error enviando mensaje de inicio: {e}")
        logger.info(f"Agente v0.7.2 corriendo — ciclo cada {LOOP_INTERVAL_MIN} minutos")
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
