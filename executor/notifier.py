"""
notifier.py — Notificaciones por Telegram
Responsabilidad: enviar todos los mensajes al operador via Telegram Bot.
"""

import logging
import os
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv(override=False)
logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not all([self.bot_token, self.chat_id]):
            raise EnvironmentError(
                "Faltan credenciales de Telegram en el .env. "
                "Verifica: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID"
            )

        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        logger.info("TelegramNotifier inicializado")

    def send(self, message: str) -> bool:
        try:
            response = httpx.post(
                f"{self.api_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10
            )
            if response.status_code == 200:
                logger.info("Telegram enviado correctamente")
                return True
            else:
                logger.error(f"Error Telegram: {response.status_code} — {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error enviando Telegram: {e}")
            return False

    def notify_no_funds(self, usdt_free: float, min_required: float) -> bool:
        message = (
            f"🔴 <b>SIN SALDO DISPONIBLE</b>\n"
            f"USDT en cuenta: <b>${usdt_free:.2f}</b>\n"
            f"Mínimo para operar: <b>${min_required:.2f}</b>\n"
            f"El agente está en pausa.\n"
            f"Deposita USDT en Binance para continuar."
        )
        return self.send(message)

    def notify_trade_opened(
        self,
        symbol: str,
        direction: str,
        amount_usd: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        leverage: str,
        reasoning: str,
        account_balance: float = 0.0,
        trade_amount: float = 0.0
    ) -> bool:
        arrow = "📈" if direction == "long" else "📉"
        direction_str = "SUBE (LONG)" if direction == "long" else "BAJA (SHORT)"
        monto_operacion = trade_amount if trade_amount > 0 else amount_usd

        message = (
            f"{arrow} <b>OPERACIÓN ABIERTA</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Activo: <b>{symbol}</b> — {direction_str}\n"
            f"Precio de entrada: <b>${entry_price:,.4f}</b>\n"
            f"Apalancamiento: <b>{leverage}</b>\n"
            f"\n"
            f"💰 <b>CAPITAL</b>\n"
            f"Saldo total en cuenta: <b>${account_balance:.2f} USDT</b>\n"
            f"Monto asignado a esta operación: <b>${monto_operacion:.2f} USDT</b>\n"
            f"\n"
            f"🎯 <b>NIVELES</b>\n"
            f"Stop-loss: <b>${stop_loss:,.4f}</b>\n"
            f"Take-profit: <b>${take_profit:,.4f}</b>\n"
            f"\n"
            f"🧠 <b>RAZONAMIENTO</b>\n"
            f"{reasoning[:300]}"
        )
        return self.send(message)

    def notify_vobo_request(
        self,
        symbol: str,
        direction: str,
        amount_usd: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        leverage: str,
        reasoning: str,
        timeout_min: int = 10,
        account_balance: float = 0.0,
        trade_amount: float = 0.0
    ) -> bool:
        arrow = "📈" if direction == "long" else "📉"
        direction_str = "SUBE (LONG)" if direction == "long" else "BAJA (SHORT)"
        monto_operacion = trade_amount if trade_amount > 0 else amount_usd

        message = (
            f"{arrow} <b>SOLICITUD DE APROBACIÓN</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Activo: <b>{symbol}</b> — {direction_str}\n"
            f"Apalancamiento: <b>{leverage}</b>\n"
            f"\n"
            f"💰 <b>CAPITAL</b>\n"
            f"Saldo total en cuenta: <b>${account_balance:.2f} USDT</b>\n"
            f"Monto a usar en esta operación: <b>${monto_operacion:.2f} USDT</b>\n"
            f"\n"
            f"🎯 <b>NIVELES</b>\n"
            f"Stop-loss: <b>${stop_loss:,.4f}</b>\n"
            f"Take-profit: <b>${take_profit:,.4f}</b>\n"
            f"\n"
            f"🧠 <b>RAZONAMIENTO</b>\n"
            f"{reasoning[:300]}\n"
            f"\n"
            f"Responde <b>SI</b> para aprobar o <b>NO</b> para cancelar\n"
            f"(Expira en {timeout_min} minutos)"
        )
        return self.send(message)

    def notify_trade_closed(
        self,
        symbol: str,
        direction: str,
        pnl_usd: float,
        pnl_pct: float,
        duration_min: int,
        close_reason: str,
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        account_balance_after: float = 0.0
    ) -> bool:
        emoji = "✅" if pnl_usd > 0 else "❌"
        result = "GANANCIA" if pnl_usd > 0 else "PÉRDIDA"
        sign = "+" if pnl_usd > 0 else ""

        price_lines = ""
        if entry_price > 0 and exit_price > 0:
            price_lines = f"Entrada: <b>${entry_price:,.4f}</b>  →  Salida: <b>${exit_price:,.4f}</b>\n"

        balance_line = ""
        if account_balance_after > 0:
            balance_line = f"Saldo actualizado: <b>${account_balance_after:.2f} USDT</b>\n"

        message = (
            f"{emoji} <b>OPERACIÓN CERRADA — {result}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Activo: <b>{symbol}</b> ({direction.upper()})\n"
            f"{price_lines}"
            f"Resultado: <b>{sign}${pnl_usd:.2f} USD ({sign}{pnl_pct:.1f}%)</b>\n"
            f"Duración: {duration_min} min\n"
            f"Motivo de cierre: {close_reason}\n"
            f"{balance_line}"
        )
        return self.send(message)

    def notify_capital_alert(self, level: str, current_balance: float, initial_balance: float, pct_remaining: float) -> bool:
        emojis = {"yellow": "🟡", "orange": "🟠", "red": "🔴"}
        headers = {"yellow": "ALERTA DE CAPITAL", "orange": "ALERTA CRÍTICA", "red": "EMERGENCIA — CAPITAL MÍNIMO"}
        actions = {
            "yellow": "El agente continúa operando.",
            "orange": "El agente redujo el tamaño de operaciones.",
            "red": "El agente SE DETUVO. Deposita USDT para reactivarlo."
        }
        message = (
            f"{emojis[level]} <b>{headers[level]}</b>\n"
            f"Saldo actual: <b>${current_balance:.2f} USD</b> ({pct_remaining:.1f}%)\n"
            f"Saldo inicial: ${initial_balance:.2f} USD\n"
            f"{actions[level]}"
        )
        return self.send(message)

    def notify_vobo_timeout(self, symbol: str, amount_usd: float) -> bool:
        return self.send(
            f"⏱ <b>VOBO EXPIRADO</b>\n"
            f"La operación de {symbol} por ${amount_usd:.2f} USD fue cancelada.\n"
            f"El agente continúa monitoreando."
        )

    def notify_daily_report(
        self,
        date: str,
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        total_pnl: float,
        win_rate: float,
        starting_balance: float,
        ending_balance: float
    ) -> bool:
        balance_change = ending_balance - starting_balance
        sign = "+" if balance_change >= 0 else ""
        emoji = "✅" if total_pnl >= 0 else "❌"
        message = (
            f"📊 <b>RESUMEN DEL DÍA — {date}</b>\n"
            f"{emoji} P&L: <b>{'+' if total_pnl >= 0 else ''}${total_pnl:.2f} USD</b>\n"
            f"Operaciones: {total_trades} | Ganadoras: {winning_trades} | Perdedoras: {losing_trades}\n"
            f"Win rate: {win_rate:.0f}%\n"
            f"Saldo: ${starting_balance:.2f} → <b>${ending_balance:.2f} USD</b> ({sign}${balance_change:.2f})\n"
            f"¡Hasta mañana! 🚀"
        )
        return self.send(message)

    def notify_critical_error(self, error_msg: str) -> bool:
        return self.send(
            f"🚨 <b>ERROR CRÍTICO DEL AGENTE</b>\n"
            f"{error_msg[:300]}\n"
            f"Revisa el servidor — el agente puede estar detenido."
        )

    def notify_agent_started(self, balance: float, operable: float = 0.0) -> bool:
        operable_line = ""
        if operable > 0:
            operable_line = f"Monto operable: <b>${operable:.2f} USDT</b>\n"

        return self.send(
            f"🤖 <b>AGENTE INICIADO</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Saldo en cuenta: <b>${balance:.2f} USDT</b>\n"
            f"{operable_line}"
            f"Monitoreando: BTC, ETH, SOL, BNB, DOGE, XRP, ADA, PEPE\n"
            f"Te notificaré cada operación. 📱"
        )


# Alias para compatibilidad con código existente
WhatsAppNotifier = TelegramNotifier
