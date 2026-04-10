"""
prompt_builder.py — Constructor del prompt para Claude
Responsabilidad: tomar toda la información del analizador y el
contexto macro y construir un prompt claro para que Claude decida.

Un buen prompt es la diferencia entre un agente inteligente y uno torpe.
"""

import os
import logging
from dotenv import load_dotenv

from collector.models import CollectedSnapshot
from analyzer.analyzer import TradingSignal

load_dotenv()
logger = logging.getLogger(__name__)


# System prompt — define la personalidad y reglas del agente
SYSTEM_PROMPT = """Eres un agente de trading algoritmico experto en criptomonedas.
Tu trabajo es analizar señales técnicas y de mercado para decidir si ejecutar
una operación de trading, con qué monto y bajo qué parámetros.

REGLAS INVIOLABLES:
1. Nunca arriesgues más del capital disponible menos la reserva del 10%
2. El ratio mínimo riesgo/recompensa es 1:2 (take-profit debe ser el doble del stop-loss)
3. Si las señales son contradictorias o el contexto es incierto, NO operes
4. Spot solo puede ser LONG — nunca short en spot
5. Futuros puede ser LONG o SHORT según la tendencia
6. Respeta siempre los activos en HOLD — nunca los toques
7. En mercados con Fear & Greed extremo (< 15 o > 85), sé muy conservador

ESTRATEGIA DE TRADING:
- Indicador más importante: VOLUMEN (confirma si el movimiento es real)
- Tendencia: Claude decide según contexto (both long y short son válidos en futuros)
- Stop-loss: dinámico basado en soporte/resistencia más cercano
- Apalancamiento: 1x por defecto, 2x solo con score >= 80 y contexto muy favorable

FORMATO DE RESPUESTA:
Responde ÚNICAMENTE con un JSON válido con esta estructura exacta:
{
  "should_trade": true/false,
  "reason_not_trade": "explicación si should_trade es false, null si es true",
  "symbol": "BTCUSDT",
  "direction": "long/short",
  "amount_usd": 75.00,
  "stop_loss": 71500.00,
  "take_profit": 74500.00,
  "leverage": "1x",
  "trading_mode": "futures/spot_tier1/spot_tier2/spot_tier3",
  "reasoning": "Explicación clara en español de por qué tomas esta decisión",
  "confidence": 0.75
}"""


class PromptBuilder:
    """
    Construye el prompt completo para Claude con toda la información
    necesaria para tomar una decisión de trading.
    """

    def __init__(self):
        self.reserve_pct = float(os.getenv("RESERVE_PCT", "10")) / 100
        self.vobo_min_pct = float(os.getenv("VOBO_MIN_PCT", "15")) / 100
        self.hold_symbols = os.getenv("HOLD_SYMBOLS", "").split(",")

    def build(
        self,
        signal: TradingSignal,
        snapshot: CollectedSnapshot,
        available_capital: float
    ) -> str:
        """
        Construye el prompt completo para Claude.

        signal:            La señal detectada por el analizador
        snapshot:          El snapshot completo con contexto macro
        available_capital: Capital disponible en USDT (ya descontada la reserva)
        """
        ctx = snapshot.market_context
        ind = signal.indicators_1h

        vobo_threshold = available_capital * self.vobo_min_pct

        prompt = f"""=== SEÑAL DE TRADING DETECTADA ===

ACTIVO: {signal.symbol}
MODO: {signal.trading_mode}
DIRECCIÓN SUGERIDA: {signal.direction.upper()}
SCORE TÉCNICO: {signal.score:.0f}/100

=== INDICADORES TÉCNICOS (1h) ===
Precio actual:    ${signal.current_price:,.4f}
RSI:              {ind.rsi.value:.1f} ({ind.rsi.signal})
MACD:             {ind.macd.signal}
Bollinger:        {ind.bollinger.signal} — precio al {ind.bollinger.percent_b*100:.0f}% de las bandas
Volumen:          {ind.volume.ratio:.1f}x el promedio ({ind.volume.signal})
Tendencia EMA:    {ind.trend}
Dirección sugerida por indicadores: {ind.suggested_direction}

=== NIVELES DE PRECIO ===
Soporte más cercano:    ${signal.levels.nearest_support:,.4f} ({signal.levels.supports[0].distance_pct:.1f}% abajo si hay soportes else "N/A")
Resistencia más cercana: ${signal.levels.nearest_resistance:,.4f} ({signal.levels.resistances[0].distance_pct:.1f}% arriba si hay resistencias else "N/A")
Stop-loss dinámico LONG:  ${signal.levels.dynamic_stop_loss_long:,.4f} (riesgo: {signal.levels.risk_pct_long:.1f}%)
Stop-loss dinámico SHORT: ${signal.levels.dynamic_stop_loss_short:,.4f} (riesgo: {signal.levels.risk_pct_short:.1f}%)

=== CONTEXTO MACRO ===
BTC Dominance:    {ctx.btc_dominance:.1f}%
Market Cap total: ${ctx.total_market_cap_usd/1e12:.2f}T USD
Volumen 24h:      ${ctx.total_volume_24h_usd/1e9:.1f}B USD
Fear & Greed:     {ctx.fear_greed_index} — {ctx.fear_greed_label}
Sentimiento:      {ctx.market_sentiment}

=== ALERTAS DE BALLENAS (últimas 4h) ===
{self._format_whale_alerts(snapshot)}

=== CAPITAL DISPONIBLE ===
Capital operable:        ${available_capital:.2f} USD
Umbral VoBo:             ${vobo_threshold:.2f} USD (operaciones > este monto requieren aprobación)
Activos en HOLD:         {', '.join(self.hold_symbols) if self.hold_symbols else 'ninguno'}

=== TU DECISION ===
Analiza toda la información anterior y decide:
1. ¿Debes operar ahora o esperar?
2. Si operas: ¿cuánto monto en USD es apropiado dado el capital disponible?
3. ¿Qué stop-loss y take-profit son los más adecuados?
4. ¿Es apropiado usar apalancamiento 2x o mejor 1x?

Recuerda: el monto que elijas debe ser menor o igual a ${available_capital:.2f} USD.
Si el monto supera ${vobo_threshold:.2f} USD, el operador debe aprobar antes de ejecutar.

Responde solo con el JSON."""

        return prompt

    def _format_whale_alerts(self, snapshot: CollectedSnapshot) -> str:
        """Formatea las alertas de ballenas para incluir en el prompt."""
        if not snapshot.whale_alerts:
            return "Sin alertas de ballenas en las últimas 4 horas"

        lines = []
        for alert in snapshot.whale_alerts[:3]:  # Máximo 3 alertas
            signal_type = ""
            if alert.is_bearish_signal:
                signal_type = "SEÑAL BAJISTA — posible venta próxima"
            elif alert.is_bullish_signal:
                signal_type = "SEÑAL ALCISTA — posible acumulación"

            lines.append(
                f"  {alert.symbol}: ${alert.amount_usd/1e6:.1f}M USD "
                f"— {alert.transaction_type} {signal_type}"
            )

        return "\n".join(lines)
