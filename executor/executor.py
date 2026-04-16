"""
executor.py — Orquestador principal del executor
v0.7.2 — RISK_PCT dinámico por etapa del agente.
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

# RISK_PCT ahora se lee dinámicamente desde la etapa del agente
# El .env sirve como fallback si no hay etapa configurada
RISK_PCT_FALLBACK = float(os.getenv("RISK_PCT", "2.0")) / 100
ATR_MULTIPLIER = float(os.getenv("ATR_MULTIPLIER", "1.5"))

# Risk por etapa (debe coincidir con learning.py STAGES)
STAGE_RISK_PCT = {
    1: 2.0,   # Aprendiz
    2: 3.0,   # Practicante
    3: 4.0,   # Competente
    4: 5.0,   # Experto
}


def get_risk_pct() -> float:
    """Lee el RISK_PCT dinámico basado en AGENT_STAGE del .env."""
    stage = int(os.getenv("AGENT_STAGE", "1"))
    pct = STAGE_RISK_PCT.get(stage, 2.0)
    return pct / 100


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

        self.order_executor.notifier = self.notifier

        self.alert_yellow_pct = float(os.getenv("ALERT_YELLOW_PCT", "30")) / 100
        self.alert_orange_pct = float(os.getenv("ALERT_ORANGE_PCT", "20")) / 100
        self.alert_red_pct    = float(os.getenv("ALERT_RED_PCT",    "10")) / 100
        self.vobo_timeout_min = int(os.getenv("VOBO_TIMEOUT_MIN", "10"))
        self.max_capital_pct  = float(os.getenv("MAX_CAPITAL_PCT", "60")) / 100

        self._daily_starting_balance: Optional[float] = None
        self._last_alert_level: Optional[str] = None
        self._daily_trades = []
        self._committed_usd: float = 0.0
        self.db = None

        logger.info(
            f"TradingExecutor inicializado | "
            f"Modo: {'TESTNET' if testnet else 'PRODUCCION'} | "
            f"Notificaciones: {'ON' if self.notifications_enabled else 'OFF'} | "
            f"Capital máximo simultáneo: {self.max_capital_pct*100:.0f}% | "
            f"Riesgo por trade: {get_risk_pct()*100:.1f}% (Stage {os.getenv('AGENT_STAGE', '1')})"
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

    def _calculate_position_size(self, decision, balance, capital_disponible):
        risk_pct = get_risk_pct()
        dollar_risk = balance.operable * risk_pct
        atr = getattr(decision, 'atr_14', 0.0) or 0.0
        if atr > 0 and decision.stop_loss > 0:
            stop_distance = atr * ATR_MULTIPLIER
            entry_price   = decision.stop_loss
            if stop_distance > 0 and entry_price > 0:
                units = dollar_risk / stop_distance
                amount_atr = units * entry_price
                logger.info(
                    f"ATR sizing: ATR={atr:.4f} | "
                    f"Stop dist={stop_distance:.4f} ({ATR_MULTIPLIER}x ATR) | "
                    f"Dollar risk=${dollar_risk:.2f} ({risk_pct*100:.1f}%) | "
                    f"Monto ATR=${amount_atr:.2f}"
                )
                return amount_atr
        volume_ratio = getattr(decision, 'volume_ratio', 1.0) or 1.0
        if volume_ratio < 0.5:
            return balance.operable * 0.15
        elif volume_ratio < 0.8:
            return balance.operable * 0.20
        elif volume_ratio < 1.2:
            return balance.operable * 0.30
        else:
            return balance.operable * 0.40

    async def check_balance(self):
        balance = await self.balance_checker.get_balance()
        if balance is None:
            if self.notifications_enabled:
                self.notifier.notify_critical_error("No se pudo conectar con Binance para consultar el saldo.")
            return None
        if self._daily_starting_balance is None:
            self._daily_starting_balance = balance.usdt_free
            logger.info(f"Saldo inicial del dia: ${self._daily_starting_balance:.2f} USD")
        await self._check_capital_alerts(balance)
        if self._last_alert_level == "red":
            logger.critical("Capital en reserva mínima — agente detenido")
            return None
        if not balance.has_sufficient_funds:
            logger.warning(f"Sin fondos suficientes: ${balance.operable:.2f} disponible, mínimo ${balance.min_trade_amount:.2f}")
            return None
        return balance

    async def execute_decision(self, decision, balance):
        if not decision.should_trade:
            logger.info(f"Claude decidió no operar: {decision.reason_not_trade}")
            return None

        # Verificar posición abierta del mismo par en Binance
        try:
            positions = await self.exchange.fetch_positions()
            for p in positions:
                if p.get("contracts") and float(p["contracts"]) > 0:
                    sym = p["symbol"]
                    base = sym.split("/")[0] if "/" in sym else sym
                    binance_symbol = base + "USDT"
                    if binance_symbol == decision.symbol:
                        logger.info(f"Par {decision.symbol} ya tiene posición abierta en Binance — saltando")
                        return None
        except Exception as e:
            logger.warning(f"No se pudo verificar posiciones en Binance: {e}")

        if not balance.has_sufficient_funds:
            if self.notifications_enabled:
                self.notifier.notify_skipped(
                    symbol=decision.symbol, direction=decision.direction,
                    score=getattr(decision, "score", 0.0),
                    reason=f"Saldo operable ${balance.operable:.2f} < mínimo ${balance.min_trade_amount:.2f} USDT",
                    usdt_total=balance.usdt_total, margin_in_use=balance.margin_in_use,
                    reserve=balance.reserve, operable=balance.operable,
                )
            return None

        capital_disponible = self.available_capital(balance)
        if capital_disponible < MIN_TRADE_AMOUNT_USD:
            logger.warning(f"Capital disponible: ${capital_disponible:.2f} — comprometido: ${self._committed_usd:.2f}. Saltando {decision.symbol}.")
            return None

        decision.amount_usd = self._calculate_position_size(decision, balance, capital_disponible)
        decision.amount_usd = min(decision.amount_usd, capital_disponible)
        if decision.amount_usd < MIN_TRADE_AMOUNT_USD:
            if balance.operable >= MIN_TRADE_AMOUNT_USD:
                logger.info(f"Monto ajustado de ${decision.amount_usd:.2f} al mínimo de ${MIN_TRADE_AMOUNT_USD:.2f}")
                decision.amount_usd = MIN_TRADE_AMOUNT_USD
            else:
                logger.warning(f"Capital insuficiente para el mínimo — saltando {decision.symbol}")
                return None

        logger.warning(
            f"Monto final: ${decision.amount_usd:.2f} "
            f"(operable: ${balance.operable:.2f}, comprometido: ${self._committed_usd:.2f}, disponible: ${capital_disponible:.2f})"
        )

        if decision.is_autonomous:
            return await self._execute_autonomous(decision, balance)
        else:
            return await self._execute_with_vobo(decision, balance)

    async def _execute_autonomous(self, decision, balance):
        logger.info(f"Ejecutando operacion autonoma: {decision.symbol} {decision.direction.upper()} ${decision.amount_usd:.2f}")
        result = await self.order_executor.execute(decision)
        if result.success:
            self.commit_capital(decision.amount_usd)
            if self.notifications_enabled and os.getenv("NOTIFY_ON_ENTRY", "true") == "true":
                fresh_balance = await self.balance_checker.get_balance()
                bal = fresh_balance if fresh_balance else balance
                self.notifier.notify_trade_opened(
                    symbol=decision.symbol, direction=decision.direction,
                    amount_usd=decision.amount_usd, entry_price=result.entry_price,
                    stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                    leverage=decision.leverage, reasoning=decision.reasoning,
                    account_balance=bal.usdt_free, trade_amount=decision.amount_usd,
                    usdt_total=bal.usdt_total, margin_in_use=bal.margin_in_use,
                    reserve=bal.reserve, operable=bal.operable,
                )
            self._daily_trades.append({
                "symbol": decision.symbol, "direction": decision.direction,
                "amount": decision.amount_usd, "entry_price": result.entry_price,
                "opened_at": datetime.now(timezone.utc)
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
                error_msg = result.error_msg or ""
                if "mínimo de binance" in error_msg.lower() or "min_cost" in error_msg.lower() or "menor al mínimo" in error_msg.lower():
                    try:
                        min_req = float(error_msg.split("$")[2].split(")")[0])
                    except Exception:
                        min_req = 0.0
                    self.notifier.notify_skipped(
                        symbol=decision.symbol, direction=decision.direction,
                        score=getattr(decision, "score", 0.0),
                        reason=f"Mínimo de Binance ${min_req:.2f} > saldo operable ${balance.operable:.2f} USDT",
                        usdt_total=balance.usdt_total, margin_in_use=balance.margin_in_use,
                        reserve=balance.reserve, operable=balance.operable,
                    )
                else:
                    self.notifier.notify_critical_error(f"Error al ejecutar orden {decision.symbol}: {result.error_msg}")
        return result

    async def _execute_with_vobo(self, decision, balance):
        logger.info(f"Solicitando VoBo para: {decision.symbol} {decision.direction.upper()} ${decision.amount_usd:.2f}")
        if self.notifications_enabled:
            self.notifier.notify_vobo_request(
                symbol=decision.symbol, direction=decision.direction,
                amount_usd=decision.amount_usd, entry_price=decision.stop_loss,
                stop_loss=decision.stop_loss, take_profit=decision.take_profit,
                leverage=decision.leverage, reasoning=decision.reasoning,
                timeout_min=self.vobo_timeout_min, account_balance=balance.usdt_free,
                trade_amount=decision.amount_usd
            )
        logger.info(f"VoBo enviado — esperando respuesta por {self.vobo_timeout_min} min")
        return None

    async def notify_trade_closed(
        self, symbol, direction, pnl_usd, pnl_pct,
        duration_min, close_reason, amount_usd=0.0,
        entry_price=0.0, exit_price=0.0,
    ):
        if amount_usd > 0:
            self.release_capital(amount_usd)
        self._daily_trades.append({
            "symbol": symbol, "direction": direction,
            "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "closed": True,
        })
        if self.notifications_enabled and os.getenv("NOTIFY_ON_EXIT", "true") == "true":
            fresh_balance = await self.balance_checker.get_balance()
            bal = fresh_balance
            self.notifier.notify_trade_closed(
                symbol=symbol, direction=direction,
                pnl_usd=pnl_usd, pnl_pct=pnl_pct,
                duration_min=duration_min, close_reason=close_reason,
                entry_price=entry_price, exit_price=exit_price,
                account_balance_after=bal.usdt_total if bal else 0.0,
                usdt_total=bal.usdt_total if bal else 0.0,
                margin_in_use=bal.margin_in_use if bal else 0.0,
                reserve=bal.reserve if bal else 0.0,
                operable=bal.operable if bal else 0.0,
            )

    async def send_daily_report(self, current_balance, open_positions=None):
        """
        v0.7.2: Reporte corregido.
        Muestra posiciones abiertas actuales + cerradas en el periodo.
        """
        if not self.notifications_enabled:
            return

        starting = self._daily_starting_balance or current_balance

        # Contar posiciones abiertas
        open_count = len(open_positions) if open_positions else 0

        # Trades cerrados del periodo desde BD
        total_trades   = 0
        winning_trades = 0
        losing_trades  = 0
        total_pnl      = 0.0
        win_rate       = 0.0
        closed_tp      = 0
        closed_sl      = 0

        try:
            if hasattr(self, 'db') and self.db:
                today = datetime.now().strftime("%Y-%m-%d")
                summary = self.db.get_daily_summary(today)
                total_trades   = summary.get("total_trades", 0)
                winning_trades = summary.get("winning_trades", 0)
                losing_trades  = summary.get("losing_trades", 0)
                total_pnl      = summary.get("total_pnl_usd", 0.0)
                win_rate       = summary.get("win_rate", 0.0)
                closed_tp      = winning_trades
                closed_sl      = losing_trades
        except Exception:
            closed_trades  = [t for t in self._daily_trades if t.get("closed")]
            total_trades   = len(closed_trades)
            winning_trades = len([t for t in closed_trades if t.get("pnl_usd", 0) > 0])
            losing_trades  = len([t for t in closed_trades if t.get("pnl_usd", 0) <= 0])
            win_rate       = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
            total_pnl      = sum(t.get("pnl_usd", 0) for t in closed_trades)
            closed_tp      = winning_trades
            closed_sl      = losing_trades

        # Obtener etapa actual
        stage = int(os.getenv("AGENT_STAGE", "1"))
        stage_names = {1: "Aprendiz", 2: "Practicante", 3: "Competente", 4: "Experto"}
        stage_name = stage_names.get(stage, "Aprendiz")

        self.notifier.notify_daily_report(
            date=datetime.now().strftime("%d/%m/%Y"),
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            total_pnl=total_pnl,
            win_rate=win_rate,
            starting_balance=starting,
            ending_balance=current_balance,
            open_positions=open_positions or [],
            open_count=open_count,
            closed_in_period=total_trades,
            closed_tp=closed_tp,
            closed_sl=closed_sl,
            stage_name=stage_name,
        )

        self._daily_starting_balance = None
        self._daily_trades = []
        self._last_alert_level = None
        self._committed_usd = 0.0

    async def _check_capital_alerts(self, balance):
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
                    level=new_level, current_balance=balance.usdt_free,
                    initial_balance=self._daily_starting_balance,
                    pct_remaining=pct_remaining * 100
                )
