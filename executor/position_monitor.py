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
        self.db               = db
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
        trade_id: int = None,
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

            # ── Fix: obtener open orders POR SÍMBOLO para evitar rate limit ──
            # En vez de fetch_open_orders() sin símbolo, consultamos por cada par
            orders_by_sym = {}
            for symbol in list(self._tracked.keys()):
                try:
                    # Convertir XRPUSDT → XRP/USDT:USDT para ccxt
                    ccxt_symbol = None
                    for s in open_symbols:
                        base = s.split("/")[0] if "/" in s else s.replace("USDT", "")
                        if base + "USDT" == symbol:
                            ccxt_symbol = s
                            break
                    if not ccxt_symbol:
                        ccxt_symbol = symbol  # fallback

                    orders = await self.exchange.fetch_open_orders(ccxt_symbol)
                    orders_by_sym[symbol] = [o["type"].lower() for o in orders]
                except Exception:
                    orders_by_sym[symbol] = []
            # ──────────────────────────────────────────────────────────────────

            symbols_to_remove = []

            for symbol, meta in self._tracked.items():

                # Normalizar símbolo para comparar con open_symbols de Binance
                binance_sym = None
                for s in open_symbols:
                    base = s.split("/")[0] if "/" in s else s.replace("USDT", "")
                    if base + "USDT" == symbol:
                        binance_sym = s
                        break

                # 1. Posición cerrada en Binance
                if binance_sym not in open_symbols and symbol not in open_symbols:
                    logger.info(f"PositionMonitor: {symbol} cerrada en Binance")
                    await self._notify_closed(symbol, meta)
                    self._close_in_db(symbol, meta)
                    if self.trading_executor:
                        self.trading_executor.release_capital(meta.get("amount_usd", 0))
                    symbols_to_remove.append(symbol)
                    continue

                # 2. Verificar SL/TP — con SL/TP embebido no aparecen en open orders
                # Solo intentar reponer si hay un error explícito, no por ausencia
                existing = orders_by_sym.get(symbol, [])
                has_sl   = any("stop" in t for t in existing)
                has_tp   = any("take_profit" in t for t in existing)

                # Con método embebido Binance gestiona SL/TP internamente
                # Solo hacer cierre de emergencia si el precio cruza los niveles
                await self._check_emergency_close(symbol, meta, symbols_to_remove)

            for sym in symbols_to_remove:
                self._tracked.pop(sym, None)

        except Exception as e:
            logger.error(f"PositionMonitor: error en ciclo: {e}")

    def _close_in_db(self, symbol: str, meta: dict):
        if not self.db:
            return
        trade_id = meta.get("trade_id")
        if not trade_id:
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
                    exit_price=0,
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

            if not sl or not tp:
                return

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
                if self.trading_executor:
                    self.trading_executor.release_capital(meta.get("amount_usd", 0))
                if self.notifier:
                    entry   = meta["entry_price"]
                    pnl     = (current_price - entry) if direction == "long" else (entry - current_price)
                    pnl    *= meta["quantity"]
                    pnl_pct = ((current_price - entry) / entry * 100) if direction == "long" \
                              else ((entry - current_price) / entry * 100)
                    dur_min = int((datetime.now(timezone.utc) - meta["opened_at"]).total_seconds() / 60)
                    self.notifier.notify_trade_closed(
                        symbol=symbol, direction=direction,
                        pnl_usd=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                        duration_min=dur_min, close_reason=reason,
                        entry_price=entry, exit_price=current_price,
                    )
                symbols_to_remove.append(symbol)

        except Exception as e:
            logger.error(f"PositionMonitor: error en cierre de emergencia {symbol}: {e}")

    async def _notify_closed(self, symbol: str, meta: dict):
        if not self.notifier:
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
            self.notifier.notify_trade_closed(
                symbol=symbol, direction=direction,
                pnl_usd=round(pnl, 2), pnl_pct=round(pnl_pct, 2),
                duration_min=dur_min, close_reason="SL/TP ejecutado por Binance",
                entry_price=entry, exit_price=exit_price,
            )
        except Exception as e:
            logger.error(f"PositionMonitor: error notificando cierre de {symbol}: {e}")
