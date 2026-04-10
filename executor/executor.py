"""
executor.py — Orquestador principal del executor
Responsabilidad: coordinar balance, notificaciones y ejecución
de órdenes. Es el punto de entrada de la Capa 5.

Flujo:
1. Consulta saldo real de Binance
2. Si no hay saldo → notifica por WhatsApp y detiene el ciclo
3. Si hay saldo → recibe decisión de Claude y ejecuta
4. Notifica resultado por WhatsApp
5. Monitorea alertas de capital
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import ccxt.async_support as ccxt
from dotenv import load_dotenv

from brain.decision import TradeDecision
from .balance import BalanceChecker, BalanceInfo
from .notifier import WhatsAppNotifier
from .order_executor import OrderExecutor, OrderResult

load_dotenv()
logger = logging.getLogger(__name__)


class TradingExecutor:
    """
    Orquestador de la Capa 5 — ejecuta decisiones de Claude
    con verificación de saldo y notificaciones completas.

    Uso:
        executor = TradingExecutor(exchange)
        await executor.initialize()

        # Verificar saldo antes de analizar
        balance = await executor.check_balance()
        if not balance:
            return  # Ya notificó por WhatsApp

        # Ejecutar decisión de Claude
        result = await executor.execute_decision(decision, balance)
    """

    def __init__(self, exchange: ccxt.binance, testnet: bool = True):
        self.exchange = exchange
        self.testnet = testnet

        self.balance_checker = BalanceChecker(exchange)
        self.order_executor = OrderExecutor(exchange, testnet)

        # Notifier puede fallar si no hay credenciales de Twilio
        try:
            self.notifier = WhatsAppNotifier()
            self.notifications_enabled = True
        except EnvironmentError as e:
            logger.warning(f"WhatsApp deshabilitado: {e}")
            self.notifier = None
            self.notifications_enabled = False

        # Parámetros de alertas
        self.alert_yellow_pct = float(os.getenv("ALERT_YELLOW_PCT", "30")) / 100
        self.alert_orange_pct = float(os.getenv("ALERT_ORANGE_PCT", "20")) / 100
        self.alert_red_pct = float(os.getenv("ALERT_RED_PCT", "10")) / 100
        self.vobo_timeout_min = int(os.getenv("VOBO_TIMEOUT_MIN", "10"))

        # Estado del día
        self._daily_starting_balance: Optional[float] = None
        self._last_alert_level: Optional[str] = None

        # Historial del día para el resumen
        self._daily_trades = []

        logger.info(
            f"TradingExecutor inicializado | "
            f"Modo: {'TESTNET' if testnet else 'PRODUCCION'} | "
            f"Notificaciones: {'ON' if self.notifications_enabled else 'OFF'}"
        )

    async def check_balance(self) -> Optional[BalanceInfo]:
        """
        Verifica el saldo disponible en Binance.

        Si no hay saldo suficiente, envía notificación por WhatsApp
        y retorna None para detener el ciclo.

        Si hay saldo, retorna la información del balance para
        que el analizador y Claude la usen.
        """
        balance = await self.balance_checker.get_balance()

        if balance is None:
            # Error de conexión con Binance
            if self.notifications_enabled:
                self.notifier.notify_critical_error(
                    "No se pudo conectar con Binance para consultar el saldo. "
                    "Verifica tu conexion y las credenciales API."
                )
            return None

        # Guardar saldo inicial del día (primera vez que corre)
        if self._daily_starting_balance is None:
            self._daily_starting_balance = balance.usdt_free
            logger.info(
                f"Saldo inicial del dia: ${self._daily_starting_balance:.2f} USD"
            )

        # Verificar alertas de capital
        await self._check_capital_alerts(balance)

        # Si la alerta es roja — detener completamente
        if self._last_alert_level == "red":
            logger.critical("Capital en reserva mínima — agente detenido")
            return None

        # Verificar si hay fondos suficientes
        if not balance.has_sufficient_funds:
            logger.warning(
                f"Sin fondos suficientes: ${balance.operable:.2f} disponible, "
                f"mínimo ${balance.min_trade_amount:.2f}"
            )
            if self.notifications_enabled:
                self.notifier.notify_no_funds(
                    usdt_free=balance.usdt_free,
                    min_required=balance.min_trade_amount
                )
            return None

        return balance

    async def execute_decision(
        self,
        decision: TradeDecision,
        balance: BalanceInfo
    ) -> Optional[OrderResult]:
        """
        Ejecuta la decisión de Claude.

        Si requiere VoBo → envía solicitud y espera respuesta.
        Si es autónoma → ejecuta directamente y notifica.
        """
        if not decision.should_trade:
            logger.info(f"Claude decidió no operar: {decision.reason_not_trade}")
            return None

        # Verificar que el monto no supere el capital operable
        if decision.amount_usd > balance.operable:
            decision.amount_usd = balance.operable * 0.4
            logger.warning(
                f"Monto ajustado a ${decision.amount_usd:.2f} "
                f"(capital operable: ${balance.operable:.2f})"
            )

        # Decisión autónoma (monto <= umbral VoBo)
        if decision.is_autonomous:
            return await self._execute_autonomous(decision)

        # Decisión que requiere VoBo
        else:
            return await self._execute_with_vobo(decision)

    async def _execute_autonomous(
        self,
        decision: TradeDecision
    ) -> Optional[OrderResult]:
        """Ejecuta una operación autónoma sin esperar aprobación."""
        logger.info(
            f"Ejecutando operacion autonoma: {decision.symbol} "
            f"{decision.direction.upper()} ${decision.amount_usd:.2f}"
        )

        # Notificar entrada
        if self.notifications_enabled and os.getenv("NOTIFY_ON_ENTRY", "true") == "true":
            self.notifier.notify_trade_opened(
                symbol=decision.symbol,
                direction=decision.direction,
                amount_usd=decision.amount_usd,
                entry_price=decision.stop_loss,  # Aproximado
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                leverage=decision.leverage,
                reasoning=decision.reasoning
            )

        # Ejecutar orden
        result = await self.order_executor.execute(decision)

        if result.success:
            # Guardar para el resumen diario
            self._daily_trades.append({
                "symbol": decision.symbol,
                "direction": decision.direction,
                "amount": decision.amount_usd,
                "entry_price": result.entry_price,
                "opened_at": datetime.now(timezone.utc)
            })
            logger.info(f"Operacion abierta exitosamente: {result.order_id}")
        else:
            logger.error(f"Fallo al abrir operacion: {result.error_msg}")
            if self.notifications_enabled:
                self.notifier.notify_critical_error(
                    f"Error al ejecutar orden {decision.symbol}: {result.error_msg}"
                )

        return result

    async def _execute_with_vobo(
        self,
        decision: TradeDecision
    ) -> Optional[OrderResult]:
        """
        Solicita VoBo al operador y espera su respuesta.
        El webhook de Flask recibe la respuesta del operador.
        """
        logger.info(
            f"Solicitando VoBo para: {decision.symbol} "
            f"{decision.direction.upper()} ${decision.amount_usd:.2f}"
        )

        if self.notifications_enabled:
            self.notifier.notify_vobo_request(
                symbol=decision.symbol,
                direction=decision.direction,
                amount_usd=decision.amount_usd,
                entry_price=decision.stop_loss,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                leverage=decision.leverage,
                reasoning=decision.reasoning,
                timeout_min=self.vobo_timeout_min
            )

        # El VoBo handler (Flask webhook) procesará la respuesta
        # Por ahora retornamos None — el webhook ejecutará cuando llegue el SI
        logger.info(
            f"VoBo enviado — esperando respuesta por {self.vobo_timeout_min} min"
        )
        return None

    async def notify_trade_closed(
        self,
        symbol: str,
        direction: str,
        pnl_usd: float,
        pnl_pct: float,
        duration_min: int,
        close_reason: str
    ):
        """Notifica el cierre de una posición."""
        if self.notifications_enabled and os.getenv("NOTIFY_ON_EXIT", "true") == "true":
            self.notifier.notify_trade_closed(
                symbol=symbol,
                direction=direction,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                duration_min=duration_min,
                close_reason=close_reason
            )

    async def send_daily_report(self, current_balance: float):
        """Envía el resumen diario a las 10pm hora México."""
        if not self.notifications_enabled:
            return

        starting = self._daily_starting_balance or current_balance
        total_trades = len(self._daily_trades)
        total_pnl = current_balance - starting

        self.notifier.notify_daily_report(
            date=datetime.now().strftime("%d/%m/%Y"),
            total_trades=total_trades,
            winning_trades=0,   # Se actualizará cuando implementemos el tracking completo
            losing_trades=0,
            total_pnl=total_pnl,
            win_rate=0.0,
            starting_balance=starting,
            ending_balance=current_balance
        )

        # Resetear para el día siguiente
        self._daily_starting_balance = None
        self._daily_trades = []
        self._last_alert_level = None

    async def _check_capital_alerts(self, balance: BalanceInfo):
        """Verifica y envía alertas de capital según los umbrales."""
        if not self._daily_starting_balance:
            return

        pct_remaining = balance.usdt_free / self._daily_starting_balance

        new_level = None
        if pct_remaining <= self.alert_red_pct:
            new_level = "red"
        elif pct_remaining <= self.alert_orange_pct:
            new_level = "orange"
        elif pct_remaining <= self.alert_yellow_pct:
            new_level = "yellow"

        # Solo notificar si el nivel cambió
        if new_level and new_level != self._last_alert_level:
            self._last_alert_level = new_level
            if self.notifications_enabled:
                self.notifier.notify_capital_alert(
                    level=new_level,
                    current_balance=balance.usdt_free,
                    initial_balance=self._daily_starting_balance,
                    pct_remaining=pct_remaining * 100
                )
