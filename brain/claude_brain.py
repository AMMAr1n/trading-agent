"""
claude_brain.py — Interfaz principal con Claude API
Responsabilidad: enviar el prompt a Claude, parsear la respuesta
y producir una TradeDecision lista para el executor.
"""

import json
import logging
import os
from typing import Optional

import anthropic
from dotenv import load_dotenv

from collector.models import CollectedSnapshot
from analyzer.analyzer import TradingSignal
from .prompt_builder import PromptBuilder, SYSTEM_PROMPT
from .decision import TradeDecision

load_dotenv(override=False)
logger = logging.getLogger(__name__)

# Modelo de Claude a usar
CLAUDE_MODEL = "claude-sonnet-4-6"

# Máximo de tokens en la respuesta
MAX_TOKENS = 1000


class ClaudeBrain:
    """
    Cerebro del agente — interfaz con Claude API.

    Recibe señales del analizador técnico y produce
    decisiones de trading concretas.

    Uso:
        brain = ClaudeBrain()
        decision = await brain.decide(signal, snapshot, available_capital)
    """

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY no encontrada en el .env"
            )

        self.client = anthropic.Anthropic(api_key=api_key)
        self.prompt_builder = PromptBuilder()

        self.vobo_min_pct = float(os.getenv("VOBO_MIN_PCT", "15")) / 100
        # Apalancamiento decidido por Claude según reglas del prompt (1x/2x/3x)

        logger.info("ClaudeBrain inicializado")

    def decide(
        self,
        signal: TradingSignal,
        snapshot: CollectedSnapshot,
        available_capital: float,
        coingecko_sentiment: dict = None,
        rss_headlines: list = None
    ) -> Optional[TradeDecision]:
        """
        Llama a Claude y obtiene una decisión de trading.

        signal:            Señal del analizador técnico
        snapshot:          Snapshot completo con contexto macro
        available_capital: Capital disponible en USDT

        Retorna None si Claude no puede tomar una decisión.
        """
        logger.info(
            f"Consultando Claude para {signal.symbol} "
            f"(score: {signal.score:.0f}, capital: ${available_capital:.2f})"
        )

        # Construir el prompt
        prompt = self.prompt_builder.build(
            signal, snapshot, available_capital,
            coingecko_sentiment=coingecko_sentiment,
            rss_headlines=rss_headlines or []
        )

        try:
            # Llamar a Claude API con web search habilitado
            # El web search requiere un loop: Claude puede hacer búsquedas antes de responder
            messages = [{"role": "user", "content": prompt}]
            tools = [{"type": "web_search_20250305", "name": "web_search"}]

            response_text = None
            max_iterations = 5  # máximo de búsquedas web permitidas

            for iteration in range(max_iterations):
                response = self.client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=messages
                )

                if not response.content:
                    logger.error("Claude devolvió respuesta vacía")
                    return None

                # Si Claude terminó → extraer texto
                if response.stop_reason == "end_turn":
                    for block in response.content:
                        if hasattr(block, "text") and block.text.strip():
                            response_text = block.text.strip()
                            break
                    break

                # Si Claude usó web search → continuar el loop
                if response.stop_reason == "tool_use":
                    # Agregar respuesta de Claude al historial
                    messages.append({
                        "role": "assistant",
                        "content": response.content
                    })
                    # Agregar resultados de tool use (web search ya los incluye)
                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            logger.info(f"Claude buscó en web: {block.input.get('query', '')}")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": "Search completed"
                            })
                    if tool_results:
                        messages.append({
                            "role": "user",
                            "content": tool_results
                        })
                    continue

                # Otro stop_reason → intentar extraer texto
                for block in response.content:
                    if hasattr(block, "text") and block.text.strip():
                        response_text = block.text.strip()
                        break
                break

            if not response_text:
                logger.error("Claude no devolvió texto en la respuesta")
                return None

            logger.debug(f"Respuesta de Claude: {response_text[:200]}...")

            # Parsear el JSON
            decision_data = self._parse_response(response_text)
            if not decision_data:
                return None

            # Construir la TradeDecision
            return self._build_decision(decision_data, signal, available_capital)

        except anthropic.APIError as e:
            logger.error(f"Error de API de Anthropic: {e}")
            return None
        except Exception as e:
            logger.error(f"Error inesperado al consultar Claude: {e}")
            return None

    def _parse_response(self, response_text: str) -> Optional[dict]:
        """
        Parsea la respuesta JSON de Claude.
        Claude a veces incluye texto antes o después del JSON — lo limpiamos.
        """
        try:
            # Intentar parsear directamente
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # Buscar el JSON dentro del texto
        try:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start != -1 and end > start:
                json_str = response_text[start:end]
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        logger.error(f"No se pudo parsear la respuesta de Claude: {response_text[:200]}")
        return None

    def _build_decision(
        self,
        data: dict,
        signal: TradingSignal,
        available_capital: float
    ) -> TradeDecision:
        """
        Construye una TradeDecision validada desde el JSON de Claude.
        """
        should_trade = data.get("should_trade", False)

        # Usar conversiones seguras — Claude puede devolver None en campos numericos
        amount_usd = float(data.get("amount_usd") or 0)
        stop_loss = float(data.get("stop_loss") or signal.suggested_sl or 0)
        take_profit = float(data.get("take_profit") or signal.suggested_tp or 0)
        confidence = float(data.get("confidence") or 0.5)

        # Validar que el monto no supere el capital disponible
        if amount_usd > available_capital:
            logger.warning(
                f"Claude propuso ${amount_usd:.2f} pero solo hay "
                f"${available_capital:.2f} disponible — ajustando"
            )
            amount_usd = available_capital * 0.4

        # Determinar si requiere VoBo
        vobo_threshold = available_capital * self.vobo_min_pct
        requires_vobo = amount_usd > vobo_threshold

        decision = TradeDecision(
            should_trade=should_trade,
            reason_not_trade=data.get("reason_not_trade"),
            symbol=data.get("symbol", signal.symbol),
            direction=data.get("direction", signal.direction),
            amount_usd=round(amount_usd, 2),
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=data.get("leverage", "1x"),
            trading_mode=data.get("trading_mode", signal.trading_mode),
            reasoning=data.get("reasoning", "Sin razonamiento disponible"),
            confidence=confidence,
            requires_vobo=requires_vobo,
            is_autonomous=not requires_vobo,
        )

        if should_trade:
            logger.info(
                f"Claude decide OPERAR: {decision.symbol} {decision.direction.upper()} "
                f"${decision.amount_usd:.2f} | VoBo: {requires_vobo} | "
                f"Confianza: {decision.confidence:.0%}"
            )
        else:
            logger.info(
                f"Claude decide NO OPERAR: {data.get('reason_not_trade', 'sin razón')}"
            )

        return decision
