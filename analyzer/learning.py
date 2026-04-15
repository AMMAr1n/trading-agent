"""
learning.py — Motor de aprendizaje del agente
v0.7.2 — Añade skewness-adjusted t-test para priorizar patrones
con significancia estadística real vs patrones que ganaron por suerte.

Fórmula del t-test ajustado (Johnson, 1978):
  t_sa = sqrt(m) * (s + (1/3)*gamma*s^2 + (1/6m)*gamma)
  donde s = r_bar / sigma_r (media estandarizada)
        gamma = skewness de los retornos
        m = número de trades

Si p-value < 0.05 → "estadísticamente validado"
Si p-value >= 0.05 → "observado, no validado"

Esto se usa para priorizar, no para descartar.
"""

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from scipy import stats as scipy_stats

logger = logging.getLogger(__name__)

STAGES = {
    1: {"name": "Aprendiz",     "min_trades": 0,   "max_leverage": "1x",  "min_score": 50,  "min_rr": 2.0},
    2: {"name": "Practicante",  "min_trades": 20,  "max_leverage": "2x",  "min_score": 45,  "min_rr": 1.8},
    3: {"name": "Competente",   "min_trades": 50,  "max_leverage": "3x",  "min_score": 42,  "min_rr": 1.5},
    4: {"name": "Experto",      "min_trades": 100, "max_leverage": "5x",  "min_score": 40,  "min_rr": 1.3},
}

ADVANCE_CRITERIA = {
    2: {"min_wr": 50, "min_pf": 1.2, "min_trades": 20},
    3: {"min_wr": 55, "min_pf": 1.5, "min_trades": 50},
    4: {"min_wr": 55, "min_pf": 1.8, "min_trades": 100},
}

RETREAT_CRITERIA = {
    "max_drawdown_pct": 15,
    "min_wr_last_20": 40,
}


@dataclass
class TradeQuality:
    trade_id: int
    quality_score: float
    projected_rr: float
    actual_rr: float
    efficiency: float
    mae_pct: float
    mfe_pct: float
    pattern_correct: bool


@dataclass
class PatternStats:
    pattern_type: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_rr: float
    avg_quality: float
    profit_factor: float
    best_regime: str
    worst_regime: str
    # v0.7.2: significancia estadística
    is_statistically_validated: bool = False
    t_stat: float = 0.0
    p_value: float = 1.0
    priority_label: str = "observado"  # "validado" | "observado" | "insuficiente"


@dataclass
class AgentBias:
    bias_type: str
    severity: str
    description: str
    recommendation: str


