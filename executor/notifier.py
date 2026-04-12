"""
notifier.py — Notificaciones por Telegram
"""

import logging
import os
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv(override=False)
logger = logging.getLogger(__name__)

TELEGRAM_MAX_CHARS = 4000


class TelegramNotifier:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id   = os.getenv("TELEGRAM_CHAT_ID")

        if not all([self.bot_token, self.chat_id]):
            raise EnvironmentError(
                "Faltan credenciales de Telegram en el .env. "
                "Verifica: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID"
            )

        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        logger.info("TelegramNotifier inicializado")

    def send(self, message: str) -> bool:
        if len(message) <= TELEGRAM_MAX_CHARS:
            return self._send_single(message)
        parts = []
        while message:
            parts.append(message[:TELEGRAM_MAX_CHARS])
            message = message[TELEGRAM_MAX_CHARS:]
        success = True
        for i, part in enumerate(parts, 1):
            suffix = f"\n<i>(Mensaje {i}/{len(parts)})</i>" if len(parts) > 1 else ""
            if not self._send_single(part + suffix):
                success = False
        return success

    def _send_single(self, message: str) -> bool:
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
        return self.send(
            f"🔴 <b>SIN SALDO DISPONIBLE</b>\n"
            f"USDT en cuenta: <b>${usdt_free:.2f}</b>\n"
            f"Mínimo para operar: <b>${min_required:.2f}</b>\n"
            f"El agente está en pausa.\n"
            f"Deposita USDT en Binance para continuar."
        )

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
        monto = trade_amount if trade_amount > 0 else amount_usd

        # ── Fix #1: usar entry_price real, no stop_loss ───────────────────
        # entry_price se pasa desde _execute_autonomous como result.entry_price
        precio_entrada = entry_price

        # ── Fix #2: calcular P&L estimado ────────────────────────────────
        if precio_entrada > 0 and stop_loss > 0 and take_profit > 0:
            if direction == "long":
                riesgo_pct  = (precio_entrada - stop_loss) / precio_entrada * 100
                ganancia_pct = (take_profit - precio_entrada) / precio_entrada * 100
            else:
                riesgo_pct  = (stop_loss - precio_entrada) / precio_entrada * 100
                ganancia_pct = (precio_entrada - take_profit) / precio_entrada * 100

            riesgo_usd   = monto * (riesgo_pct / 100)
            ganancia_usd = monto * (ganancia_pct / 100)
            rr_ratio     = ganancia_pct / riesgo_pct if riesgo_pct > 0 else 0

            pnl_lines = (
                f"\n"
                f"📊 <b>P&L ESTIMADO</b>\n"
                f"✅ Si TP (${take_profit:,.4f}): <b>+${ganancia_usd:.2f} USD (+{ganancia_pct:.1f}%)</b>\n"
                f"❌ Si SL (${stop_loss:,.4f}): <b>-${riesgo_usd:.2f} USD (-{riesgo_pct:.1f}%)</b>\n"
                f"Ratio R/R: <b>1:{rr_ratio:.1f}</b>"
            )
        else:
            pnl_lines = ""

        msg1 = (
            f"{arrow} <b>OPERACIÓN ABIERTA</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Activo: <b>{symbol}</b> — {direction_str}\n"
            f"Precio de entrada: <b>${precio_entrada:,.4f}</b>\n"
            f"Apalancamiento: <b>{leverage}</b>\n"
            f"\n"
            f"💰 <b>CAPITAL</b>\n"
            f"Saldo total en cuenta: <b>${account_balance:.2f} USDT</b>\n"
            f"Monto asignado: <b>${monto:.2f} USDT</b>\n"
            f"\n"
            f"🎯 <b>NIVELES</b>\n"
            f"Stop-loss: <b>${stop_loss:,.4f}</b>\n"
            f"Take-profit: <b>${take_profit:,.4f}</b>"
            f"{pnl_lines}"
        )
        self._send_single(msg1)

        if reasoning:
            self.send(
                f"🧠 <b>RAZONAMIENTO — {symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{reasoning}"
            )
        return True

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
        monto = trade_amount if trade_amount > 0 else amount_usd

        msg1 = (
            f"{arrow} <b>SOLICITUD DE APROBACIÓN</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Activo: <b>{symbol}</b> — {direction_str}\n"
            f"Apalancamiento: <b>{leverage}</b>\n"
            f"\n"
            f"💰 <b>CAPITAL</b>\n"
            f"Saldo total en cuenta: <b>${account_balance:.2f} USDT</b>\n"
            f"Monto a usar: <b>${monto:.2f} USDT</b>\n"
            f"\n"
            f"🎯 <b>NIVELES</b>\n"
            f"Stop-loss: <b>${stop_loss:,.4f}</b>\n"
            f"Take-profit: <b>${take_profit:,.4f}</b>\n"
            f"\n"
            f"Responde <b>SI</b> para aprobar o <b>NO</b> para cancelar\n"
            f"(Expira en {timeout_min} minutos)"
        )
        self._send_single(msg1)

        if reasoning:
            self.send(
                f"🧠 <b>RAZONAMIENTO — {symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{reasoning}"
            )
        return True

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
        emoji  = "✅" if pnl_usd > 0 else "❌"
        result = "GANANCIA" if pnl_usd > 0 else "PÉRDIDA"
        sign   = "+" if pnl_usd > 0 else ""

        price_lines = ""
        if entry_price > 0 and exit_price > 0:
            price_lines = f"Entrada: <b>${entry_price:,.4f}</b>  →  Salida: <b>${exit_price:,.4f}</b>\n"

        balance_line = ""
        if account_balance_after > 0:
            balance_line = f"Saldo actualizado: <b>${account_balance_after:.2f} USDT</b>\n"

        return self.send(
            f"{emoji} <b>OPERACIÓN CERRADA — {result}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Activo: <b>{symbol}</b> ({direction.upper()})\n"
            f"{price_lines}"
            f"Resultado: <b>{sign}${pnl_usd:.2f} USD ({sign}{pnl_pct:.1f}%)</b>\n"
            f"Duración: {duration_min} min\n"
            f"Motivo: {close_reason}\n"
            f"{balance_line}"
        )

    def notify_capital_alert(
        self, level: str, current_balance: float,
        initial_balance: float, pct_remaining: float
    ) -> bool:
        emojis  = {"yellow": "🟡", "orange": "🟠", "red": "🔴"}
        headers = {
            "yellow": "ALERTA DE CAPITAL",
            "orange": "ALERTA CRÍTICA",
            "red":    "EMERGENCIA — CAPITAL MÍNIMO"
        }
        actions = {
            "yellow": "El agente continúa operando.",
            "orange": "El agente redujo el tamaño de operaciones.",
            "red":    "El agente SE DETUVO. Deposita USDT para reactivarlo."
        }
        return self.send(
            f"{emojis[level]} <b>{headers[level]}</b>\n"
            f"Saldo actual: <b>${current_balance:.2f} USD</b> ({pct_remaining:.1f}%)\n"
            f"Saldo inicial: ${initial_balance:.2f} USD\n"
            f"{actions[level]}"
        )

    def notify_insufficient_amount(self, symbol: str, amount_usd: float, min_required: float) -> bool:
        return self.send(
            f"⚠️ <b>MONTO INSUFICIENTE — {symbol}</b>\n"
            f"Monto calculado: <b>${amount_usd:.2f} USDT</b>\n"
            f"Mínimo requerido por Binance: <b>${min_required:.2f} USDT</b>\n"
            f"El agente saltó este par."
        )

    def notify_connection_error(self, details: str) -> bool:
        return self.send(
            f"📡 <b>ERROR DE CONEXIÓN</b>\n"
            f"No se pudo conectar con Binance.\n"
            f"Detalle: {details[:200]}\n"
            f"El agente reintentará en el próximo ciclo."
        )

    def notify_unexpected_error(self, context: str, details: str) -> bool:
        return self.send(
            f"🚨 <b>ERROR INESPERADO</b>\n"
            f"Contexto: {context}\n"
            f"Detalle: {details[:300]}\n"
            f"Revisa los logs en la VM."
        )

    def notify_critical_error(self, error_msg: str) -> bool:
        msg = error_msg.lower()
        if "minimum amount" in msg or "mínimo de costo" in msg or "monto" in msg:
            return self.send(f"⚠️ <b>MONTO INSUFICIENTE</b>\n{error_msg[:300]}\nEl agente saltó este par.")
        elif "network" in msg or "conexión" in msg or "connect" in msg:
            return self.send(f"📡 <b>ERROR DE CONEXIÓN</b>\n{error_msg[:300]}\nEl agente reintentará en el próximo ciclo.")
        elif "sl/tp" in msg or "stop" in msg or "protección" in msg:
            return self.send(f"⚠️ <b>POSICIÓN SIN PROTECCIÓN</b>\n{error_msg[:300]}\nEl monitor intentará reponer SL/TP.")
        else:
            return self.send(f"🚨 <b>ERROR CRÍTICO DEL AGENTE</b>\n{error_msg[:300]}\nRevisa el servidor.")

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
        ending_balance: float,
        open_positions: list = None
    ) -> bool:
        balance_change = ending_balance - starting_balance
        sign  = "+" if balance_change >= 0 else ""
        emoji = "✅" if total_pnl >= 0 else "❌"

        # ── Fix #6: posiciones abiertas en el reporte ─────────────────────
        positions_section = ""
        if open_positions:
            positions_section = "\n\n📂 <b>POSICIONES ABIERTAS</b>\n"
            for pos in open_positions:
                symbol    = pos.get("symbol", "")
                entry     = pos.get("entry_price", 0)
                sl        = pos.get("stop_loss", 0)
                tp        = pos.get("take_profit", 0)
                amount    = pos.get("amount_usd", 0)
                direction = pos.get("direction", "long")
                arrow     = "📈" if direction == "long" else "📉"
                positions_section += (
                    f"{arrow} <b>{symbol}</b> | Entrada: ${entry:,.4f} | "
                    f"Monto: ${amount:.2f}\n"
                    f"   SL: ${sl:,.4f} | TP: ${tp:,.4f}\n"
                )

        return self.send(
            f"📊 <b>RESUMEN DEL DÍA — {date}</b>\n"
            f"{emoji} P&L: <b>{'+' if total_pnl >= 0 else ''}${total_pnl:.2f} USD</b>\n"
            f"Operaciones: {total_trades} | Ganadoras: {winning_trades} | Perdedoras: {losing_trades}\n"
            f"Win rate: {win_rate:.0f}%\n"
            f"Saldo: ${starting_balance:.2f} → <b>${ending_balance:.2f} USD</b> ({sign}${balance_change:.2f})"
            f"{positions_section}\n"
            f"¡Hasta el próximo reporte! 🚀"
        )

    def notify_agent_started(self, balance: float, operable: float = 0.0) -> bool:
        operable_line = f"Monto operable: <b>${operable:.2f} USDT</b>\n" if operable > 0 else ""
        return self.send(
            f"🤖 <b>AGENTE INICIADO</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Saldo en cuenta: <b>${balance:.2f} USDT</b>\n"
            f"{operable_line}"
            f"Monitoreando: BTC, ETH, SOL, BNB, DOGE, XRP, ADA\n"
            f"Te notificaré cada operación. 📱"
        )


# Alias para compatibilidad
WhatsAppNotifier = TelegramNotifier
