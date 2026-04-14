"""
claude_brain.py — Interfaz principal con Claude API
v0.7.0 — Recibe mtf_alignment y lo pasa al prompt builder.
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

CLAUDE_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1000


class ClaudeBrain:

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY no encontrada en el .env"
            )

        self.client = anthropic.Anthropic(api_key=api_key)
        self.prompt_builder = PromptBuilder()
        self.vobo_min_pct = float(os.getenv("VOBO_MIN_PCT", "15")) / 100

        logger.info("ClaudeBrain v0.7.0 inicializado")

    def decide(
        self,
        signal: TradingSignal,
        snapshot: CollectedSnapshot,
        available_capital: float,
        coingecko_sentiment: dict = None,
        rss_headlines: list = None,
        learning_context=None,
        mtf_alignment=None,           # NUEVO v0.7.0
    ) -> Optional[TradeDecision]:
        """
        Llama a Claude y obtiene una decisión de trading.

        v0.7.0: recibe mtf_alignment (chart patterns, breakout, regime)
        y learning_context puede ser objeto LearningContext o dict legacy.
        """
        logger.info(
            f"Consultando Claude para {signal.symbol} "
            f"(score: {signal.score:.0f}, capital: ${available_capital:.2f})"
        )

        prompt = self.prompt_builder.build(
            signal, snapshot, available_capital,
            coingecko_sentiment=coingecko_sentiment,
            rss_headlines=rss_headlines,
            learning_context=learning_context,
            mtf_alignment=mtf_alignment,        # NUEVO v0.7.0
        )

        try:
            response = self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            if not response.content:
                logger.error("Claude devolvió respuesta vacía")
                return None
            response_text = response.content[0].text.strip()
            logger.debug(f"Respuesta de Claude: {response_text[:200]}...")

            decision_data = self._parse_response(response_text)
            if not decision_data:
                return None

            return self._build_decision(decision_data, signal, available_capital)

        except anthropic.APIError as e:
            logger.error(f"Error de API de Anthropic: {e}")
            return None
        except Exception as e:
            logger.error(f"Error inesperado al consultar Claude: {e}")
            return None

    def _parse_response(self, response_text: str) -> Optional[dict]:
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

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
        should_trade = data.get("should_trade", False)

        amount_usd = float(data.get("amount_usd") or 0)
        stop_loss = float(data.get("stop_loss") or signal.suggested_sl or 0)
        take_profit = float(data.get("take_profit") or signal.suggested_tp or 0)
        confidence = float(data.get("confidence") or 0.5)

        if amount_usd > available_capital:
            logger.warning(
                f"Claude propuso ${amount_usd:.2f} pero solo hay "
                f"${available_capital:.2f} disponible — ajustando"
            )
            amount_usd = available_capital * 0.4

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
