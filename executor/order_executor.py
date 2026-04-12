"""
order_executor.py — Ejecutor de órdenes en Binance
"""

import logging
from dataclasses import dataclass
from typing import Optional

import ccxt.async_support as ccxt

from brain.decision import TradeDecision

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    symbol: str
    direction: str
    amount_usd: float
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float
    error_msg: Optional[str]


class OrderExecutor:
    def __init__(self, exchange: ccxt.binance, testnet: bool = True, notifier=None):
        self.exchange = exchange
        self.testnet  = testnet
        self.notifier = notifier  # para notificar errores de SL/TP
        logger.info(f"OrderExecutor inicializado — Modo: {'TESTNET' if testnet else 'PRODUCCION'}")

    async def execute(self, decision: TradeDecision) -> OrderResult:
        if not decision.should_trade:
            return OrderResult(
                success=False, order_id=None,
                symbol=decision.symbol, direction=decision.direction,
                amount_usd=decision.amount_usd, entry_price=0,
                stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                quantity=0, error_msg="Claude decidió no operar"
            )

        try:
            ticker        = await self.exchange.fetch_ticker(decision.symbol)
            current_price = float(ticker["last"])
            market        = self.exchange.market(decision.symbol)

            # ── Calcular cantidad ─────────────────────────────────────────
            quantity   = decision.amount_usd / current_price
            min_amount = float(market.get("limits", {}).get("amount", {}).get("min", 0) or 0)
            min_cost   = float(market.get("limits", {}).get("cost",   {}).get("min", 0) or 0)

            if min_amount > 0 and quantity < min_amount:
                quantity = min_amount
                logger.info(f"Cantidad ajustada al mínimo: {min_amount} {market['base']}")

            quantity = float(self.exchange.amount_to_precision(decision.symbol, quantity))

            if min_cost > 0 and (quantity * current_price) < min_cost:
                error = (
                    f"Monto (${quantity * current_price:.2f}) menor al mínimo "
                    f"de Binance (${min_cost:.2f}) para {decision.symbol}."
                )
                logger.warning(error)
                return OrderResult(
                    success=False, order_id=None,
                    symbol=decision.symbol, direction=decision.direction,
                    amount_usd=decision.amount_usd, entry_price=0,
                    stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                    quantity=0, error_msg=error
                )
            # ──────────────────────────────────────────────────────────────

            side = "buy" if decision.direction == "long" else "sell"
            logger.info(
                f"Ejecutando: {decision.symbol} {side.upper()} "
                f"{quantity} @ ${current_price:,.4f} (${decision.amount_usd:.2f} USD)"
            )

            if decision.trading_mode == "futures":
                leverage = int(decision.leverage.replace("x", ""))
                await self.exchange.set_leverage(leverage, decision.symbol)

            # ── Abrir posición (sin SL/TP embebido — no funciona en ccxt futures) ──
            order = await self.exchange.create_order(
                symbol=decision.symbol,
                type="market",
                side=side,
                amount=quantity,
            )
            # ──────────────────────────────────────────────────────────────

            order_id   = order.get("id", "unknown")
            fill_price = float(order.get("average", current_price) or current_price)

            logger.info(f"Posición abierta: ID {order_id} | Precio: ${fill_price:,.4f}")

            # ── Colocar SL/TP via Algo API oficial de Binance ────────────
            import asyncio as _asyncio
            await _asyncio.sleep(1)  # dar tiempo a Binance para registrar la posición

            sl_tp_ok = await self.place_sl_tp(
                symbol=decision.symbol,
                direction=decision.direction,
                quantity=quantity,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
            )

            if not sl_tp_ok:
                # SL/TP falló — cerrar posición por seguridad
                logger.error(f"SL/TP fallido — cerrando {decision.symbol} por seguridad")
                close_side_emerg = "sell" if decision.direction == "long" else "buy"
                await self.exchange.create_order(
                    symbol=decision.symbol, type="market",
                    side=close_side_emerg, amount=quantity,
                    params={"reduceOnly": True}
                )
                if self.notifier:
                    self.notifier.send(
                        f"🚨 <b>POSICIÓN CERRADA POR SEGURIDAD — {decision.symbol}</b>\n"
                        f"No fue posible colocar SL/TP via Algo API.\n"
                        f"La posición fue cerrada automáticamente para evitar pérdidas sin límite.\n"
                        f"Precio de entrada: ${fill_price:,.4f} | Cierre inmediato."
                    )
                return OrderResult(
                    success=False, order_id=order_id,
                    symbol=decision.symbol, direction=decision.direction,
                    amount_usd=decision.amount_usd, entry_price=fill_price,
                    stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                    quantity=quantity,
                    error_msg="SL/TP falló — posición cerrada por seguridad"
                )

            logger.info(f"SL/TP colocados: SL=${decision.stop_loss} | TP=${decision.take_profit}")
            # ──────────────────────────────────────────────────────────────

            return OrderResult(
                success=True,
                order_id=order_id,
                symbol=decision.symbol,
                direction=decision.direction,
                amount_usd=decision.amount_usd,
                entry_price=fill_price,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                quantity=quantity,
                error_msg=None
            )

        except ccxt.InsufficientFunds:
            error = "Fondos insuficientes en Binance"
            logger.error(error)
            return OrderResult(
                success=False, order_id=None,
                symbol=decision.symbol, direction=decision.direction,
                amount_usd=decision.amount_usd, entry_price=0,
                stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                quantity=0, error_msg=error
            )
        except Exception as e:
            error = f"Error ejecutando orden: {e}"
            logger.error(error)
            return OrderResult(
                success=False, order_id=None,
                symbol=decision.symbol, direction=decision.direction,
                amount_usd=decision.amount_usd, entry_price=0,
                stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                quantity=0, error_msg=error
            )

    async def place_sl_tp(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        stop_loss: float,
        take_profit: float,
    ) -> bool:
        """
        Coloca SL/TP via POST /fapi/v1/algoOrder (Algo API oficial de Binance).
        Desde 2025-12-09 es el único endpoint válido para órdenes condicionales
        en Futuros USD-M. Parámetros según documentación oficial de Binance.
        """
        # En One-way Mode: close_side es opuesto a la dirección de la posición
        close_side = "SELL" if direction == "long" else "BUY"
        raw_symbol = symbol.replace("/", "").replace(":USDT", "")
        sl_ok = False
        tp_ok = False

        # ── Stop Loss via Algo API ────────────────────────────────────────
        try:
            await self.exchange.fapiPrivatePostAlgoOrder({
                "algoType":    "CONDITIONAL",
                "symbol":      raw_symbol,
                "side":        close_side,
                "positionSide": "BOTH",
                "type":        "STOP_MARKET",
                "quantity":    quantity,
                "triggerPrice": stop_loss,
                "workingType": "MARK_PRICE",
                "reduceOnly":  "true",
                "timeInForce": "GTC",
            })
            logger.info(f"SL colocado via Algo API: ${stop_loss:,.4f}")
            sl_ok = True
        except Exception as e:
            logger.error(f"Error colocando SL para {symbol}: {e}")

        # ── Take Profit via Algo API ──────────────────────────────────────
        try:
            await self.exchange.fapiPrivatePostAlgoOrder({
                "algoType":    "CONDITIONAL",
                "symbol":      raw_symbol,
                "side":        close_side,
                "positionSide": "BOTH",
                "type":        "TAKE_PROFIT_MARKET",
                "quantity":    quantity,
                "triggerPrice": take_profit,
                "workingType": "MARK_PRICE",
                "reduceOnly":  "true",
                "timeInForce": "GTC",
            })
            logger.info(f"TP colocado via Algo API: ${take_profit:,.4f}")
            tp_ok = True
        except Exception as e:
            logger.error(f"Error colocando TP para {symbol}: {e}")

        return sl_ok and tp_ok
