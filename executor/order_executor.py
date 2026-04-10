"""
order_executor.py — Ejecutor de órdenes en Binance
"""

import logging
import math
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
    def __init__(self, exchange: ccxt.binance, testnet: bool = True):
        self.exchange = exchange
        self.testnet  = testnet
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

            # ── Calcular cantidad y validar mínimos ───────────────────────
            quantity = decision.amount_usd / current_price

            # Mínimo de cantidad permitida por Binance
            min_amount = float(market.get("limits", {}).get("amount", {}).get("min", 0) or 0)
            min_cost   = float(market.get("limits", {}).get("cost",   {}).get("min", 0) or 0)

            # Si la cantidad es menor al mínimo, ajustar al mínimo (no rechazar)
            if min_amount > 0 and quantity < min_amount:
                quantity = min_amount
                logger.info(f"Cantidad ajustada al mínimo de Binance: {min_amount} {market['base']}")

            # Redondear según precisión del exchange
            quantity = float(self.exchange.amount_to_precision(decision.symbol, quantity))

            # Verificar costo mínimo después del redondeo
            if min_cost > 0 and (quantity * current_price) < min_cost:
                error = (
                    f"Monto (${quantity * current_price:.2f}) menor al mínimo "
                    f"de costo de Binance (${min_cost:.2f}) para {decision.symbol}."
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

            order      = await self.exchange.create_order(
                symbol=decision.symbol, type="market", side=side, amount=quantity
            )
            order_id   = order.get("id", "unknown")
            fill_price = float(order.get("average", current_price) or current_price)

            logger.info(f"Orden ejecutada: ID {order_id} | Precio: ${fill_price:,.4f}")

            sl_tp_ok = await self.place_sl_tp(
                symbol=decision.symbol,
                direction=decision.direction,
                quantity=quantity,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
            )

            if not sl_tp_ok:
                logger.error(f"⚠️ {decision.symbol} abierta SIN SL/TP — el monitor intentará reponerlos.")

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
                error_msg=None if sl_tp_ok else "Posición abierta pero SL/TP falló"
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
        Coloca SL/TP en Binance Futuros.
        Usa timeInForce=GTE_GTC que es compatible con Cross Margin.
        """
        close_side = "sell" if direction == "long" else "buy"
        sl_ok = False
        tp_ok = False

        try:
            await self.exchange.create_order(
                symbol=symbol,
                type="STOP_MARKET",
                side=close_side,
                amount=quantity,
                params={
                    "stopPrice":    stop_loss,
                    "reduceOnly":   True,
                    "workingType":  "MARK_PRICE",
                    "timeInForce":  "GTE_GTC",
                }
            )
            logger.info(f"Stop-loss colocado: ${stop_loss:,.4f}")
            sl_ok = True
        except Exception as e:
            logger.error(f"Error colocando SL para {symbol}: {e}")

        try:
            await self.exchange.create_order(
                symbol=symbol,
                type="TAKE_PROFIT_MARKET",
                side=close_side,
                amount=quantity,
                params={
                    "stopPrice":    take_profit,
                    "reduceOnly":   True,
                    "workingType":  "MARK_PRICE",
                    "timeInForce":  "GTE_GTC",
                }
            )
            logger.info(f"Take-profit colocado: ${take_profit:,.4f}")
            tp_ok = True
        except Exception as e:
            logger.error(f"Error colocando TP para {symbol}: {e}")

        return sl_ok and tp_ok
