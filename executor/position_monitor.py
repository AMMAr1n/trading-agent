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
        # v0.8.0: barrido defensivo siempre, incluso si _tracked está vacío
        # Esto detecta órdenes huérfanas de cierres previos que no fueron limpiados
        await self.sweep_orphan_algo_orders()

        if not self._tracked:
            return

        try:
            raw_positions = await self.exchange.fetch_positions()
            # Normalizar símbolos: ccxt retorna "XRP/USDT:USDT", nosotros usamos "XRPUSDT"
            open_symbols = set()
            for p in raw_positions:
                if p.get("contracts") and float(p["contracts"]) > 0:
                    sym = p["symbol"]
                    # Normalizar: "XRP/USDT:USDT" → "XRPUSDT"
                    base = sym.split("/")[0] if "/" in sym else sym.replace("USDT", "")
                    normalized = base + "USDT"
                    open_symbols.add(normalized)

            # Obtener órdenes abiertas POR SÍMBOLO (no global — Binance penaliza)
            orders_by_sym = {}
            for symbol in list(self._tracked.keys()):
                try:
                    sym_orders = await self.exchange.fetch_open_orders(symbol)
                    for o in sym_orders:
                        orders_by_sym.setdefault(o["symbol"], []).append(o["type"].lower())
                except Exception:
                    pass  # Si falla un símbolo, continuar con los demás

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

                # 2. Verificar SL/TP
                # Nuestros SL/TP están en la Algo API (condicionales), NO en órdenes regulares.
                # fetch_open_orders no las ve. Si el monitor tiene SL/TP registrados, confiar.
                has_registered_sl = meta.get("stop_loss", 0) > 0
                has_registered_tp = meta.get("take_profit", 0) > 0

                if has_registered_sl and has_registered_tp:
                    # SL/TP registrados — solo verificar emergency close
                    await self._check_emergency_close(symbol, meta, symbols_to_remove)
                else:
                    # Sin SL/TP — intentar reponer
                    logger.warning(f"PositionMonitor: {symbol} sin SL/TP registrados — reponiendo...")
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
                # Solo cerrar en DB si no fue ya actualizado por _notify_closed
                from database.database import TradingDatabase
                trades = self.db.get_open_trades()
                still_open = any(t["id"] == trade_id for t in trades)
                if still_open:
                    self.db.close_trade(
                        trade_id=trade_id,
                        exit_price=0, pnl_usd=0, pnl_pct=0,
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
        Usa order_executor.exchange que tiene los métodos fapiPrivate*.
        v0.8.0 fix: incluye symbol en el DELETE (requerido por Binance) y
        notifica por Telegram si la cancelación falla para evitar huérfanas silenciosas.
        """
        if not self.order_executor:
            logger.warning(f"PositionMonitor: no hay order_executor para cancelar órdenes de {symbol}")
            return
        try:
            raw_symbol = symbol.replace("/", "").replace(":USDT", "")
            algo_orders = await self.order_executor.exchange.fapiPrivateGetOpenAlgoOrders({"symbol": raw_symbol})
            orders = algo_orders if isinstance(algo_orders, list) else algo_orders.get("orders", [])
            logger.info(f"PositionMonitor: {symbol} tiene {len(orders)} órden(es) algo abierta(s) para cancelar")
            cancelled = 0
            failed = 0
            for order in orders:
                algo_id = order.get("algoId") or order.get("orderId")
                if algo_id:
                    try:
                        # Binance requiere symbol + algoId para cancelar
                        await self.order_executor.exchange.fapiPrivateDeleteAlgoOrder({
                            "symbol": raw_symbol,
                            "algoId": algo_id,
                        })
                        cancelled += 1
                        logger.info(f"PositionMonitor: algo order {algo_id} cancelada para {symbol}")
                    except Exception as e:
                        failed += 1
                        logger.error(f"PositionMonitor: NO se pudo cancelar algo order {algo_id} para {symbol}: {e}")
            if cancelled > 0:
                logger.info(f"PositionMonitor: {cancelled} órden(es) condicional(es) cancelada(s) para {symbol}")
            # Si hubo fallos, notificar por Telegram para que el usuario cancele manualmente
            if failed > 0 and self.notifier:
                try:
                    self.notifier.notify_critical_error(
                        f"huerfana {symbol}: {failed} orden(es) algo NO cancelada(s) automáticamente. "
                        f"Revisa 'Open Orders > Conditional' en Binance y cancela manualmente."
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"PositionMonitor: error cancelando órdenes algo para {symbol}: {e}")
            if self.notifier:
                try:
                    self.notifier.notify_critical_error(
                        f"huerfana {symbol}: fallo al consultar órdenes algo. Revisa Open Orders manualmente."
                    )
                except Exception:
                    pass

    async def sweep_orphan_algo_orders(self):
        """
        Barrido defensivo: detecta órdenes algo abiertas cuyo símbolo ya NO tiene
        posición abierta en Binance, y las cancela. Protege contra órdenes huérfanas
        dejadas por cierres anteriores que no fueron limpiados.
        Se ejecuta al inicio del agente y cada ciclo del monitor.
        v0.8.0 fix: usa order_executor.exchange (tiene métodos fapiPrivate*)
        """
        if not self.order_executor:
            return
        try:
            # 1. Obtener símbolos con posición abierta en Binance (usando executor exchange)
            raw_positions = await self.order_executor.exchange.fetch_positions()
            open_symbols_raw = set()
            for p in raw_positions:
                if p.get("contracts") and float(p["contracts"]) > 0:
                    sym = p["symbol"]
                    base = sym.split("/")[0] if "/" in sym else sym.replace("USDT", "")
                    open_symbols_raw.add(base + "USDT")

            # 2. Obtener TODAS las órdenes algo abiertas (sin filtrar por símbolo)
            algo_result = await self.order_executor.exchange.fapiPrivateGetOpenAlgoOrders({})
            orders = algo_result if isinstance(algo_result, list) else algo_result.get("orders", [])

            # 3. Agrupar por símbolo
            orders_by_symbol: dict = {}
            for o in orders:
                sym = o.get("symbol", "")
                if sym:
                    orders_by_symbol.setdefault(sym, []).append(o)

            # 4. Cancelar órdenes de símbolos SIN posición abierta
            total_cancelled = 0
            for sym, sym_orders in orders_by_symbol.items():
                if sym not in open_symbols_raw:
                    logger.warning(
                        f"PositionMonitor: {sym} tiene {len(sym_orders)} orden(es) algo huérfana(s) — cancelando"
                    )
                    for order in sym_orders:
                        algo_id = order.get("algoId") or order.get("orderId")
                        if algo_id:
                            try:
                                await self.order_executor.exchange.fapiPrivateDeleteAlgoOrder({
                                    "symbol": sym,
                                    "algoId": algo_id,
                                })
                                total_cancelled += 1
                                logger.info(f"PositionMonitor: huérfana {algo_id} de {sym} cancelada")
                            except Exception as e:
                                logger.error(f"PositionMonitor: no se pudo cancelar huérfana {algo_id} de {sym}: {e}")

            if total_cancelled > 0:
                logger.info(f"PositionMonitor: barrido completado — {total_cancelled} huérfana(s) cancelada(s)")
                if self.notifier:
                    try:
                        self.notifier.notify_critical_error(
                            f"huerfanas: {total_cancelled} orden(es) algo huérfana(s) canceladas automáticamente."
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"PositionMonitor: error en barrido de huérfanas: {e}")

    async def _notify_closed(self, symbol: str, meta: dict):
        if not self.notifier and not self.trading_executor:
            return
        try:
            entry     = meta["entry_price"]
            direction = meta["direction"]
            quantity  = meta["quantity"]

            # Obtener precio real de cierre desde historial de Binance
            exit_price  = 0.0
            close_reason = "SL/TP ejecutado por Binance"
            try:
                raw_sym = symbol.replace("USDT", "/USDT:USDT")
                trades_history = await self.exchange.fetch_my_trades(raw_sym, limit=5)
                if trades_history:
                    last = trades_history[-1]
                    exit_price = float(last.get("price", 0) or 0)
                    # Determinar si fue TP o SL comparando distancias
                    sl = meta.get("stop_loss", 0)
                    tp = meta.get("take_profit", 0)
                    if exit_price > 0 and sl > 0 and tp > 0:
                        dist_to_sl = abs(exit_price - sl)
                        dist_to_tp = abs(exit_price - tp)
                        if dist_to_sl < dist_to_tp:
                            close_reason = "stop_loss"
                        else:
                            close_reason = "take_profit"
                    elif exit_price > 0 and entry > 0:
                        # Fallback: si no hay SL/TP, usar P&L
                        diff = (exit_price - entry) if direction == "long" else (entry - exit_price)
                        close_reason = "take_profit" if diff > 0 else "stop_loss"
            except Exception as e:
                logger.warning(f"PositionMonitor: no se pudo obtener precio de cierre real para {symbol}: {e}")

            # Fallback al precio actual si no se obtuvo el real
            if exit_price == 0:
                try:
                    ticker = await self.exchange.fetch_ticker(symbol)
                    exit_price = float(ticker["last"])
                except Exception:
                    exit_price = entry  # último recurso

            # Calcular P&L con precio real
            diff    = (exit_price - entry) if direction == "long" else (entry - exit_price)
            pnl     = round(diff * quantity, 2)
            pnl_pct = round((diff / entry) * 100, 2) if entry > 0 else 0.0
            dur_min = int((datetime.now(timezone.utc) - meta["opened_at"]).total_seconds() / 60)

            # Actualizar DB con precio real y P&L
            if self.db:
                trade_id = meta.get("trade_id")
                if trade_id:
                    try:
                        self.db.close_trade(
                            trade_id=trade_id,
                            exit_price=exit_price,
                            pnl_usd=pnl,
                            pnl_pct=pnl_pct,
                            close_reason=close_reason,
                        )
                    except Exception as e:
                        logger.error(f"PositionMonitor: error actualizando DB con P&L real: {e}")

            logger.info(f"PositionMonitor: {symbol} cerrada | Precio: ${exit_price:.4f} | P&L: ${pnl:.2f} | Razón: {close_reason}")

            if self.trading_executor:
                await self.trading_executor.notify_trade_closed(
                    symbol=symbol, direction=direction,
                    pnl_usd=pnl, pnl_pct=pnl_pct,
                    duration_min=dur_min, close_reason=close_reason,
                    amount_usd=meta.get("amount_usd", 0),
                    entry_price=entry, exit_price=exit_price,
                )
            elif self.notifier:
                self.notifier.notify_trade_closed(
                    symbol=symbol, direction=direction,
                    pnl_usd=pnl, pnl_pct=pnl_pct,
                    duration_min=dur_min, close_reason=close_reason,
                    entry_price=entry, exit_price=exit_price,
                )
        except Exception as e:
            logger.error(f"PositionMonitor: error notificando cierre de {symbol}: {e}")
