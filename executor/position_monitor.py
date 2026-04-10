"""
position_monitor.py — Monitor de posiciones abiertas
Responsabilidad: en cada ciclo garantizar que las posiciones abiertas
tengan SL/TP. Si Binance no los tiene registrados, los repone.
Si el precio ya superó el SL/TP y Binance no cerró, cierra manualmente.
Notifica por Telegram cada cierre.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)


class PositionMonitor:
    """
    Uso desde main.py:
        monitor = PositionMonitor(exchange, order_executor, notifier)

        # Al abrir una posición:
        monitor.register(symbol, direction, quantity, entry_price, sl, tp)

        # En cada ciclo del agente:
        await monitor.run()
    """

    def __init__(self, exchange: ccxt.binance, order_executor=None, notifier=None):
        self.exchange       = exchange
        self.order_executor = order_executor  # para reponer SL/TP
        self.notifier       = notifier        # para notificar cierres

        # symbol → {direction, quantity, entry_price, stop_loss, take_profit, opened_at}
        self._tracked: dict = {}

    def register(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ):
        """Registra una posición recién abierta para monitoreo."""
        self._tracked[symbol] = {
            "direction":   direction,
            "quantity":    quantity,
            "entry_price": entry_price,
            "stop_loss":   stop_loss,
            "take_profit": take_profit,
            "opened_at":   datetime.now(timezone.utc),
        }
        logger.info(f"PositionMonitor: registrada {symbol} {direction.upper()} | SL=${stop_loss} TP=${take_profit}")

    async def run(self):
        """
        Ciclo de monitoreo. Llamar en cada ciclo del agente (cada 5 min).
        """
        if not self._tracked:
            return  # nada que monitorear

        try:
            # Posiciones reales abiertas en Binance
            raw_positions = await self.exchange.fetch_positions()
            open_symbols  = {
                p["symbol"]
                for p in raw_positions
                if p.get("contracts") and float(p["contracts"]) > 0
            }

            # Órdenes abiertas (SL/TP pendientes)
            open_orders   = await self.exchange.fetch_open_orders()
            orders_by_sym = {}
            for o in open_orders:
                orders_by_sym.setdefault(o["symbol"], []).append(o["type"])

            symbols_to_remove = []

            for symbol, meta in self._tracked.items():

                # ── 1. Posición ya cerrada por Binance ────────────────────
                if symbol not in open_symbols:
                    logger.info(f"PositionMonitor: {symbol} ya cerrada por Binance")
                    await self._notify_closed(symbol, meta)
                    symbols_to_remove.append(symbol)
                    continue

                # ── 2. Verificar SL/TP presentes ─────────────────────────
                existing_types = orders_by_sym.get(symbol, [])
                has_sl = any("stop" in t.lower() for t in existing_types)
                has_tp = any("take_profit" in t.lower() for t in existing_types)

                if not has_sl or not has_tp:
                    logger.warning(
                        f"PositionMonitor: {symbol} sin "
                        f"{'SL' if not has_sl else ''}"
                        f"{'/' if not has_sl and not has_tp else ''}"
                        f"{'TP' if not has_tp else ''} — reponiendo..."
                    )
                    if self.order_executor:
                        await self.order_executor.place_sl_tp(
                            symbol=symbol,
                            direction=meta["direction"],
                            quantity=meta["quantity"],
                            stop_loss=meta["stop_loss"],
                            take_profit=meta["take_profit"],
                        )
                        if self.notifier:
                            self.notifier.notify_critical_error(
                                f"⚠️ {symbol}: SL/TP faltaban y fueron repuestos automáticamente por el monitor."
                            )

                # ── 3. Cierre de emergencia si precio cruzó SL/TP ────────
                else:
                    await self._check_emergency_close(symbol, meta, symbols_to_remove)

            for sym in symbols_to_remove:
                self._tracked.pop(sym, None)

        except Exception as e:
            logger.error(f"PositionMonitor: error en ciclo de monitoreo: {e}")

    async def _check_emergency_close(self, symbol: str, meta: dict, symbols_to_remove: list):
        """
        Si el precio ya cruzó SL o TP pero Binance no ejecutó las órdenes,
        cierra la posición manualmente.
        """
        try:
            ticker        = await self.exchange.fetch_ticker(symbol)
            current_price = float(ticker["last"])
            direction     = meta["direction"]
            sl            = meta["stop_loss"]
            tp            = meta["take_profit"]

            should_close = False
            reason       = ""

            if direction == "long":
                if current_price <= sl:
                    should_close = True
                    reason       = f"Precio (${current_price:.4f}) cruzó SL (${sl:.4f})"
                elif current_price >= tp:
                    should_close = True
                    reason       = f"Precio (${current_price:.4f}) alcanzó TP (${tp:.4f})"
            else:  # short
                if current_price >= sl:
                    should_close = True
                    reason       = f"Precio (${current_price:.4f}) cruzó SL (${sl:.4f})"
                elif current_price <= tp:
                    should_close = True
                    reason       = f"Precio (${current_price:.4f}) alcanzó TP (${tp:.4f})"

            if should_close:
                logger.warning(f"PositionMonitor: cierre de emergencia {symbol} — {reason}")
                close_side = "sell" if direction == "long" else "buy"
                await self.exchange.create_order(
                    symbol=symbol, type="market", side=close_side,
                    amount=meta["quantity"],
                    params={"reduceOnly": True}
                )
                if self.notifier:
                    entry  = meta["entry_price"]
                    pnl    = (current_price - entry) if direction == "long" else (entry - current_price)
                    pnl   *= meta["quantity"]
                    pnl_pct = ((current_price - entry) / entry * 100) if direction == "long" \
                              else ((entry - current_price) / entry * 100)
                    opened  = meta["opened_at"]
                    dur_min = int((datetime.now(timezone.utc) - opened).total_seconds() / 60)
                    self.notifier.notify_trade_closed(
                        symbol=symbol,
                        direction=direction,
                        pnl_usd=round(pnl, 2),
                        pnl_pct=round(pnl_pct, 2),
                        duration_min=dur_min,
                        close_reason=reason,
                        entry_price=entry,
                        exit_price=current_price,
                    )
                symbols_to_remove.append(symbol)

        except Exception as e:
            logger.error(f"PositionMonitor: error en cierre de emergencia {symbol}: {e}")

    async def _notify_closed(self, symbol: str, meta: dict):
        """Notifica que una posición fue cerrada por Binance (SL/TP ejecutado)."""
        if not self.notifier:
            return
        try:
            ticker        = await self.exchange.fetch_ticker(symbol)
            exit_price    = float(ticker["last"])
            entry         = meta["entry_price"]
            direction     = meta["direction"]
            pnl           = (exit_price - entry) if direction == "long" else (entry - exit_price)
            pnl          *= meta["quantity"]
            pnl_pct       = ((exit_price - entry) / entry * 100) if direction == "long" \
                            else ((entry - exit_price) / entry * 100)
            opened        = meta["opened_at"]
            dur_min       = int((datetime.now(timezone.utc) - opened).total_seconds() / 60)
            self.notifier.notify_trade_closed(
                symbol=symbol,
                direction=direction,
                pnl_usd=round(pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                duration_min=dur_min,
                close_reason="SL/TP ejecutado por Binance",
                entry_price=entry,
                exit_price=exit_price,
            )
        except Exception as e:
            logger.error(f"PositionMonitor: error notificando cierre de {symbol}: {e}")
