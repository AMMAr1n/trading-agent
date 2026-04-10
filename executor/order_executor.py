"""
order_executor.py — Ejecutor de órdenes en Binance
Responsabilidad: abrir y cerrar posiciones en Binance
según las decisiones de Claude.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import ccxt.async_support as ccxt

from brain.decision import TradeDecision

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    """Resultado de una orden ejecutada en Binance."""
    success: bool
    order_id: Optional[str]
    symbol: str
    direction: str
    amount_usd: float
    entry_price: float
    stop_loss: float
    take_profit: float
    error_msg: Optional[str]


class OrderExecutor:
    """
    Ejecuta órdenes en Binance según la decisión de Claude.

    En TESTNET opera con dinero simulado — seguro para pruebas.
    En PRODUCCION opera con dinero real.
    """

    def __init__(self, exchange: ccxt.binance, testnet: bool = True):
        self.exchange = exchange
        self.testnet = testnet
        logger.info(
            f"OrderExecutor inicializado — "
            f"Modo: {'TESTNET (simulado)' if testnet else 'PRODUCCION (real)'}"
        )

    async def execute(self, decision: TradeDecision) -> OrderResult:
        """
        Ejecuta la decisión de Claude en Binance.
        Abre la posición con stop-loss y take-profit automáticos.
        """
        if not decision.should_trade:
            return OrderResult(
                success=False,
                order_id=None,
                symbol=decision.symbol,
                direction=decision.direction,
                amount_usd=decision.amount_usd,
                entry_price=0,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                error_msg="Claude decidió no operar"
            )

        try:
            # Obtener precio actual
            ticker = await self.exchange.fetch_ticker(decision.symbol)
            current_price = float(ticker["last"])

            # Calcular cantidad en la moneda base
            quantity = decision.amount_usd / current_price

            # Redondear según las reglas del exchange
            market = self.exchange.market(decision.symbol)
            quantity = float(self.exchange.amount_to_precision(
                decision.symbol, quantity
            ))

            # ── VALIDACIÓN DE MÍNIMOS ──────────────────────────────────────
            # Binance rechaza órdenes por debajo del mínimo permitido
            min_amount = float(market.get("limits", {}).get("amount", {}).get("min", 0) or 0)
            min_cost   = float(market.get("limits", {}).get("cost",   {}).get("min", 0) or 0)

            if min_amount > 0 and quantity < min_amount:
                error = (
                    f"Cantidad calculada ({quantity} {market['base']}) "
                    f"es menor al mínimo de Binance ({min_amount}). "
                    f"Necesitas al menos ${min_amount * current_price:.2f} USD para operar {decision.symbol}."
                )
                logger.warning(error)
                return OrderResult(
                    success=False, order_id=None,
                    symbol=decision.symbol, direction=decision.direction,
                    amount_usd=decision.amount_usd, entry_price=0,
                    stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                    error_msg=error
                )

            if min_cost > 0 and (quantity * current_price) < min_cost:
                error = (
                    f"Monto calculado (${quantity * current_price:.2f} USD) "
                    f"es menor al mínimo de costo de Binance (${min_cost:.2f} USD) "
                    f"para {decision.symbol}."
                )
                logger.warning(error)
                return OrderResult(
                    success=False, order_id=None,
                    symbol=decision.symbol, direction=decision.direction,
                    amount_usd=decision.amount_usd, entry_price=0,
                    stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                    error_msg=error
                )
            # ──────────────────────────────────────────────────────────────

            # Determinar lado de la orden
            side = "buy" if decision.direction == "long" else "sell"

            logger.info(
                f"Ejecutando orden: {decision.symbol} {side.upper()} "
                f"{quantity} @ ${current_price:,.4f} "
                f"(${decision.amount_usd:.2f} USD)"
            )

            if decision.trading_mode == "futures":
                # Configurar apalancamiento
                leverage = int(decision.leverage.replace("x", ""))
                await self.exchange.set_leverage(leverage, decision.symbol)

                # Orden de mercado en futuros
                order = await self.exchange.create_order(
                    symbol=decision.symbol,
                    type="market",
                    side=side,
                    amount=quantity,
                )
            else:
                # Orden de mercado en spot
                order = await self.exchange.create_order(
                    symbol=decision.symbol,
                    type="market",
                    side=side,
                    amount=quantity,
                )

            order_id = order.get("id", "unknown")
            fill_price = float(order.get("average", current_price) or current_price)

            logger.info(
                f"Orden ejecutada: ID {order_id} | "
                f"Precio: ${fill_price:,.4f}"
            )

            # Colocar stop-loss y take-profit
            sl_tp_ok = await self._place_sl_tp(decision, quantity, fill_price)
            if not sl_tp_ok:
                logger.error(
                    f"⚠️  Posición abierta en {decision.symbol} SIN SL/TP — "
                    f"ciérrala manualmente en Binance si es necesario."
                )

            return OrderResult(
                success=True,
                order_id=order_id,
                symbol=decision.symbol,
                direction=decision.direction,
                amount_usd=decision.amount_usd,
                entry_price=fill_price,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                error_msg=None if sl_tp_ok else "Posición abierta pero SL/TP falló — sin protección automática"
            )

        except ccxt.InsufficientFunds:
            error = "Fondos insuficientes en Binance para esta operacion"
            logger.error(error)
            return OrderResult(
                success=False, order_id=None,
                symbol=decision.symbol, direction=decision.direction,
                amount_usd=decision.amount_usd, entry_price=0,
                stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                error_msg=error
            )
        except Exception as e:
            error = f"Error ejecutando orden: {e}"
            logger.error(error)
            return OrderResult(
                success=False, order_id=None,
                symbol=decision.symbol, direction=decision.direction,
                amount_usd=decision.amount_usd, entry_price=0,
                stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                error_msg=error
            )

    async def _place_sl_tp(
        self,
        decision: TradeDecision,
        quantity: float,
        entry_price: float
    ) -> bool:
        """
        Coloca las órdenes de stop-loss y take-profit después de entrar.
        Retorna True si ambas se colocaron correctamente, False si alguna falló.
        """
        close_side = "sell" if decision.direction == "long" else "buy"
        sl_ok = False
        tp_ok = False

        # ── STOP-LOSS ──────────────────────────────────────────────────────
        try:
            await self.exchange.create_order(
                symbol=decision.symbol,
                type="stop_market",
                side=close_side,
                amount=quantity,
                params={
                    "stopPrice": decision.stop_loss,
                    "reduceOnly": True,
                }
            )
            logger.info(f"Stop-loss colocado: ${decision.stop_loss:,.4f}")
            sl_ok = True
        except Exception as e:
            logger.error(f"Error colocando stop-loss para {decision.symbol}: {e}")

        # ── TAKE-PROFIT ────────────────────────────────────────────────────
        try:
            await self.exchange.create_order(
                symbol=decision.symbol,
                type="take_profit_market",
                side=close_side,
                amount=quantity,
                params={
                    "stopPrice": decision.take_profit,
                    "reduceOnly": True,
                }
            )
            logger.info(f"Take-profit colocado: ${decision.take_profit:,.4f}")
            tp_ok = True
        except Exception as e:
            logger.error(f"Error colocando take-profit para {decision.symbol}: {e}")

        return sl_ok and tp_ok
