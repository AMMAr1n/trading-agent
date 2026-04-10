"""
balance.py — Consulta de saldo real en Binance
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv(override=False)
logger = logging.getLogger(__name__)

# Mínimo absoluto por operación — Binance Futuros rechaza órdenes menores a $5 USD
MIN_TRADE_AMOUNT_USD = float(os.getenv("MIN_TRADE_AMOUNT_USD", "5.5"))


@dataclass
class BalanceInfo:
    usdt_total: float
    usdt_free: float
    reserve: float
    operable: float
    vobo_threshold: float
    min_trade_amount: float
    has_sufficient_funds: bool
    hold_symbols_value: dict

    @property
    def summary(self) -> str:
        return (
            f"USDT disponible: ${self.usdt_free:.2f} | "
            f"Operable: ${self.operable:.2f} | "
            f"Reserva: ${self.reserve:.2f} | "
            f"VoBo si > ${self.vobo_threshold:.2f}"
        )

    @property
    def whatsapp_no_funds_message(self) -> str:
        return (
            f"SIN SALDO DISPONIBLE\n"
            f"{'─'*30}\n"
            f"USDT en cuenta: ${self.usdt_free:.2f}\n"
            f"Minimo para operar: ${self.min_trade_amount:.2f}\n"
            f"{'─'*30}\n"
            f"El agente esta en pausa hasta que\n"
            f"deposites USDT en tu cuenta de Binance.\n"
            f"Deposita en: Binance > Depositar > USDT"
        )


class BalanceChecker:
    def __init__(self, exchange: ccxt.binance):
        self.exchange = exchange
        self.reserve_pct   = float(os.getenv("RESERVE_PCT",    "10")) / 100
        self.vobo_min_pct  = float(os.getenv("VOBO_MIN_PCT",   "15")) / 100
        self.min_trade_pct = float(os.getenv("MIN_TRADE_PCT",  "10")) / 100
        self.hold_symbols  = [
            s.strip() for s in os.getenv("HOLD_SYMBOLS", "").split(",")
            if s.strip()
        ]

    async def get_balance(self) -> Optional[BalanceInfo]:
        try:
            balance    = await self.exchange.fetch_balance()
            usdt_info  = balance.get("USDT", {})
            usdt_total = float(usdt_info.get("total", 0) or 0)
            usdt_free  = float(usdt_info.get("free",  0) or 0)

            reserve  = usdt_total * self.reserve_pct
            operable = max(usdt_free - reserve, 0)

            vobo_threshold       = operable * self.vobo_min_pct
            min_trade_pct_amount = operable * self.min_trade_pct
            # Piso absoluto: nunca menos de MIN_TRADE_AMOUNT_USD
            min_trade_amount     = max(min_trade_pct_amount, MIN_TRADE_AMOUNT_USD)

            has_funds = operable >= min_trade_amount

            hold_values = {}
            for symbol in self.hold_symbols:
                base       = symbol.replace("USDT", "")
                asset_info = balance.get(base, {})
                asset_free = float(asset_info.get("free", 0) or 0)
                if asset_free > 0:
                    hold_values[symbol] = asset_free

            balance_info = BalanceInfo(
                usdt_total=round(usdt_total, 2),
                usdt_free=round(usdt_free, 2),
                reserve=round(reserve, 2),
                operable=round(operable, 2),
                vobo_threshold=round(vobo_threshold, 2),
                min_trade_amount=round(min_trade_amount, 2),
                has_sufficient_funds=has_funds,
                hold_symbols_value=hold_values,
            )
            logger.info(balance_info.summary)
            return balance_info

        except ccxt.AuthenticationError:
            logger.error("Error de autenticacion con Binance — verifica API Key y Secret")
            return None
        except ccxt.NetworkError as e:
            logger.error(f"Error de red al consultar saldo: {e}")
            return None
        except Exception as e:
            logger.error(f"Error inesperado al consultar saldo: {e}")
            return None
