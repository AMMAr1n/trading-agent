"""
notifier.py — Notificaciones por Telegram
v0.7.2 — Fix reporte periódico: muestra posiciones abiertas y cerradas del periodo.
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

    def notify_no_funds(
        self, usdt_free: float, min_required: float,
        usdt_total: float = 0.0, margin_in_use: float = 0.0,
        reserve: float = 0.0, operable: float = 0.0,
        symbol: str = "", direction: str = "", score: float = 0.0
    ) -> bool:
        total = usdt_total if usdt_total > 0 else usdt_free
        arrow = "📈" if direction == "long" else "📉" if direction == "short" else ""
        direction_str = "LONG (SUBE)" if direction == "long" else "SHORT (BAJA)" if direction == "short" else ""
        signal_line = ""
        if symbol:
            signal_line = f"{arrow} Señal: <b>{symbol}</b>"
            if direction_str:
                signal_line += f" — {direction_str}"
            if score > 0:
                signal_line += f" | Score: <b>{score:.0f}/100</b>"
            signal_line += "\n"
        return self.send(
            f"🔴 <b>SIN SALDO DISPONIBLE</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{signal_line}"
            f"💰 Saldo total: <b>${total:.2f} USDT</b>\n"
            f"🔒 Margen en uso: <b>${margin_in_use:.2f} USDT</b>\n"
            f"🏦 Reserva (10%): <b>${reserve:.2f} USDT</b>\n"
            f"✅ Saldo operable: <b>${operable:.2f} USDT</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Mínimo para operar: <b>${min_required:.2f} USDT</b>\n"
            f"El agente está en pausa.\n"
            f"Deposita USDT en Binance para continuar."
        )

    def notify_trade_opened(
        self, symbol, direction, amount_usd, entry_price, stop_loss,
        take_profit, leverage, reasoning, account_balance=0.0,
        trade_amount=0.0, usdt_total=0.0, margin_in_use=0.0,
        reserve=0.0, operable=0.0,
    ) -> bool:
        arrow = "📈" if direction == "long" else "📉"
        direction_str = "SUBE (LONG)" if direction == "long" else "BAJA (SHORT)"
        monto = trade_amount if trade_amount > 0 else amount_usd
        precio_entrada = entry_price
        total = usdt_total if usdt_total > 0 else account_balance
        capital_section = (
            f"💰 Saldo total: <b>${total:.2f} USDT</b>\n"
            f"🔒 Margen en uso: <b>${margin_in_use:.2f} USDT</b>\n"
            f"🏦 Reserva (10%): <b>${reserve:.2f} USDT</b>\n"
            f"✅ Saldo operable: <b>${operable:.2f} USDT</b>\n"
            f"📌 Esta operación: <b>${monto:.2f} USDT</b>"
        )
        pnl_lines = ""
        if precio_entrada > 0 and stop_loss > 0 and take_profit > 0:
            if direction == "long":
                riesgo_pct   = (precio_entrada - stop_loss) / precio_entrada * 100
                ganancia_pct = (take_profit - precio_entrada) / precio_entrada * 100
            else:
                riesgo_pct   = (stop_loss - precio_entrada) / precio_entrada * 100
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
        msg1 = (
            f"{arrow} <b>OPERACIÓN ABIERTA</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Activo: <b>{symbol}</b> — {direction_str}\n"
            f"Precio de entrada: <b>${precio_entrada:,.4f}</b>\n"
            f"Apalancamiento: <b>{leverage}</b>\n"
            f"\n"
            f"💰 <b>CAPITAL</b>\n"
            f"{capital_section}\n"
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
        self, symbol, direction, amount_usd, entry_price,
        stop_loss, take_profit, leverage, reasoning,
        timeout_min=10, account_balance=0.0, trade_amount=0.0
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
            f"⏱ Tiempo para aprobar: <b>{timeout_min} minutos</b>\n"
            f"Responde 'ok' para aprobar, cualquier otra cosa cancela."
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
        self, symbol, direction, pnl_usd, pnl_pct,
        duration_min=0, close_reason="", entry_price=0.0,
        exit_price=0.0, account_balance_after=0.0,
        usdt_total=0.0, margin_in_use=0.0, reserve=0.0, operable=0.0,
    ) -> bool:
        arrow = "📈" if direction == "long" else "📉"
        emoji = "✅" if pnl_usd >= 0 else "❌"
        sign = "+" if pnl_usd >= 0 else ""
        reason_str = ""
        if "take_profit" in str(close_reason).lower() or "tp" in str(close_reason).lower():
            reason_str = "✅ Take Profit alcanzado"
        elif "stop_loss" in str(close_reason).lower() or "sl" in str(close_reason).lower():
            reason_str = "❌ Stop Loss tocado"
        else:
            reason_str = f"Razón: {close_reason}"
        hours = duration_min // 60
        mins = duration_min % 60
        dur_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
        balance_section = ""
        if usdt_total > 0:
            balance_section = (
                f"\n💰 Saldo total: <b>${usdt_total:.2f} USDT</b>\n"
                f"🔒 Margen en uso: <b>${margin_in_use:.2f} USDT</b>\n"
                f"🏦 Reserva: <b>${reserve:.2f} USDT</b>\n"
                f"✅ Operable: <b>${operable:.2f} USDT</b>"
            )
        return self.send(
            f"{emoji} <b>OPERACIÓN CERRADA — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{arrow} Dirección: {direction.upper()}\n"
            f"Entrada: ${entry_price:,.4f} → Salida: ${exit_price:,.4f}\n"
            f"P&L: <b>{sign}${pnl_usd:.2f} USD ({sign}{pnl_pct:.1f}%)</b>\n"
            f"Duración: {dur_str}\n"
            f"{reason_str}"
            f"{balance_section}"
        )

    def notify_capital_alert(self, level, current_balance, initial_balance, pct_remaining):
        emojis  = {"yellow": "🟡", "orange": "🟠", "red": "🔴"}
        headers = {
            "yellow": "ALERTA DE CAPITAL — NIVEL AMARILLO",
            "orange": "ALERTA DE CAPITAL — NIVEL NARANJA",
            "red":    "ALERTA DE CAPITAL — NIVEL ROJO"
        }
        actions = {
            "yellow": "El agente reduce el tamaño de posición.",
            "orange": "El agente opera con mínimos.",
            "red":    "El agente se detiene hasta que el capital se recupere."
        }
        return self.send(
            f"{emojis[level]} <b>{headers[level]}</b>\n"
            f"Saldo actual: <b>${current_balance:.2f} USD</b> ({pct_remaining:.1f}%)\n"
            f"Saldo inicial: ${initial_balance:.2f} USD\n"
            f"{actions[level]}"
        )

    def notify_insufficient_amount(
        self, symbol, amount_usd, min_required, score=0.0,
        usdt_total=0.0, margin_in_use=0.0, reserve=0.0, operable=0.0
    ) -> bool:
        score_line = f"Score de la señal: <b>{score:.0f}/100</b>\n" if score > 0 else ""
        balance_section = ""
        if usdt_total > 0:
            balance_section = (
                f"\n💰 Saldo total: <b>${usdt_total:.2f} USDT</b>\n"
                f"🔒 Margen en uso: <b>${margin_in_use:.2f} USDT</b>\n"
                f"🏦 Reserva (10%): <b>${reserve:.2f} USDT</b>\n"
                f"✅ Saldo operable: <b>${operable:.2f} USDT</b>\n"
            )
        return self.send(
            f"⚠️ <b>MONTO INSUFICIENTE — {symbol}</b>\n"
            f"{score_line}"
            f"Monto calculado: <b>${amount_usd:.2f} USDT</b>\n"
            f"Mínimo requerido por Binance: <b>${min_required:.2f} USDT</b>"
            f"{balance_section}"
            f"El agente saltó este par."
        )

    def notify_connection_error(self, details):
        return self.send(
            f"📡 <b>ERROR DE CONEXIÓN</b>\n"
            f"No se pudo conectar con Binance.\n"
            f"Detalle: {details[:200]}\n"
            f"El agente reintentará en el próximo ciclo."
        )

    def notify_unexpected_error(self, context, details):
        return self.send(
            f"🚨 <b>ERROR INESPERADO</b>\n"
            f"Contexto: {context}\n"
            f"Detalle: {details[:300]}\n"
            f"Revisa los logs en la VM."
        )

    def notify_critical_error(self, error_msg):
        msg = error_msg.lower()
        if "minimum amount" in msg or "mínimo de costo" in msg or "monto" in msg:
            return self.send(f"⚠️ <b>MONTO INSUFICIENTE</b>\n{error_msg[:300]}\nEl agente saltó este par.")
        elif "network" in msg or "conexión" in msg or "connect" in msg:
            return self.send(f"📡 <b>ERROR DE CONEXIÓN</b>\n{error_msg[:300]}\nEl agente reintentará en el próximo ciclo.")
        elif "sl/tp" in msg or "stop" in msg or "protección" in msg:
            return self.send(f"⚠️ <b>POSICIÓN SIN PROTECCIÓN</b>\n{error_msg[:300]}\nEl monitor intentará reponer SL/TP.")
        else:
            return self.send(f"🚨 <b>ERROR CRÍTICO DEL AGENTE</b>\n{error_msg[:300]}\nRevisa el servidor.")

    def notify_vobo_timeout(self, symbol, amount_usd):
        return self.send(
            f"⏱ <b>VOBO EXPIRADO</b>\n"
            f"La operación de {symbol} por ${amount_usd:.2f} USD fue cancelada.\n"
            f"El agente continúa monitoreando."
        )

    def notify_daily_report(
        self, date, total_trades, winning_trades, losing_trades,
        total_pnl, win_rate, starting_balance, ending_balance,
        open_positions=None, period_label="",
        open_count=0, closed_in_period=0,
        closed_tp=0, closed_sl=0, stage_name="",
    ) -> bool:
        """
        v0.7.2: Reporte periódico corregido.
        - Muestra posiciones abiertas actuales
        - Muestra cerradas en el periodo (TP vs SL)
        """
        balance_change = ending_balance - starting_balance
        sign  = "+" if balance_change >= 0 else ""
        emoji = "✅" if total_pnl >= 0 else "❌"

        if not period_label:
            hour = datetime.now().hour
            if hour < 6:
                period_label = "RESUMEN DE LA NOCHE"
            elif hour < 12:
                period_label = "RESUMEN DE LA MAÑANA"
            elif hour < 18:
                period_label = "RESUMEN DEL MEDIODÍA"
            else:
                period_label = "RESUMEN DE LA TARDE"

        # Línea de operaciones corregida
        ops_line = f"Posiciones abiertas: <b>{open_count}</b>"
        if closed_in_period > 0:
            ops_line += f" | Cerradas: <b>{closed_in_period}</b> ({closed_tp} TP, {closed_sl} SL)"
            ops_line += f"\nWin rate periodo: {win_rate:.0f}%"
        else:
            ops_line += " | Sin cierres en este periodo"

        # Posiciones abiertas con detalles
        positions_section = ""
        if open_positions:
            positions_section = "\n\n📂 <b>POSICIONES ABIERTAS</b>"
            for pos in open_positions:
                symbol    = pos.get("symbol", "")
                entry     = pos.get("entry_price", 0) or 0
                sl        = pos.get("stop_loss", 0) or 0
                tp        = pos.get("take_profit", 0) or 0
                amount    = pos.get("amount_usd", 0) or 0
                direction = pos.get("direction", "long")
                current   = pos.get("current_price", entry) or entry
                arrow     = "📈" if direction == "long" else "📉"

                if entry > 0 and current > 0:
                    pnl_pct = ((current - entry) / entry * 100) if direction == "long" \
                              else ((entry - current) / entry * 100)
                    pnl_usd = amount * (pnl_pct / 100)
                    sign_pnl = "+" if pnl_pct >= 0 else ""
                    pnl_str = f"{sign_pnl}{pnl_usd:.2f} USD ({sign_pnl}{pnl_pct:.1f}%)"
                else:
                    pnl_str = "N/A"

                dist_sl_pct = abs((entry - sl) / entry * 100) if entry > 0 and sl > 0 else 0
                dist_sl_usd = amount * (dist_sl_pct / 100)
                dist_tp_pct = abs((tp - entry) / entry * 100) if entry > 0 and tp > 0 else 0
                dist_tp_usd = amount * (dist_tp_pct / 100)

                positions_section += (
                    f"\n\n{arrow} <b>{symbol}</b> | ${amount:.2f} USD\n"
                    f"Entrada: ${entry:,.4f} | Actual: ${current:,.4f}\n"
                    f"P&L: <b>{pnl_str}</b>\n"
                    f"✅ TP: ${tp:,.4f} (+{dist_tp_pct:.1f}% / +${dist_tp_usd:.2f})\n"
                    f"❌ SL: ${sl:,.4f} (-{dist_sl_pct:.1f}% / -${dist_sl_usd:.2f})"
                )

        # Línea de etapa
        stage_line = f"🎓 Etapa: <b>{stage_name}</b>" if stage_name else ""

        return self.send(
            f"📊 <b>{period_label} — {date}</b>\n"
            f"{emoji} P&L del periodo: <b>{'+' if total_pnl >= 0 else ''}${total_pnl:.2f} USD</b>\n"
            f"{ops_line}\n"
            f"Saldo: ${starting_balance:.2f} → <b>${ending_balance:.2f} USD</b> ({sign}${balance_change:.2f})\n"
            f"{stage_line}"
            f"{positions_section}\n"
            f"¡Hasta el próximo reporte! 🚀"
        )

    def notify_skipped(
        self, symbol, direction="", score=0.0, reason="",
        min_required=0.0, usdt_total=0.0, margin_in_use=0.0,
        reserve=0.0, operable=0.0,
    ) -> bool:
        arrow = "📈" if direction == "long" else "📉" if direction == "short" else "⚪"
        direction_str = "LONG (SUBE)" if direction == "long" else "SHORT (BAJA)" if direction == "short" else ""
        signal_parts = [f"<b>{symbol}</b>"]
        if direction_str:
            signal_parts.append(direction_str)
        signal_line = f"{arrow} Señal: {' — '.join(signal_parts[:2])}"
        if score > 0:
            signal_line += f" | Score: <b>{score:.0f}/100</b>"
        reason_line = f"Motivo: {reason}" if reason else ""
        return self.send(
            f"⚠️ <b>OPERACIÓN SALTADA — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{signal_line}\n"
            f"{reason_line}\n"
            f"\n"
            f"💰 Saldo total: <b>${usdt_total:.2f} USDT</b>\n"
            f"🔒 Margen en uso: <b>${margin_in_use:.2f} USDT</b>\n"
            f"🏦 Reserva (10%): <b>${reserve:.2f} USDT</b>\n"
            f"✅ Saldo operable: <b>${operable:.2f} USDT</b>\n"
        )

    def notify_agent_started(
        self, balance=0.0, operable=0.0, margin_in_use=0.0, reserve=0.0,
        symbols=None,
    ) -> bool:
        if symbols:
            names = [s.replace("USDT", "") for s in symbols]
            symbols_line = f"Monitoreando: {', '.join(names)} ({len(names)} pares)"
        else:
            symbols_line = "Monitoreando activos configurados"
        return self.send(
            f"🤖 <b>AGENTE INICIADO</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Saldo total: <b>${balance:.2f} USDT</b>\n"
            f"🔒 Margen en uso: <b>${margin_in_use:.2f} USDT</b>\n"
            f"🏦 Reserva: <b>${reserve:.2f} USDT</b>\n"
            f"✅ Saldo operable: <b>${operable:.2f} USDT</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{symbols_line}\n"
            f"Te notificaré cada operación. 📱"
        )


# Alias para compatibilidad
WhatsAppNotifier = TelegramNotifier
