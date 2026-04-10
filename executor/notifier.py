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

    def notify_trade_opened(self, symbol, direction, amount_usd, entry_price, stop_loss, take_profit, leverage, reasoning) -> bool:
        arrow = "📈" if direction == "long" else "📉"
        direction_str = "SUBE (LONG)" if direction == "long" else "BAJA (SHORT)"
        message = (
            f"{arrow} <b>OPERACIÓN ABIERTA</b>\n"
            f"Activo: <b>{symbol}</b> — {direction_str}\n"
            f"Monto: <b>${amount_usd:.2f} USD</b> | Apalancamiento: {leverage}\n"
            f"Precio: ${entry_price:,.4f}\n"
            f"Stop-loss: ${stop_loss:,.4f}\n"
            f"Take-profit: ${take_profit:,.4f}\n"
            f"Razón: {reasoning[:200]}"
        )
        return self.send(message)

    def notify_vobo_request(self, symbol, direction, amount_usd, entry_price, stop_loss, take_profit, leverage, reasoning, timeout_min=10) -> bool:
        arrow = "📈" if direction == "long" else "📉"
        direction_str = "SUBE (LONG)" if direction == "long" else "BAJA (SHORT)"
        message = (
            f"{arrow} <b>SOLICITUD DE APROBACIÓN</b>\n"
            f"Activo: <b>{symbol}</b> — {direction_str}\n"
            f"Monto: <b>${amount_usd:.2f} USD</b> | Apalancamiento: {leverage}\n"
            f"Stop-loss: ${stop_loss:,.4f}\n"
            f"Take-profit: ${take_profit:,.4f}\n"
            f"Análisis: {reasoning[:300]}\n"
            f"Responde <b>SI</b> para aprobar o <b>NO</b> para cancelar\n"
            f"(Expira en {timeout_min} minutos)"
        )
        return self.send(message)

    def notify_trade_closed(self, symbol, direction, pnl_usd, pnl_pct, duration_min, close_reason) -> bool:
        emoji = "✅" if pnl_usd > 0 else "❌"
        result = "GANANCIA" if pnl_usd > 0 else "PÉRDIDA"
        sign = "+" if pnl_usd > 0 else ""
        message = (
            f"{emoji} <b>OPERACIÓN CERRADA — {result}</b>\n"
            f"Activo: <b>{symbol}</b> ({direction.upper()})\n"
            f"Resultado: <b>{sign}${pnl_usd:.2f} USD ({sign}{pnl_pct:.1f}%)</b>\n"
            f"Duración: {duration_min} min | Razón: {close_reason}"
        )
        return self.send(message)

    def notify_capital_alert(self, level, current_balance, initial_balance, pct_remaining) -> bool:
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

    def notify_vobo_timeout(self, symbol, amount_usd) -> bool:
        return self.send(
            f"⏱ <b>VOBO EXPIRADO</b>\n"
            f"La operación de {symbol} por ${amount_usd:.2f} USD fue cancelada.\n"
            f"El agente continúa monitoreando."
        )

    def notify_daily_report(self, date, total_trades, winning_trades, losing_trades, total_pnl, win_rate, starting_balance, ending_balance) -> bool:
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

    def notify_critical_error(self, error_msg) -> bool:
        return self.send(
            f"🚨 <b>ERROR CRÍTICO DEL AGENTE</b>\n"
            f"{error_msg[:300]}\n"
            f"Revisa el servidor — el agente puede estar detenido."
        )

    def notify_agent_started(self, balance) -> bool:
        return self.send(
            f"🤖 <b>AGENTE INICIADO</b>\n"
            f"Saldo disponible: <b>${balance:.2f} USDT</b>\n"
            f"Monitoreando: BTC, ETH, SOL, BNB, DOGE, XRP, ADA, PEPE\n"
            f"Te notificaré cada operación. 📱"
        )


# Alias para compatibilidad con código existente
WhatsAppNotifier = TelegramNotifier
