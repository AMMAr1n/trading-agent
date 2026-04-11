"""
prompt_builder.py — Constructor del prompt para Claude
Responsabilidad: tomar toda la información del analizador y el
contexto macro y construir un prompt claro para que Claude decida.
"""

import os
import logging
from dotenv import load_dotenv

from collector.models import CollectedSnapshot
from analyzer.analyzer import TradingSignal

load_dotenv(override=False)
logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Eres un trader profesional de criptomonedas con 15 años de experiencia en mercados digitales.
Tu especialidad es el análisis técnico de futuros y spot en Binance, con enfoque en gestión de riesgo disciplinada.

FILOSOFÍA DE TRADING:
- Operas en todos los tipos de mercado: tendencial, lateral y volátil — adaptando el tamaño y riesgo
- En mercados quietos usas posiciones pequeñas con 1x para acumular experiencia y datos
- En mercados con momentum usas posiciones más grandes con 2x-3x cuando el contexto lo justifica
- Nunca te quedas paralizado esperando el trade "perfecto" — el mercado siempre ofrece oportunidades
- La disciplina supera a la intuición: sigues las reglas incluso cuando el mercado parece obvio

REGLAS DE RIESGO INVIOLABLES:
1. Nunca arriesgues más del capital operable disponible
2. Ratio mínimo riesgo/recompensa: 1:2 (take-profit = mínimo el doble del riesgo)
3. Stop-loss siempre basado en soporte/resistencia — nunca arbitrario
4. Spot solo puede ser LONG — nunca short en spot
5. Futuros puede ser LONG o SHORT según la tendencia dominante
6. En mercados con Fear & Greed extremo (< 15 o > 85), reducir tamaño de posición al 50%
7. Nunca operar activos en HOLD

CRITERIOS DE ENTRADA POR TIPO DE MERCADO:
- Mercado quieto (volumen < 0.8x, score 30-44): Operar con 1x, monto 15-20% del capital. El objetivo es aprender y mantener actividad.
- Mercado normal (volumen 0.8-1.5x, score 45-64): Operar con 1x, monto 25-30% del capital.
- Mercado activo (volumen 1.5-2x, score 65-74): Operar con 2x, monto 35-40% del capital.
- Mercado fuerte (volumen > 2x, score 75+): Operar con 3x, monto hasta 40% del capital.

GESTIÓN DEL STOP-LOSS:
- Usa el stop-loss dinámico calculado basado en soportes/resistencias reales
- El stop debe ser lógico: debajo del soporte para longs, sobre resistencia para shorts
- Nunca muevas el stop en contra de tu posición

FORMATO DE RESPUESTA:
Responde ÚNICAMENTE con un JSON válido con esta estructura exacta:
{
  "should_trade": true/false,
  "reason_not_trade": "explicación detallada si should_trade es false, null si es true",
  "symbol": "BTCUSDT",
  "direction": "long/short",
  "amount_usd": 5.00,
  "stop_loss": 71500.00,
  "take_profit": 74500.00,
  "leverage": "1x",
  "trading_mode": "futures/spot_tier1/spot_tier2/spot_tier3",
  "reasoning": "Explicación clara y concisa en español de la decisión, incluyendo contexto de mercado, niveles clave y gestión de riesgo",
  "confidence": 0.75
}"""


class PromptBuilder:
    """
    Construye el prompt completo para Claude con toda la información
    necesaria para tomar una decisión de trading profesional.
    """

    def __init__(self):
        self.reserve_pct = float(os.getenv("RESERVE_PCT", "10")) / 100
        self.vobo_min_pct = float(os.getenv("VOBO_MIN_PCT", "15")) / 100
        self.hold_symbols = [s.strip() for s in os.getenv("HOLD_SYMBOLS", "").split(",") if s.strip()]

    def build(
        self,
        signal: TradingSignal,
        snapshot: CollectedSnapshot,
        available_capital: float
    ) -> str:
        ctx = snapshot.market_context
        ind = signal.indicators_1h
        vobo_threshold = available_capital * self.vobo_min_pct

        # Determinar tipo de mercado para guiar a Claude
        volume_ratio = ind.volume.ratio
        if volume_ratio >= 2.0:
            market_type = "FUERTE (alto volumen — oportunidad clara)"
        elif volume_ratio >= 1.5:
            market_type = "ACTIVO (buen volumen — señal confirmada)"
        elif volume_ratio >= 0.8:
            market_type = "NORMAL (volumen estándar)"
        elif volume_ratio >= 0.5:
            market_type = "QUIETO (volumen bajo — operar con cautela y tamaño reducido)"
        else:
            market_type = "MUY QUIETO (volumen muy bajo — posición mínima si opera)"

        # Niveles de precio con manejo seguro de listas vacías
        nearest_support = f"${signal.levels.nearest_support:,.4f}" if signal.levels.nearest_support else "No detectado"
        nearest_resistance = f"${signal.levels.nearest_resistance:,.4f}" if signal.levels.nearest_resistance else "No detectado"

        support_dist = f"{signal.levels.supports[0].distance_pct:.1f}% abajo" if signal.levels.supports else "N/A"
        resistance_dist = f"{signal.levels.resistances[0].distance_pct:.1f}% arriba" if signal.levels.resistances else "N/A"

        # Contexto del timeframe 2h si está disponible
        confirmation_4h = ""
        if signal.indicators_4h:
            ind4h = signal.indicators_4h
            confirmation_4h = f"""