@dataclass
class LearningContext:
    stage: int
    stage_name: str
    stage_config: dict
    total_trades_closed: int
    win_rate: float
    profit_factor: float
    avg_rr: float
    total_pnl: float
    pattern_stats: list[PatternStats]
    best_patterns: list[str]
    worst_patterns: list[str]
    patterns_to_avoid: list[str]
    biases: list[AgentBias]
    adaptive_rules: list[str]
    next_stage_progress: str
    can_advance: bool
    should_retreat: bool

    def prompt_section(self) -> str:
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

        cfg = self.stage_config
        lines.append(f"Parámetros de tu etapa:")
        lines.append(
            f"  Leverage máximo: {cfg['max_leverage']} | "
            f"MIN_SCORE: {cfg['min_score']} | "
            f"R:R mínimo: {cfg['min_rr']}:1"
        )
        lines.append("")

        # Patrones validados estadísticamente (PRIORIDAD MÁXIMA)
        validated = [ps for ps in self.pattern_stats
                     if ps.is_statistically_validated and ps.total_trades >= 3]
        if validated:
            lines.append("Patrones VALIDADOS estadísticamente (priorizar):")
            for ps in validated:
                lines.append(
                    f"  ⭐ {ps.pattern_type.replace('_', ' ').title()}: "
                    f"{ps.win_rate:.0f}% WR ({ps.wins}/{ps.total_trades}) | "
                    f"PF: {ps.profit_factor:.1f}x | "
                    f"p-value: {ps.p_value:.3f} | "
                    f"Mejor en: {ps.best_regime}"
                )
            lines.append("")

        # Patrones observados (funcionan pero sin validación estadística)
        if self.best_patterns:
            observed_good = [ps for ps in self.pattern_stats
                            if ps.pattern_type in self.best_patterns
                            and not ps.is_statistically_validated
                            and ps.total_trades >= 3]
            if observed_good:
                lines.append("Patrones que funcionan (observados, sin validación estadística):")
                for ps in observed_good:
                    lines.append(
                        f"  ✅ {ps.pattern_type.replace('_', ' ').title()}: "
                        f"{ps.win_rate:.0f}% WR ({ps.wins}/{ps.total_trades}) | "
                        f"PF: {ps.profit_factor:.1f}x"
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

        if self.biases:
            lines.append("Sesgos detectados:")
            for bias in self.biases:
                lines.append(f"  ⚠️ {bias.description}")
                lines.append(f"     → {bias.recommendation}")
            lines.append("")

        if self.adaptive_rules:
            lines.append("Reglas adaptativas (generadas de tu historial):")
            for rule in self.adaptive_rules:
                lines.append(f"  📌 {rule}")

        return "\n".join(lines)


class LearningEngine:

    def __init__(self, db):
        self.db = db
        self.current_stage = int(os.getenv("AGENT_STAGE", "1"))

    def _get_closed_trades(self, limit=200):
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

    def _skewness_adjusted_ttest(self, returns: list) -> tuple:
        """
        Calcula el t-test ajustado por asimetría (Johnson, 1978).
        Usado por la tesis de Charles University para evaluar patrones en crypto.

        Returns: (t_statistic, p_value)
        """
        m = len(returns)
        if m < 5:
            return 0.0, 1.0

        r = np.array(returns, dtype=float)
        r_bar = np.mean(r)
        sigma_r = np.std(r, ddof=1)

        if sigma_r == 0:
            return 0.0, 1.0

        s = r_bar / sigma_r  # media estandarizada
        gamma = np.mean((r - r_bar) ** 3) / (sigma_r ** 3)  # skewness

        # t_sa = sqrt(m) * (s + (1/3)*gamma*s^2 + (1/(6*m))*gamma)
        t_sa = math.sqrt(m) * (s + (1/3) * gamma * s**2 + (1/(6*m)) * gamma)

        # p-value bilateral usando distribución t con m-1 grados de libertad
        p_value = 2 * (1 - scipy_stats.t.cdf(abs(t_sa), df=m-1))

        return float(t_sa), float(p_value)

    def _calculate_pattern_stats(self, trades):
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

            # v0.7.2: Skewness-adjusted t-test para significancia
            returns = [t.get("pnl_pct", 0) or 0 for t in group]
            t_stat, p_value = self._skewness_adjusted_ttest(returns)
            is_validated = p_value < 0.05 and len(group) >= 5 and wins / len(group) > 0.5

            if len(group) < 5:
                priority_label = "insuficiente"
            elif is_validated:
                priority_label = "validado"
            else:
                priority_label = "observado"

            stats.append(PatternStats(
                pattern_type=pt,
                total_trades=len(group),
                wins=wins,
                losses=losses,
                win_rate=round(wins / len(group) * 100, 1) if group else 0,
                avg_rr=round(avg_rr, 2),
                avg_quality=0,
                profit_factor=round(pf, 2),
                best_regime=best_regime,
                worst_regime=worst_regime,
                is_statistically_validated=is_validated,
                t_stat=round(t_stat, 3),
                p_value=round(p_value, 4),
                priority_label=priority_label,
            ))

        stats.sort(key=lambda s: (s.is_statistically_validated, s.win_rate), reverse=True)
        return stats

    def _detect_biases(self, trades):
        biases = []
        if not trades:
            return biases

        longs = sum(1 for t in trades if t.get("direction") == "long")
        shorts = len(trades) - longs
        if longs > 0 and shorts > 0:
            long_pct = longs / len(trades) * 100
            if long_pct > 75:
                biases.append(AgentBias(
                    bias_type="long_bias", severity="medium",
                    description=f"Sesgo LONG: {long_pct:.0f}% de trades son LONG",
                    recommendation="Buscar activamente oportunidades SHORT"
                ))
            elif long_pct < 25:
                biases.append(AgentBias(
                    bias_type="short_bias", severity="medium",
                    description=f"Sesgo SHORT: {100-long_pct:.0f}% de trades son SHORT",
                    recommendation="Considerar más setups LONG en tendencias alcistas"
                ))

        hour_stats = {}
        for t in trades:
            hour = t.get("hour_opened")
            if hour is not None:
                if hour not in hour_stats:
                    hour_stats[hour] = {"wins": 0, "total": 0}
                hour_stats[hour]["total"] += 1
                if (t.get("pnl_usd") or 0) > 0:
                    hour_stats[hour]["wins"] += 1

        bad_hours = [h for h, s in hour_stats.items()
                     if s["total"] >= 3 and s["wins"] / s["total"] < 0.3]
        if bad_hours:
            biases.append(AgentBias(
                bias_type="bad_hours", severity="low",
                description=f"Horas con bajo rendimiento (UTC): {bad_hours}",
                recommendation=f"Evitar operar entre {min(bad_hours)}-{max(bad_hours)} UTC"
            ))

        day_counts = {}
        for t in trades:
            day = str(t.get("opened_at", ""))[:10]
            if day:
                if day not in day_counts:
                    day_counts[day] = {"total": 0, "wins": 0}
                day_counts[day]["total"] += 1
                if (t.get("pnl_usd") or 0) > 0:
                    day_counts[day]["wins"] += 1

        overtrading_days = [d for d, s in day_counts.items()
                           if s["total"] >= 5 and s["wins"] / s["total"] < 0.4]
        if overtrading_days:
            biases.append(AgentBias(
                bias_type="overtrading", severity="high",
                description=f"Overtrading detectado en {len(overtrading_days)} día(s) con WR < 40%",
                recommendation="Limitar a máximo 4 trades por día. Calidad sobre cantidad."
            ))

        return biases

    def _generate_adaptive_rules(self, trades, pattern_stats, biases):
        rules = []

        # Reglas por patrón con priorización estadística
        for ps in pattern_stats:
            if ps.is_statistically_validated and ps.win_rate >= 60:
                rules.append(
                    f"PRIORIZAR {ps.pattern_type.replace('_', ' ')} — "
                    f"validado estadísticamente: {ps.win_rate:.0f}% WR, "
                    f"p-value {ps.p_value:.3f}, {ps.total_trades} trades"
                )
            elif ps.total_trades >= 5 and ps.win_rate >= 70 and not ps.is_statistically_validated:
                rules.append(
                    f"Considerar {ps.pattern_type.replace('_', ' ')} — "
                    f"{ps.win_rate:.0f}% WR pero aún no validado estadísticamente "
                    f"(p-value: {ps.p_value:.3f}). Operar con tamaño reducido."
                )
            elif ps.total_trades >= 5 and ps.win_rate < 35:
                rules.append(
                    f"EVITAR {ps.pattern_type.replace('_', ' ')} — "
                    f"solo {ps.win_rate:.0f}% WR. No entrar aunque el score sea alto."
                )

        if trades:
            recent_20 = trades[:20]
            recent_wr = sum(1 for t in recent_20 if (t.get("pnl_usd") or 0) > 0) / len(recent_20) * 100
            if recent_wr < 40:
                rules.append(
                    f"PRECAUCIÓN: Win rate reciente {recent_wr:.0f}% (últimos {len(recent_20)} trades). "
                    f"Ser más selectivo."
                )
            elif recent_wr > 65:
                rules.append(
                    f"Buen momento: Win rate reciente {recent_wr:.0f}%. "
                    f"Mantener la disciplina."
                )

        return rules[:6]

    def _evaluate_stage(self, trades):
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
            can_advance = wr >= criteria["min_wr"] and pf >= criteria["min_pf"] and total >= criteria["min_trades"]
            stage_name = STAGES[next_stage]["name"]
            progress = (
                f"{total}/{criteria['min_trades']} trades, "
                f"WR {wr:.0f}%/{criteria['min_wr']}%, "
                f"PF {pf:.1f}x/{criteria['min_pf']}x "
                f"→ {'✅ listo para ' + stage_name if can_advance else '⏳ en progreso'}"
            )
        else:
            progress = f"Etapa máxima alcanzada con {total} trades"

        if total >= 20:
            recent_20 = trades[:20]
            recent_wr = sum(1 for t in recent_20 if (t.get("pnl_usd") or 0) > 0) / 20 * 100
            if recent_wr < RETREAT_CRITERIA["min_wr_last_20"]:
                should_retreat = True
                progress += f" ⚠️ WR últimos 20: {recent_wr:.0f}% — RETROCESO recomendado"

        return can_advance, should_retreat, progress

    def get_context(self):
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

        validated_count = sum(1 for ps in pattern_stats if ps.is_statistically_validated)
        logger.info(
            f"Learning context: Stage {self.current_stage} ({stage_config['name']}) | "
            f"{total} trades | WR: {wr:.0f}% | PF: {pf:.1f}x | "
            f"Validated patterns: {validated_count} | "
            f"Advance: {can_advance} | Retreat: {should_retreat}"
        )

        return context
