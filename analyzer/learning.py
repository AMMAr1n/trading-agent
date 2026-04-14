"""
learning.py — Motor de aprendizaje del agente
Reemplaza get_learning_context() con un sistema analítico completo.

Funciones principales:
1. Trade quality scoring (R:R real, MAE/MFE, eficiencia)
2. Pattern performance tracking (win rate por patrón + régimen)
3. Bias detection (overtrading, sesgo long, horas malas)
4. Progressive stage management (aprendiz → experto)
5. Adaptive rule generation para el prompt de Claude

v0.7.0
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Etapas de evolución
STAGES = {
    1: {"name": "Aprendiz",     "min_trades": 0,   "max_leverage": "1x",  "min_score": 50,  "min_rr": 2.0},
    2: {"name": "Practicante",  "min_trades": 20,  "max_leverage": "2x",  "min_score": 45,  "min_rr": 1.8},
    3: {"name": "Competente",   "min_trades": 50,  "max_leverage": "3x",  "min_score": 42,  "min_rr": 1.5},
    4: {"name": "Experto",      "min_trades": 100, "max_leverage": "5x",  "min_score": 40,  "min_rr": 1.3},
}

# Criterios de avance
ADVANCE_CRITERIA = {
    2: {"min_wr": 50, "min_pf": 1.2, "min_trades": 20},
    3: {"min_wr": 55, "min_pf": 1.5, "min_trades": 50},
    4: {"min_wr": 55, "min_pf": 1.8, "min_trades": 100},
}

# Criterios de retroceso
RETREAT_CRITERIA = {
    "max_drawdown_pct": 15,
    "min_wr_last_20": 40,
}


@dataclass
class TradeQuality:
    """Calidad de un trade individual."""
    trade_id: int
    quality_score: float          # 0-100
    projected_rr: float           # R:R al abrir
    actual_rr: float              # R:R real al cerrar
    efficiency: float             # % del TP alcanzado (0-1+)
    mae_pct: float                # Max Adverse Excursion (% peor momento)
    mfe_pct: float                # Max Favorable Excursion (% mejor momento)
    pattern_correct: bool         # ¿El patrón acertó la dirección?


@dataclass
class PatternStats:
    """Estadísticas de rendimiento de un tipo de patrón."""
    pattern_type: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_rr: float
    avg_quality: float
    profit_factor: float          # ganancias / pérdidas
    best_regime: str              # Régimen donde mejor funciona
    worst_regime: str             # Régimen donde peor funciona


@dataclass
class AgentBias:
    """Sesgo detectado en el comportamiento del agente."""
    bias_type: str                # "long_bias", "overtrading", "bad_hours", etc.
    severity: str                 # "low" | "medium" | "high"
    description: str
    recommendation: str


@dataclass
class LearningContext:
    """
    Contexto de aprendizaje completo para el prompt de Claude.
    Reemplaza la sección simple de historial.
    """
    stage: int
    stage_name: str
    stage_config: dict

    # Stats globales
    total_trades_closed: int
    win_rate: float
    profit_factor: float
    avg_rr: float
    total_pnl: float

    # Análisis por patrón
    pattern_stats: list[PatternStats]
    best_patterns: list[str]         # Top 3 patrones por win rate
    worst_patterns: list[str]        # Bottom 3 patrones
    patterns_to_avoid: list[str]     # WR < 40%

    # Sesgos
    biases: list[AgentBias]

    # Reglas adaptativas
    adaptive_rules: list[str]

    # Estado de evolución
    next_stage_progress: str         # "15/20 trades para Practicante"
    can_advance: bool
    should_retreat: bool

    def prompt_section(self) -> str:
        """Genera la sección completa de aprendizaje para el prompt."""
        lines = []

        lines.append(f"=== TU EXPERIENCIA COMO TRADER (Etapa: {self.stage_name}) ===")
        lines.append(
            f"Trades cerrados: {self.total_trades_closed} | "
            f"Win rate: {self.win_rate:.0f}% | "
            f"Profit factor: {self.profit_factor:.1f}x | "
            f"P&L total: ${self.total_pnl:.2f}"
        )
        lines.append(f"Progreso: {self.next_stage_progress}")
        lines.append("")

        # Parámetros de la etapa actual
        cfg = self.stage_config
        lines.append(f"Parámetros de tu etapa:")
        lines.append(
            f"  Leverage máximo: {cfg['max_leverage']} | "
            f"MIN_SCORE: {cfg['min_score']} | "
            f"R:R mínimo: {cfg['min_rr']}:1"
        )
        lines.append("")

        # Patrones que funcionan
        if self.best_patterns:
            lines.append("Patrones que te funcionan:")
            for ps in self.pattern_stats:
                if ps.pattern_type in self.best_patterns and ps.total_trades >= 3:
                    lines.append(
                        f"  ✅ {ps.pattern_type.replace('_', ' ').title()}: "
                        f"{ps.win_rate:.0f}% WR ({ps.wins}/{ps.total_trades}) | "
                        f"PF: {ps.profit_factor:.1f}x | "
                        f"Mejor en: {ps.best_regime}"
                    )
            lines.append("")

        # Patrones a evitar
        if self.patterns_to_avoid:
            lines.append("Patrones a EVITAR:")
            for ps in self.pattern_stats:
                if ps.pattern_type in self.patterns_to_avoid and ps.total_trades >= 3:
                    lines.append(
                        f"  ❌ {ps.pattern_type.replace('_', ' ').title()}: "
                        f"{ps.win_rate:.0f}% WR ({ps.wins}/{ps.total_trades}) — no entrar"
                    )
            lines.append("")

        # Sesgos
        if self.biases:
            lines.append("Sesgos detectados:")
            for bias in self.biases:
                lines.append(f"  ⚠️ {bias.description}")
                lines.append(f"     → {bias.recommendation}")
            lines.append("")

        # Reglas adaptativas
        if self.adaptive_rules:
            lines.append("Reglas adaptativas (generadas de tu historial):")
            for rule in self.adaptive_rules:
                lines.append(f"  📌 {rule}")

        return "\n".join(lines)


class LearningEngine:
    """
    Motor de aprendizaje del agente.

    Consulta la BD de trades, calcula estadísticas,
    detecta sesgos, y genera contexto para el prompt.

    Usage:
        engine = LearningEngine(db)
        context = engine.get_context()
    """

    def __init__(self, db):
        """
        Args:
            db: Instancia de TradingDatabase con métodos:
                - get_closed_trades(limit) → list[dict]
                - get_performance_stats(days) → dict
        """
        self.db = db
        self.current_stage = int(os.getenv("AGENT_STAGE", "1"))

    def _get_closed_trades(self, limit: int = 200) -> list[dict]:
        """Obtiene trades cerrados de la BD."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM trades
                    WHERE status = 'closed' AND pnl_usd IS NOT NULL
                    ORDER BY closed_at DESC
                    LIMIT ?
                """, (limit,))
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error obteniendo trades cerrados: {e}")
            return []

    def _calculate_pattern_stats(self, trades: list[dict]) -> list[PatternStats]:
        """Calcula estadísticas por tipo de patrón."""
        pattern_groups = {}
        for t in trades:
            pt = t.get("pattern_type", "unknown") or "unknown"
            if pt not in pattern_groups:
                pattern_groups[pt] = []
            pattern_groups[pt].append(t)

        stats = []
        for pt, group in pattern_groups.items():
            wins = sum(1 for t in group if (t.get("pnl_usd") or 0) > 0)
            losses = len(group) - wins
            total_gain = sum(t.get("pnl_usd", 0) for t in group if (t.get("pnl_usd") or 0) > 0)
            total_loss = abs(sum(t.get("pnl_usd", 0) for t in group if (t.get("pnl_usd") or 0) < 0))
            pf = total_gain / total_loss if total_loss > 0 else (2.0 if total_gain > 0 else 0)

            # Mejor/peor régimen
            regime_wr = {}
            for t in group:
                regime = t.get("regime", "unknown") or "unknown"
                if regime not in regime_wr:
                    regime_wr[regime] = {"wins": 0, "total": 0}
                regime_wr[regime]["total"] += 1
                if (t.get("pnl_usd") or 0) > 0:
                    regime_wr[regime]["wins"] += 1

            best_regime = max(regime_wr.items(),
                            key=lambda x: x[1]["wins"]/max(x[1]["total"],1),
                            default=("unknown", {}))[0]
            worst_regime = min(regime_wr.items(),
                             key=lambda x: x[1]["wins"]/max(x[1]["total"],1),
                             default=("unknown", {}))[0]

            avg_rr_list = [t.get("actual_rr", 0) for t in group if t.get("actual_rr")]
            avg_rr = sum(avg_rr_list) / len(avg_rr_list) if avg_rr_list else 0

            stats.append(PatternStats(
                pattern_type=pt,
                total_trades=len(group),
                wins=wins,
                losses=losses,
                win_rate=round(wins / len(group) * 100, 1) if group else 0,
                avg_rr=round(avg_rr, 2),
                avg_quality=0,  # Se calcula después
                profit_factor=round(pf, 2),
                best_regime=best_regime,
                worst_regime=worst_regime,
            ))

        stats.sort(key=lambda s: s.win_rate, reverse=True)
        return stats

    def _detect_biases(self, trades: list[dict]) -> list[AgentBias]:
        """Detecta sesgos sistemáticos en el comportamiento del agente."""
        biases = []
        if not trades:
            return biases

        # Sesgo de dirección
        longs = sum(1 for t in trades if t.get("direction") == "long")
        shorts = len(trades) - longs
        if longs > 0 and shorts > 0:
            long_pct = longs / len(trades) * 100
            if long_pct > 75:
                biases.append(AgentBias(
                    bias_type="long_bias",
                    severity="medium",
                    description=f"Sesgo LONG: {long_pct:.0f}% de trades son LONG",
                    recommendation="Buscar activamente oportunidades SHORT cuando los indicadores lo sugieran"
                ))
            elif long_pct < 25:
                biases.append(AgentBias(
                    bias_type="short_bias",
                    severity="medium",
                    description=f"Sesgo SHORT: {100-long_pct:.0f}% de trades son SHORT",
                    recommendation="Considerar más setups LONG en tendencias alcistas"
                ))

        # Horas malas
        hour_stats = {}
        for t in trades:
            hour = t.get("hour_opened")
            if hour is not None:
                if hour not in hour_stats:
                    hour_stats[hour] = {"wins": 0, "total": 0}
                hour_stats[hour]["total"] += 1
                if (t.get("pnl_usd") or 0) > 0:
                    hour_stats[hour]["wins"] += 1

        bad_hours = [
            h for h, s in hour_stats.items()
            if s["total"] >= 3 and s["wins"] / s["total"] < 0.3
        ]
        if bad_hours:
            biases.append(AgentBias(
                bias_type="bad_hours",
                severity="low",
                description=f"Horas con bajo rendimiento (UTC): {bad_hours}",
                recommendation=f"Evitar operar entre {min(bad_hours)}-{max(bad_hours)} UTC"
            ))

        # Overtrading (más de 5 trades en un día con WR bajo)
        day_counts = {}
        for t in trades:
            day = str(t.get("opened_at", ""))[:10]
            if day:
                if day not in day_counts:
                    day_counts[day] = {"total": 0, "wins": 0}
                day_counts[day]["total"] += 1
                if (t.get("pnl_usd") or 0) > 0:
                    day_counts[day]["wins"] += 1

        overtrading_days = [
            d for d, s in day_counts.items()
            if s["total"] >= 5 and s["wins"] / s["total"] < 0.4
        ]
        if overtrading_days:
            biases.append(AgentBias(
                bias_type="overtrading",
                severity="high",
                description=f"Overtrading detectado en {len(overtrading_days)} día(s) con WR < 40%",
                recommendation="Limitar a máximo 4 trades por día. Calidad sobre cantidad."
            ))

        return biases

    def _generate_adaptive_rules(self, trades: list[dict], pattern_stats: list[PatternStats],
                                  biases: list[AgentBias]) -> list[str]:
        """Genera reglas adaptativas basadas en el historial."""
        rules = []

        # Reglas por patrón
        for ps in pattern_stats:
            if ps.total_trades >= 5 and ps.win_rate >= 70:
                rules.append(
                    f"Priorizar {ps.pattern_type.replace('_', ' ')} — "
                    f"históricamente {ps.win_rate:.0f}% WR con {ps.total_trades} trades"
                )
            elif ps.total_trades >= 5 and ps.win_rate < 35:
                rules.append(
                    f"EVITAR {ps.pattern_type.replace('_', ' ')} — "
                    f"solo {ps.win_rate:.0f}% WR. No entrar aunque el score sea alto."
                )

        # Reglas por win rate general
        if trades:
            recent_20 = trades[:20]
            recent_wr = sum(1 for t in recent_20 if (t.get("pnl_usd") or 0) > 0) / len(recent_20) * 100
            if recent_wr < 40:
                rules.append(
                    f"PRECAUCIÓN: Win rate reciente {recent_wr:.0f}% (últimos {len(recent_20)} trades). "
                    f"Ser más selectivo, subir MIN_SCORE."
                )
            elif recent_wr > 65:
                rules.append(
                    f"Buen momento: Win rate reciente {recent_wr:.0f}%. "
                    f"Mantener la disciplina, no aumentar riesgo."
                )

        return rules[:6]  # Máximo 6 reglas

    def _evaluate_stage(self, trades: list[dict]) -> tuple:
        """Evalúa si el agente debe avanzar o retroceder de etapa."""
        can_advance = False
        should_retreat = False
        progress = ""

        total = len(trades)
        wins = sum(1 for t in trades if (t.get("pnl_usd") or 0) > 0)
        wr = (wins / total * 100) if total > 0 else 0
        total_gain = sum(t.get("pnl_usd", 0) for t in trades if (t.get("pnl_usd") or 0) > 0)
        total_loss = abs(sum(t.get("pnl_usd", 0) for t in trades if (t.get("pnl_usd") or 0) < 0))
        pf = total_gain / total_loss if total_loss > 0 else 2.0

        next_stage = self.current_stage + 1
        if next_stage in ADVANCE_CRITERIA:
            criteria = ADVANCE_CRITERIA[next_stage]
            meets_wr = wr >= criteria["min_wr"]
            meets_pf = pf >= criteria["min_pf"]
            meets_trades = total >= criteria["min_trades"]
            can_advance = meets_wr and meets_pf and meets_trades

            stage_name = STAGES[next_stage]["name"]
            progress = (
                f"{total}/{criteria['min_trades']} trades, "
                f"WR {wr:.0f}%/{criteria['min_wr']}%, "
                f"PF {pf:.1f}x/{criteria['min_pf']}x "
                f"→ {'✅ listo para ' + stage_name if can_advance else '⏳ en progreso'}"
            )
        else:
            progress = f"Etapa máxima alcanzada con {total} trades"

        # Retroceso
        if total >= 20:
            recent_20 = trades[:20]
            recent_wr = sum(1 for t in recent_20 if (t.get("pnl_usd") or 0) > 0) / 20 * 100
            if recent_wr < RETREAT_CRITERIA["min_wr_last_20"]:
                should_retreat = True
                progress += f" ⚠️ WR últimos 20: {recent_wr:.0f}% — RETROCESO recomendado"

        return can_advance, should_retreat, progress

    def get_context(self) -> LearningContext:
        """
        Genera el contexto de aprendizaje completo.
        Este es el método principal — reemplaza get_learning_context().
        """
        trades = self._get_closed_trades(200)
        total = len(trades)
        wins = sum(1 for t in trades if (t.get("pnl_usd") or 0) > 0)
        wr = (wins / total * 100) if total > 0 else 0
        total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
        total_gain = sum(t.get("pnl_usd", 0) for t in trades if (t.get("pnl_usd") or 0) > 0)
        total_loss = abs(sum(t.get("pnl_usd", 0) for t in trades if (t.get("pnl_usd") or 0) < 0))
        pf = round(total_gain / total_loss, 2) if total_loss > 0 else 2.0

        avg_rr_list = [t.get("actual_rr", 0) for t in trades if t.get("actual_rr")]
        avg_rr = round(sum(avg_rr_list) / len(avg_rr_list), 2) if avg_rr_list else 0

        pattern_stats = self._calculate_pattern_stats(trades)
        biases = self._detect_biases(trades)
        adaptive_rules = self._generate_adaptive_rules(trades, pattern_stats, biases)
        can_advance, should_retreat, progress = self._evaluate_stage(trades)

        # Best/worst patterns
        good = [ps.pattern_type for ps in pattern_stats if ps.win_rate >= 60 and ps.total_trades >= 3]
        bad = [ps.pattern_type for ps in pattern_stats if ps.win_rate < 40 and ps.total_trades >= 3]

        stage_config = STAGES.get(self.current_stage, STAGES[1])

        context = LearningContext(
            stage=self.current_stage,
            stage_name=stage_config["name"],
            stage_config=stage_config,
            total_trades_closed=total,
            win_rate=round(wr, 1),
            profit_factor=pf,
            avg_rr=avg_rr,
            total_pnl=round(total_pnl, 2),
            pattern_stats=pattern_stats,
            best_patterns=good[:3],
            worst_patterns=bad[-3:] if bad else [],
            patterns_to_avoid=bad,
            biases=biases,
            adaptive_rules=adaptive_rules,
            next_stage_progress=progress,
            can_advance=can_advance,
            should_retreat=should_retreat,
        )

        logger.info(
            f"Learning context: Stage {self.current_stage} ({stage_config['name']}) | "
            f"{total} trades | WR: {wr:.0f}% | PF: {pf:.1f}x | "
            f"Advance: {can_advance} | Retreat: {should_retreat}"
        )

        return context