=== CONFIRMACIÓN 2H ===
Tendencia 2h:     {ind4h.trend}
RSI 2h:           {ind4h.rsi.value:.1f} ({ind4h.rsi.signal})
MACD 2h:          {ind4h.macd.signal}
Dirección 2h:     {ind4h.suggested_direction}
Alineación 1h/2h: {'✅ ALINEADOS' if ind4h.suggested_direction == signal.direction else '⚠️ CONTRADICCIÓN — considera reducir tamaño'}"""

        # Contexto diario — tendencia mayor
        daily_context = ""
        if hasattr(signal, 'indicators_1d') and signal.indicators_1d:
            ind1d = signal.indicators_1d
            daily_context = f"""
=== TENDENCIA DIARIA (1D) ===
Tendencia diaria: {ind1d.trend}
RSI diario:       {ind1d.rsi.value:.1f} ({ind1d.rsi.signal})
MACD diario:      {ind1d.macd.signal}
EMA 20/50/200:    ${ind1d.ema_20:,.2f} / ${ind1d.ema_50:,.2f} / ${ind1d.ema_200:,.2f}
Dirección diaria: {ind1d.suggested_direction}
Alineación 1h/1d: {'✅ ALINEADOS' if ind1d.suggested_direction == signal.direction else '⚠️ CONTRADICCIÓN MAYOR'}"""

        prompt = f"""=== SEÑAL DE TRADING DETECTADA ===

ACTIVO: {signal.symbol}
MODO: {signal.trading_mode.upper()}
DIRECCIÓN SUGERIDA: {signal.direction.upper()}
SCORE TÉCNICO: {signal.score:.0f}/100
TIPO DE MERCADO: {market_type}

=== INDICADORES TÉCNICOS (1h) ===
Precio actual:      ${signal.current_price:,.4f}
RSI (14):           {ind.rsi.value:.1f} ({ind.rsi.signal}) — prev: {ind.rsi.prev_value:.1f}
MACD:               {ind.macd.signal} | Histograma: {ind.macd.histogram:.4f}
Bollinger Bands:    {ind.bollinger.signal} — precio al {ind.bollinger.percent_b*100:.0f}% de las bandas
Volumen:            {ind.volume.ratio:.2f}x el promedio ({ind.volume.signal})
EMA 20/50/200:      ${ind.ema_20:,.2f} / ${ind.ema_50:,.2f} / ${ind.ema_200:,.2f}
Tendencia general:  {ind.trend}
{confirmation_4h}{daily_context}

=== NIVELES DE PRECIO ===
Soporte más cercano:      {nearest_support} ({support_dist})
Resistencia más cercana:  {nearest_resistance} ({resistance_dist})
Stop-loss sugerido LONG:  ${signal.levels.dynamic_stop_loss_long:,.4f} (riesgo: {signal.levels.risk_pct_long:.1f}%)
Stop-loss sugerido SHORT: ${signal.levels.dynamic_stop_loss_short:,.4f} (riesgo: {signal.levels.risk_pct_short:.1f}%)
Take-profit sugerido:     ${signal.suggested_tp:,.4f}

=== CONTEXTO MACRO ===
BTC Dominance:    {ctx.btc_dominance:.1f}%
Market Cap total: ${ctx.total_market_cap_usd/1e12:.2f}T USD
Volumen 24h:      ${ctx.total_volume_24h_usd/1e9:.1f}B USD
Fear & Greed:     {ctx.fear_greed_index}/100 — {ctx.fear_greed_label}
Sentimiento:      {ctx.market_sentiment}

=== ALERTAS DE BALLENAS (últimas 4h) ===
{self._format_whale_alerts(snapshot)}

=== CAPITAL Y GESTIÓN DE RIESGO ===
Capital operable:    ${available_capital:.2f} USD
Umbral VoBo:         ${vobo_threshold:.2f} USD
Activos en HOLD:     {', '.join(self.hold_symbols) if self.hold_symbols else 'ninguno'}

Guía de tamaño según tipo de mercado:
- Mercado muy quieto (vol < 0.5x): usar ${ available_capital * 0.15:.2f} USD (15%)
- Mercado quieto (vol 0.5-0.8x):   usar ${ available_capital * 0.20:.2f} USD (20%)
- Mercado normal (vol 0.8-1.5x):   usar ${ available_capital * 0.30:.2f} USD (30%)
- Mercado activo (vol > 1.5x):     usar ${ available_capital * 0.40:.2f} USD (40%)

=== TU DECISIÓN COMO TRADER PROFESIONAL ===
Recuerda: no esperes el trade perfecto. Un trader profesional opera en todos los mercados
adaptando el tamaño. Si las señales son razonables aunque no perfectas, opera con tamaño reducido.

Analiza:
1. ¿El setup técnico justifica una entrada ahora?
2. ¿Qué tamaño es apropiado dado el tipo de mercado?
3. ¿Dónde poner el stop-loss basado en niveles reales?
4. ¿Qué apalancamiento corresponde al score y volumen actuales?

Responde solo con el JSON."""

        return prompt

    def _format_whale_alerts(self, snapshot: CollectedSnapshot) -> str:
        if not snapshot.whale_alerts:
            return "Sin alertas de ballenas en las últimas 4 horas"

        lines = []
        for alert in snapshot.whale_alerts[:3]:
            signal_type = ""
            if alert.is_bearish_signal:
                signal_type = "⚠️ BAJISTA — posible venta próxima"
            elif alert.is_bullish_signal:
                signal_type = "✅ ALCISTA — posible acumulación"

            lines.append(
                f"  {alert.symbol}: ${alert.amount_usd/1e6:.1f}M USD "
                f"— {alert.transaction_type} {signal_type}"
            )

        return "\n".join(lines)
