"""
position_monitor.py — Monitor de posiciones abiertas
"""

import logging
from datetime import datetime, timezone

import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)


class PositionMonitor:

    def __init__(self, exchange: ccxt.binance, order_executor=None, notifier=None,
                 trading_executor=None, db=None):
        self.exchange         = exchange
        self.order_executor   = order_executor
        self.notifier         = notifier
        self.trading_executor = trading_executor
        self.db               = db        # ← DB para sincronizar cierres
        self._tracked: dict   = {}

    def register(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        amount_usd: float = 0.0,
        trade_id: int = None,   # ← ID en la DB para poder cerrarla
    ):
        self._tracked[symbol] = {
            "direction":   direction,
            "quantity":    quantity,
            "entry_price": entry_price,
            "stop_loss":   stop_loss,
            "take_profit": take_profit,
            "amount_usd":  amount_usd,
            "trade_id":    trade_id,
            "opened_at":   datetime.now(timezone.utc),
        }
        logger.info(
            f"PositionMonitor: registrada {symbol} {direction.upper()} | "
            f"SL=${stop_loss} TP=${take_profit} | DB ID={trade_id}"
        )

    async def run(self):
        if not self._tracked:
            return

        try:
            raw_positions = await self.exchange.fetch_positions()
            open_symbols  = {
                p["symbol"]
                for p in raw_positions
                if p.get("contracts") and float(p["contracts"]) > 0
            }

            open_orders   = await self.exchange.fetch_open_orders()
            orders_by_sym = {}
            for o in open_orders:
                orders_by_sym.setdefault(o["symbol"], []).append(o["type"].lower())

            symbols_to_remove = []

            for symbol, meta in self._tracked.items():

                # 1. Posición cerrada en Binance → cancelar órdenes huérfanas, cerrar en DB y notificar
                if symbol not in open_symbols:
                    logger.info(f"PositionMonitor: {symbol} cerrada en Binance")
                    # Cancelar SL/TP huérfanos via Algo API para evitar posiciones involuntarias
                    await self._cancel_algo_orders(symbol)
                    await self._notify_closed(symbol, meta)
                    self._close_in_db(symbol, meta)
                    if self.trading_executor:
                        self.trading_executor.release_capital(meta.get("amount_usd", 0))
                    symbols_to_remove.append(symbol)
                    continue

                # 2. Verificar SL/TP presentes
                existing = orders_by_sym.get(symbol, [])
                has_sl   = any("stop" in t for t in existing)
                has_tp   = any("take_profit" in t for t in existing)

                if not has_sl or not has_tp:
                    logger.warning(f"PositionMonitor: {symbol} sin SL/TP — reponiendo...")
                    if self.order_executor:
                        ok = await self.order_executor.place_sl_tp(
                            symbol=symbol,
                            direction=meta["direction"],
                            quantity=meta["quantity"],
                            stop_loss=meta["stop_loss"],
                            take_profit=meta["take_profit"],
                        )
                        if self.notifier:
                            if ok:
                                self.notifier.notify_critical_error(
                                    f"sl/tp {symbol}: SL/TP repuestos automáticamente."
                                )
                            else:
                                self.notifier.notify_critical_error(
                                    f"sl/tp {symbol}: No se pudieron reponer SL/TP. "
                                    f"Cierra manualmente si es necesario."
                                )
                else:
                    await self._check_emergency_close(symbol, meta, symbols_to_remove)

            for sym in symbols_to_remove:
                self._tracked.pop(sym, None)

        except Exception as e:
            logger.error(f"PositionMonitor: error en ciclo: {e}")

    def _close_in_db(self, symbol: str, meta: dict):
        """Cierra la posición en la DB cuando Binance la cierra."""
        if not self.db:
            return
        trade_id = meta.get("trade_id")
        if not trade_id:
            # Buscar por símbolo en las abiertas
            try:
                trades = self.db.get_open_trades()
                for t in trades:
                    if t["symbol"] == symbol:
                        trade_id = t["id"]
                        break
            except Exception as e:
                logger.error(f"PositionMonitor: error buscando trade en DB: {e}")
                return

        if trade_id:
            try:
                self.db.close_trade(
                    trade_id=trade_id,
                    exit_price=0,    # el monitor no sabe el precio exacto de cierre
                    pnl_usd=0,
                    pnl_pct=0,
                    close_reason="cerrada_por_binance"
                )
                logger.info(f"PositionMonitor: trade ID {trade_id} cerrado en DB")
            except Exception as e:
                logger.error(f"PositionMonitor: error cerrando trade en DB: {e}")

    async def _check_emergency_close(self, symbol: str, meta: dict, symbols_to_remove: list):
        try:
            ticker        = await self.exchange.fetch_ticker(symbol)
            current_price = float(ticker["last"])
            direction     = meta["direction"]
            sl, tp        = meta["stop_loss"], meta["take_profit"]

            should_close = False
            reason       = ""

            if direction == "long":
                if current_price <= sl:
                    should_close, reason = True, f"Precio (${current_price:.4f}) cruzó SL (${sl:.4f})"
                elif current_price >= tp:
                    should_close, reason = True, f"Precio (${current_price:.4f}) alcanzó TP (${tp:.4f})"
            else:
                if current_price >= sl:
                    should_close, reason = True, f"Precio (${current_price:.4f}) cruzó SL (${sl:.4f})"
                elif current_price <= tp:
                    should_close, reason = True, f"Precio (${current_price:.4f}) alcanzó TP (${tp:.4f})"

            if should_close:
                logger.warning(f"PositionMonitor: cierre de emergencia {symbol} — {reason}")
                close_side = "sell" if direction == "long" else "buy"
                await self.exchange.create_order(
                    symbol=symbol, type="market", side=close_side,
                    amount=meta["quantity"], params={"reduceOnly": True}
                )
                self._close_in_db(symbol, meta)
                entry   = meta["entry_price"]
                pnl     = (current_price - entry) if direction == "long" else (entry - current_price)
                pnl    *= meta["quantity"]
                pnl_pct = ((current_price - entry) / entry * 100) if direction == "long" \
                          else ((entry - current_price) / entry * 100)
                dur_min = int((datetime.now(timezone.utc) - meta["opened_at"]).total_seconds() / 60)
                if self.trading_executor:
                    import asyncio as _ai
                    _ai.ensure_future(self.trading_executor.notify_trade_closed(
                        symbol=symbol, direction=direction,
                        pnl_usd=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                        duration_min=dur_min, close_reason=reason,
                        amount_usd=meta.get("amount_usd", 0),
                        entry_price=entry, exit_price=current_price,
                    ))
                elif self.notifier:
                    self.notifier.notify_trade_closed(
                        symbol=symbol, direction=direction,
                        pnl_usd=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                        duration_min=dur_min, close_reason=reason,
                        entry_price=entry, exit_price=current_price,
                    )
                symbols_to_remove.append(symbol)

        except Exception as e:
            logger.error(f"PositionMonitor: error en cierre de emergencia {symbol}: {e}")

    async def _cancel_algo_orders(self, symbol: str):
        """
        Cancela todas las órdenes condicionales (Algo) abiertas para un símbolo.
        Se llama cuando una posición se cierra para evitar que el SL/TP
        huérfano abra una posición contraria involuntaria.
        """
        try:
            raw_symbol = symbol.replace("/", "").replace(":USDT", "")
            # Obtener órdenes algo abiertas para este símbolo
            algo_orders = await self.exchange.fapiPrivateGetOpenAlgoOrders({"symbol": raw_symbol})
            orders = algo_orders if isinstance(algo_orders, list) else algo_orders.get("orders", [])
            cancelled = 0
            for order in orders:
                algo_id = order.get("algoId") or order.get("orderId")
                if algo_id:
                    try:
                        await self.exchange.fapiPrivateDeleteAlgoOrder({"algoId": algo_id})
                        cancelled += 1
                    except Exception as e:
                        logger.warning(f"PositionMonitor: no se pudo cancelar algo order {algo_id}: {e}")
            if cancelled > 0:
                logger.info(f"PositionMonitor: {cancelled} órdenes condicionales canceladas para {symbol}")
        except Exception as e:
            logger.warning(f"PositionMonitor: error cancelando órdenes algo para {symbol}: {e}")

    async def _notify_closed(self, symbol: str, meta: dict):
        if not self.notifier and not self.trading_executor:
            return
        try:
            ticker      = await self.exchange.fetch_ticker(symbol)
            exit_price  = float(ticker["last"])
            entry       = meta["entry_price"]
            direction   = meta["direction"]
            pnl         = (exit_price - entry) if direction == "long" else (entry - exit_price)
            pnl        *= meta["quantity"]
            pnl_pct     = ((exit_price - entry) / entry * 100) if direction == "long" \
                          else ((entry - exit_price) / entry * 100)
            dur_min     = int((datetime.now(timezone.utc) - meta["opened_at"]).total_seconds() / 60)
            if self.trading_executor:
                await self.trading_executor.notify_trade_closed(
                    symbol=symbol, direction=direction,
                    pnl_usd=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                    duration_min=dur_min, close_reason="SL/TP ejecutado por Binance",
                    amount_usd=meta.get("amount_usd", 0),
                    entry_price=entry, exit_price=exit_price,
                )
            elif self.notifier:
                self.notifier.notify_trade_closed(
                    symbol=symbol, direction=direction,
                    pnl_usd=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                    duration_min=dur_min, close_reason="SL/TP ejecutado por Binance",
                    entry_price=entry, exit_price=exit_price,
                )
        except Exception as e:
            logger.error(f"PositionMonitor: error notificando cierre de {symbol}: {e}")
