"""
decision.py — Estructura de la decisión de Claude
Define exactamente qué decide Claude y cómo se representa.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeDecision:
    """
    La decisión que toma Claude después de analizar todos los datos.
    Esta decisión va al executor que la ejecuta en Binance.
    """
    # ¿Opera o no?
    should_trade: bool
    reason_not_trade: Optional[str]  # Por qué no opera (si should_trade=False)

    # Detalles de la operación (si should_trade=True)
    symbol: str
    direction: str              # "long" | "short"
    amount_usd: float           # Monto en USD a invertir
    stop_loss: float            # Precio de stop-loss
    take_profit: float          # Precio de take-profit
    leverage: str               # "1x" | "2x"
    trading_mode: str           # "futures" | "spot_tier1" | etc

    # Razonamiento de Claude en lenguaje natural
    reasoning: str              # Explicación completa para el WhatsApp
    confidence: float           # Confianza de Claude 0-1

    # Flags de operación
    requires_vobo: bool         # True si necesita aprobación del operador
    is_autonomous: bool         # True si opera solo

    @property
    def whatsapp_entry_message(self) -> str:
        """Mensaje de WhatsApp cuando ENTRA una operación autónoma."""
        direction_emoji = "↑" if self.direction == "long" else "↓"
        mode = "Futuros" if self.trading_mode == "futures" else "Spot"
        return (
            f"OPERACION ABIERTA {direction_emoji}\n"
            f"{'─'*30}\n"
            f"Activo:    {self.symbol}\n"
            f"Modo:      {mode} {self.leverage}\n"
            f"Direccion: {self.direction.upper()}\n"
            f"Monto:     ${self.amount_usd:.2f} USD\n"
            f"Stop-loss: ${self.stop_loss:,.4f}\n"
            f"Take-profit: ${self.take_profit:,.4f}\n"
            f"{'─'*30}\n"
            f"Razon: {self.reasoning[:200]}"
        )

    @property
    def whatsapp_vobo_message(self) -> str:
        """Mensaje de WhatsApp pidiendo VoBo para operaciones grandes."""
        direction_emoji = "↑" if self.direction == "long" else "↓"
        mode = "Futuros" if self.trading_mode == "futures" else "Spot"
        return (
            f"SOLICITUD DE APROBACION {direction_emoji}\n"
            f"{'─'*30}\n"
            f"Activo:    {self.symbol}\n"
            f"Modo:      {mode} {self.leverage}\n"
            f"Direccion: {self.direction.upper()}\n"
            f"Monto:     ${self.amount_usd:.2f} USD\n"
            f"Stop-loss: ${self.stop_loss:,.4f}\n"
            f"Take-profit: ${self.take_profit:,.4f}\n"
            f"{'─'*30}\n"
            f"Analisis: {self.reasoning[:300]}\n"
            f"{'─'*30}\n"
            f"Responde SI para aprobar o NO para cancelar\n"
            f"(Expira en 10 minutos)"
        )
