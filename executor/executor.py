"""
executor.py — Orquestador principal del executor
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import ccxt.async_support as ccxt
from dotenv import load_dotenv

from brain.decision import TradeDecision
from .balance import BalanceChecker, BalanceInfo, MIN_TRADE_AMOUNT_USD
from .notifier import WhatsAppNotifier
from .order_executor import OrderExecutor, OrderResult

load_dotenv(override=False)
logger = logging.getLogger(__name__)

# Position sizing — Fixed Fractional method (Position Sizer skill)
# Nunca arriesgar más del RISK_PCT del capital operable por trade
RISK_PCT = float(os.getenv("RISK_PCT", "1.0")) / 100   # default 1%
# ATR multiplier para calcular distancia al stop (Turtle Traders: 2x)
ATR_MULTIPLIER = float(os.getenv("ATR_MULTIPLIER", "1.5"))  # 1.5x para swing corto


class TradingExecutor:

    def __init__(self, exchange: ccxt.binance, testnet: bool = True):
        self.exchange = exchange
        self.testnet  = testnet

        self.balance_checker = BalanceChecker(exchange)
        self.order_executor  = OrderExecutor(exchange, testnet)

        try:
            self.notifier = WhatsAppNotifier()
            self.notifications_enabled = True
        except EnvironmentError as e:
            logger.warning(f"Telegram deshabilitado: {e}")
            self.notifier = None
            self.notifications_enabled = False

        self.alert_yellow_pct = float(os.getenv("ALERT_YELLOW_PCT", "30")) / 100
        self.alert_orange_pct = float(os.getenv("ALERT_ORANGE_PCT", "20")) / 100
        self.alert_red_pct    = float(os.getenv("ALERT_RED_PCT",    "10")) / 100
        self.vobo_timeout_min = int(os.getenv("VOBO_TIMEOUT_MIN", "10"))
        self.max_capital_pct  = float(os.getenv("MAX_CAPITAL_PCT", "60")) / 100

        self._daily_starting_balance: Optional[float] = None
        self._last_alert_level: Optional[str] = None
        self._daily_trades = []
        self._committed_usd: float = 0.0

        logger.info(
            f"TradingExecutor inicializado | "
            f"Modo: {'TESTNET' if testnet else 'PRODUCCION'} | "
            f"Notificaciones: {'ON' if self.notifications_enabled else 'OFF'} | "
            f"Capital máximo simultáneo: {self.max_capital_pct*100:.0f}% | "
            f"Riesgo por trade: {RISK_PCT*100:.1f}%"
        )

    def commit_capital(self, amount_usd: float):
        self._committed_usd += amount_usd
        logger.info(f"Capital comprometido: ${self._committed_usd:.2f} USD")

    def release_capital(self, amount_usd: float):
        self._committed_usd = max(0.0, self._committed_usd - amount_usd)
        logger.info(f"Capital liberado. Comprometido ahora: ${self._committed_usd:.2f} USD")

    def available_capital(self, balance: BalanceInfo) -> float:
        max_allowed = balance.usdt_free * self.max_capital_pct
        return max(0.0, max_allowed - self._committed_usd)

    def _calculate_position_size(
        self,
        decision: TradeDecision,
        balance: BalanceInfo,
        capital_disponible: float
    ) -> float:
        """
        Calcula el tamaño de posición usando Fixed Fractional method.

        Basado en Position Sizer skill:
        - dollar_risk = capital_operable * RISK_PCT (1% por defecto)
        - Si hay ATR: stop_distance = ATR * ATR_MULTIPLIER
          amount = dollar_risk / stop_distance * entry_price
        - Si no hay ATR: usa porcentaje de capital según volumen (método anterior)

        El skill recomienda nunca exceder 2% de riesgo. Con $29 capital:
        - 1% riesgo = $0.29 máximo a perder por trade
        - Esto es muy conservador pero correcto para cuentas pequeñas
        """
        dollar_risk = balance.operable * RISK_PCT
        atr = getattr(decision, 'atr_14', 0.0) or 0.0

        if atr > 0 and decision.stop_loss > 0:
            # ── ATR-based sizing ──────────────────────────────────────────
            stop_distance = atr * ATR_MULTIPLIER
            entry_price   = decision.stop_loss  # aproximado pre-fill

            if stop_distance > 0 and entry_price > 0:
                # Cuántas unidades podemos comprar dado el riesgo en USD
                units = dollar_risk / stop_distance
                amount_atr = units * entry_price

                logger.info(
                    f"ATR sizing: ATR={atr:.4f} | "
                    f"Stop dist={stop_distance:.4f} ({ATR_MULTIPLIER}x ATR) | "
                    f"Dollar risk=${dollar_risk:.2f} ({RISK_PCT*100:.1f}%) | "
                    f"Monto ATR=${amount_atr:.2f}"
                )
                return amount_atr
        
        # ── Fallback: Fixed Fractional por volumen ────────────────────────
        volume_ratio = getattr(decision, 'volume_ratio', 1.0) or 1.0
        if volume_ratio < 0.5:
            return balance.operable * 0.15
        elif volume_ratio < 0.8:
            return balance.operable * 0.20
        elif volume_ratio < 1.2:
            return balance.operable * 0.30
        else:
            return balance.operable * 0.40

    async def check_balance(self) -> Optional[BalanceInfo]:
        balance = await self.balance_checker.get_balance()

        if balance is None:
            if self.notifications_enabled:
                self.notifier.notify_critical_error(
                    "No se pudo conectar con Binance para consultar el saldo."
                )
            return None

        if self._daily_starting_balance is None:
            self._daily_starting_balance = balance.usdt_free
            logger.info(f"Saldo inicial del dia: ${self._daily_starting_balance:.2f} USD")

        await self._check_capital_alerts(balance)

        if self._last_alert_level == "red":
            logger.critical("Capital en reserva mínima — agente detenido")
            return None

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
        if not decision.should_trade:
            logger.info(f"Claude decidió no operar: {decision.reason_not_trade}")
            return None

        # ── Verificar que no hay posición abierta del mismo par en Binance ─
        try:
            positions = await self.exchange.fetch_positions()
            for p in positions:
                if p.get("contracts") and float(p["contracts"]) > 0:
                    sym = p["symbol"]
                    base = sym.split("/")[0] if "/" in sym else sym
                    binance_symbol = base + "USDT"
                    if binance_symbol == decision.symbol:
                        logger.info(
                            f"Par {decision.symbol} ya tiene posición abierta en Binance — saltando"
                        )
                        return None
        except Exception as e:
            logger.warning(f"No se pudo verificar posiciones en Binance: {e}")
        # ──────────────────────────────────────────────────────────────────

        # ── Control de capital comprometido ───────────────────────────────
        capital_disponible = self.available_capital(balance)
        if capital_disponible < MIN_TRADE_AMOUNT_USD:
            logger.warning(
                f"Capital disponible: ${capital_disponible:.2f} — "
                f"comprometido: ${self._committed_usd:.2f}. Saltando {decision.symbol}."
            )
            return None

        # ── Calcular tamaño de posición con Fixed Fractional / ATR ────────
        decision.amount_usd = self._calculate_position_size(
            decision, balance, capital_disponible
        )

        # Nunca superar el capital disponible
        decision.amount_usd = min(decision.amount_usd, capital_disponible)

        # Subir al mínimo si quedó por debajo
        if decision.amount_usd < MIN_TRADE_AMOUNT_USD:
            if balance.operable >= MIN_TRADE_AMOUNT_USD:
                logger.info(
                    f"Monto ajustado de ${decision.amount_usd:.2f} "
                    f"al mínimo de ${MIN_TRADE_AMOUNT_USD:.2f}"
                )
                decision.amount_usd = MIN_TRADE_AMOUNT_USD
            else:
                logger.warning(
                    f"Capital insuficiente para el mínimo — saltando {decision.symbol}"
                )
                return None

        logger.warning(
            f"Monto final: ${decision.amount_usd:.2f} "
            f"(operable: ${balance.operable:.2f}, "
            f"comprometido: ${self._committed_usd:.2f}, "
            f"disponible: ${capital_disponible:.2f})"
        )

        if decision.is_autonomous:
            return await self._execute_autonomous(decision, balance)
        else:
            return await self._execute_with_vobo(decision, balance)

    async def _execute_autonomous(
        self,
        decision: TradeDecision,
        balance: BalanceInfo
    ) -> Optional[OrderResult]:
        logger.info(
            f"Ejecutando operacion autonoma: {decision.symbol} "
            f"{decision.direction.upper()} ${decision.amount_usd:.2f}"
        )

        result = await self.order_executor.execute(decision)

        if result.success:
            self.commit_capital(decision.amount_usd)
            # Notificar con precio REAL de ejecución
            if self.notifications_enabled and os.getenv("NOTIFY_ON_ENTRY", "true") == "true":
                self.notifier.notify_trade_opened(
                    symbol=decision.symbol,
                    direction=decision.direction,
                    amount_usd=decision.amount_usd,
                    entry_price=result.entry_price,   # precio real de Binance
                    stop_loss=decision.stop_loss,
                    take_profit=decision.take_profit,
                    leverage=decision.leverage,
                    reasoning=decision.reasoning,
                    account_balance=balance.usdt_free,
                    trade_amount=decision.amount_usd
                )
            self._daily_trades.append({
                "symbol":      decision.symbol,
                "direction":   decision.direction,
                "amount":      decision.amount_usd,
                "entry_price": result.entry_price,
                "opened_at":   datetime.now(timezone.utc)
            })
            logger.info(f"Operacion abierta exitosamente: {result.order_id}")

            if result.error_msg:
                if self.notifications_enabled:
                    self.notifier.notify_critical_error(
                        f"⚠️ {decision.symbol}: posición abierta pero SL/TP falló. "
                        f"El monitor intentará reponerlos en el próximo ciclo."
                    )
        else:
            logger.error(f"Fallo al abrir operacion: {result.error_msg}")
            if self.notifications_enabled:
                self.notifier.notify_critical_error(
                    f"Error al ejecutar orden {decision.symbol}: {result.error_msg}"
                )

        return result

    async def _execute_with_vobo(
        self,
        decision: TradeDecision,
        balance: BalanceInfo
    ) -> Optional[OrderResult]:
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
                timeout_min=self.vobo_timeout_min,
                account_balance=balance.usdt_free,
                trade_amount=decision.amount_usd
            )

        logger.info(f"VoBo enviado — esperando respuesta por {self.vobo_timeout_min} min")
        return None

    async def notify_trade_closed(
        self,
        symbol: str,
        direction: str,
        pnl_usd: float,
        pnl_pct: float,
        duration_min: int,
        close_reason: str,
        amount_usd: float = 0.0
    ):
        if amount_usd > 0:
            self.release_capital(amount_usd)

        if self.notifications_enabled and os.getenv("NOTIFY_ON_EXIT", "true") == "true":
            self.notifier.notify_trade_closed(
                symbol=symbol, direction=direction,
                pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                duration_min=duration_min, close_reason=close_reason
            )

    async def send_daily_report(self, current_balance: float, open_positions: list = None):
        if not self.notifications_enabled:
            return
        starting     = self._daily_starting_balance or current_balance
        total_trades = len(self._daily_trades)
        total_pnl    = current_balance - starting
        self.notifier.notify_daily_report(
            date=datetime.now().strftime("%d/%m/%Y"),
            total_trades=total_trades,
            winning_trades=0, losing_trades=0,
            total_pnl=total_pnl, win_rate=0.0,
            starting_balance=starting, ending_balance=current_balance,
            open_positions=open_positions or []
        )
        self._daily_starting_balance = None
        self._daily_trades = []
        self._last_alert_level = None
        self._committed_usd = 0.0

    async def _check_capital_alerts(self, balance: BalanceInfo):
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
        if new_level and new_level != self._last_alert_level:
            self._last_alert_level = new_level
            if self.notifications_enabled:
                self.notifier.notify_capital_alert(
                    level=new_level,
                    current_balance=balance.usdt_free,
                    initial_balance=self._daily_starting_balance,
                    pct_remaining=pct_remaining * 100
                )
