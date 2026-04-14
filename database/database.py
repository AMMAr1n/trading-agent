"""
database.py — Base de datos SQLite
Responsabilidad: registrar todas las operaciones, señales y
métricas de rendimiento del agente.

Tablas:
- trades: historial completo de operaciones abiertas y cerradas
- signals: todas las señales detectadas (operadas o no)
- daily_summary: resumen diario de rendimiento
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

load_dotenv(override=False)
logger = logging.getLogger(__name__)

# Ruta de la base de datos
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_agent.db")


@dataclass
class TradeRecord:
    """Registro de una operación en la base de datos."""
    id: Optional[int]
    symbol: str
    direction: str              # "long" | "short"
    trading_mode: str           # "futures" | "spot_tier1" etc
    amount_usd: float
    entry_price: float
    stop_loss: float
    take_profit: float
    leverage: str
    score: float                # Score de confianza al momento de entrar
    reasoning: str              # Razonamiento de Claude
    status: str                 # "open" | "closed" | "cancelled"
    opened_at: datetime
    closed_at: Optional[datetime]
    exit_price: Optional[float]
    pnl_usd: Optional[float]
    pnl_pct: Optional[float]
    close_reason: Optional[str] # "take_profit" | "stop_loss" | "manual" | "timeout"
    order_id: Optional[str]     # ID de la orden en Binance (trazabilidad)
    volume_ratio: float = 0.0
    trend_1h: Optional[str] = None
    trend_1d: Optional[str] = None
    trend_1w: Optional[str] = None
    patterns: Optional[str] = None
    hour_opened: int = 0
    fear_greed: int = 50
    score_breakdown: Optional[str] = None
    balance_total: float = 0.0
    balance_reserve: float = 0.0
    balance_operable: float = 0.0
    duration_min: int = 0
    sl_tp_method: str = "algo_api"
    version: str = "v0.6.0"


@dataclass
class SignalRecord:
    """Registro de una señal detectada."""
    id: Optional[int]
    symbol: str
    direction: str
    score: float
    was_traded: bool            # Si el agente decidió operar
    reason_not_traded: Optional[str]
    detected_at: datetime
    rsi: float
    macd_signal: str
    volume_ratio: float
    trend: str


class TradingDatabase:
    """
    Gestiona la base de datos SQLite del agente.

    Uso:
        db = TradingDatabase()
        db.initialize()

        # Registrar apertura de operación
        trade_id = db.open_trade(trade_record)

        # Registrar cierre
        db.close_trade(trade_id, exit_price, pnl_usd, pnl_pct, "take_profit")

        # Obtener resumen del día
        summary = db.get_daily_summary()
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        logger.info(f"TradingDatabase inicializado: {db_path}")

    @contextmanager
    def get_connection(self):
        """Context manager para conexiones a la base de datos."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Permite acceder por nombre de columna
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def initialize(self):
        """Crea las tablas si no existen."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Tabla de operaciones
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    trading_mode TEXT NOT NULL,
                    amount_usd REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    leverage TEXT DEFAULT '1x',
                    score REAL DEFAULT 0,
                    reasoning TEXT,
                    status TEXT DEFAULT 'open',
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    exit_price REAL,
                    pnl_usd REAL,
                    pnl_pct REAL,
                    close_reason TEXT,
                    order_id TEXT,
                    volume_ratio REAL DEFAULT 0,
                    trend_1h TEXT,
                    trend_1d TEXT,
                    trend_1w TEXT,
                    patterns TEXT,
                    hour_opened INTEGER DEFAULT 0,
                    fear_greed INTEGER DEFAULT 50,
                    score_breakdown TEXT,
                    balance_total REAL DEFAULT 0,
                    balance_reserve REAL DEFAULT 0,
                    balance_operable REAL DEFAULT 0,
                    duration_min INTEGER DEFAULT 0,
                    sl_tp_method TEXT DEFAULT 'algo_api',
                    version TEXT DEFAULT 'v0.6.0'
                )
            """)

            # Tabla de versiones del agente
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL UNIQUE,
                    description TEXT,
                    implemented_at TEXT NOT NULL,
                    notes TEXT
                )
            """)

            # Insertar versión actual si no existe
            cursor.execute("""
                INSERT OR IGNORE INTO versions (version, description, implemented_at, notes)
                VALUES (
                    'v0.6.0',
                    'ta-lib patrones velas, timeframe 1W, fix SL/TP precio bajo, MIN_SCORE=45, pipeline aprendizaje BD, notificaciones mejoradas',
                    '2026-04-13',
                    'Primera versión con aprendizaje desde BD y contexto multi-timeframe completo'
                )
            """)

            # Migración: agregar columnas si no existen (para BD existentes)
            for col, col_type in [
                ("volume_ratio", "REAL DEFAULT 0"),
                ("trend_1h", "TEXT"),
                ("trend_1d", "TEXT"),
                ("trend_1w", "TEXT"),
                ("patterns", "TEXT"),
                ("hour_opened", "INTEGER DEFAULT 0"),
                ("fear_greed", "INTEGER DEFAULT 50"),
                ("score_breakdown", "TEXT"),
                ("balance_total", "REAL DEFAULT 0"),
                ("balance_reserve", "REAL DEFAULT 0"),
                ("balance_operable", "REAL DEFAULT 0"),
                ("duration_min", "INTEGER DEFAULT 0"),
                ("sl_tp_method", "TEXT DEFAULT 'algo_api'"),
                ("version", "TEXT DEFAULT 'v0.6.0'"),
            ]:
                try:
                    cursor.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
                except Exception:
                    pass  # columna ya existe

            # Tabla de señales detectadas
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    score REAL NOT NULL,
                    was_traded INTEGER DEFAULT 0,
                    reason_not_traded TEXT,
                    detected_at TEXT NOT NULL,
                    rsi REAL,
                    macd_signal TEXT,
                    volume_ratio REAL,
                    trend TEXT
                )
            """)

            # Tabla de resúmenes diarios
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL UNIQUE,
                    total_trades INTEGER DEFAULT 0,
                    winning_trades INTEGER DEFAULT 0,
                    losing_trades INTEGER DEFAULT 0,
                    total_pnl_usd REAL DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    starting_balance REAL DEFAULT 0,
                    ending_balance REAL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)

            # Índice único para order_id de Binance (trazabilidad)
            try:
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_order_id ON trades(order_id) WHERE order_id IS NOT NULL")
            except Exception:
                pass

        logger.info("Base de datos inicializada correctamente")

    # ─── OPERACIONES ──────────────────────────────────────────────────────────

    def open_trade(self, trade: TradeRecord) -> int:
        """Registra una nueva operación abierta. Retorna el ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (
                    symbol, direction, trading_mode, amount_usd,
                    entry_price, stop_loss, take_profit, leverage,
                    score, reasoning, status, opened_at, order_id,
                    volume_ratio, trend_1h, trend_1d, trend_1w,
                    patterns, hour_opened, fear_greed, score_breakdown,
                    balance_total, balance_reserve, balance_operable,
                    duration_min, sl_tp_method, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?)
            """, (
                trade.symbol, trade.direction, trade.trading_mode,
                trade.amount_usd, trade.entry_price, trade.stop_loss,
                trade.take_profit, trade.leverage, trade.score,
                trade.reasoning,
                datetime.now(timezone.utc).isoformat(),
                trade.order_id,
                getattr(trade, 'volume_ratio', 0),
                getattr(trade, 'trend_1h', None),
                getattr(trade, 'trend_1d', None),
                getattr(trade, 'trend_1w', None),
                getattr(trade, 'patterns', None),
                getattr(trade, 'hour_opened', 0),
                getattr(trade, 'fear_greed', 50),
                getattr(trade, 'score_breakdown', None),
                getattr(trade, 'balance_total', 0),
                getattr(trade, 'balance_reserve', 0),
                getattr(trade, 'balance_operable', 0),
                getattr(trade, 'duration_min', 0),
                getattr(trade, 'sl_tp_method', 'algo_api'),
                getattr(trade, 'version', 'v0.6.0'),
            ))
            trade_id = cursor.lastrowid
            logger.info(f"Operación registrada: ID {trade_id} — {trade.symbol} {trade.direction.upper()}")
            return trade_id

    def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        pnl_usd: float,
        pnl_pct: float,
        close_reason: str
    ):
        """Registra el cierre de una operación."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Calcular duración real
            cursor.execute("SELECT opened_at FROM trades WHERE id = ?", (trade_id,))
            row = cursor.fetchone()
            duration_min = 0
            if row and row["opened_at"]:
                try:
                    opened = datetime.fromisoformat(row["opened_at"])
                    now = datetime.now(timezone.utc)
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    duration_min = int((now - opened).total_seconds() / 60)
                except Exception:
                    pass
            cursor.execute("""
                UPDATE trades SET
                    status = 'closed',
                    closed_at = ?,
                    exit_price = ?,
                    pnl_usd = ?,
                    pnl_pct = ?,
                    close_reason = ?,
                    duration_min = ?
                WHERE id = ?
            """, (
                datetime.now(timezone.utc).isoformat(),
                exit_price, pnl_usd, pnl_pct, close_reason, duration_min, trade_id
            ))
            logger.info(
                f"Operación cerrada: ID {trade_id} | "
                f"P&L: {'+'if pnl_usd >= 0 else ''}${pnl_usd:.2f} ({close_reason})"
            )

    def get_open_trades(self) -> list[dict]:
        """Retorna todas las operaciones actualmente abiertas."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'open' ORDER BY opened_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_open_trades_count(self) -> int:
        """Retorna el número de operaciones abiertas."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'open'")
            return cursor.fetchone()[0]

    # ─── SEÑALES ──────────────────────────────────────────────────────────────

    def record_signal(self, signal: SignalRecord) -> int:
        """Registra una señal detectada."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO signals (
                    symbol, direction, score, was_traded,
                    reason_not_traded, detected_at,
                    rsi, macd_signal, volume_ratio, trend
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.symbol, signal.direction, signal.score,
                1 if signal.was_traded else 0,
                signal.reason_not_traded,
                datetime.now(timezone.utc).isoformat(),
                signal.rsi, signal.macd_signal,
                signal.volume_ratio, signal.trend
            ))
            return cursor.lastrowid

    # ─── RESÚMENES ────────────────────────────────────────────────────────────

    def get_daily_summary(self, date: Optional[str] = None) -> dict:
        """
        Calcula el resumen del día a partir de las operaciones cerradas.
        Si no se especifica fecha, usa el día actual.
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Operaciones cerradas hoy
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as winners,
                    SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) as losers,
                    SUM(pnl_usd) as total_pnl,
                    AVG(pnl_pct) as avg_pnl_pct
                FROM trades
                WHERE status = 'closed'
                AND DATE(closed_at) = ?
            """, (date,))

            row = cursor.fetchone()
            total = row["total"] or 0
            winners = row["winners"] or 0
            losers = row["losers"] or 0
            total_pnl = row["total_pnl"] or 0.0
            win_rate = (winners / total * 100) if total > 0 else 0.0

            return {
                "date": date,
                "total_trades": total,
                "winning_trades": winners,
                "losing_trades": losers,
                "total_pnl_usd": round(total_pnl, 2),
                "win_rate": round(win_rate, 1),
            }

    def save_daily_summary(
        self,
        date: str,
        starting_balance: float,
        ending_balance: float
    ):
        """Guarda el resumen diario en la tabla."""
        summary = self.get_daily_summary(date)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO daily_summary (
                    date, total_trades, winning_trades, losing_trades,
                    total_pnl_usd, win_rate, starting_balance,
                    ending_balance, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date,
                summary["total_trades"],
                summary["winning_trades"],
                summary["losing_trades"],
                summary["total_pnl_usd"],
                summary["win_rate"],
                starting_balance,
                ending_balance,
                datetime.now(timezone.utc).isoformat()
            ))
            logger.info(f"Resumen diario guardado: {date} | P&L: ${summary['total_pnl_usd']:.2f}")

    def register_version(self, version: str, description: str, implemented_at: str, notes: str = None):
        """Registra una nueva versión del agente."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO versions (version, description, implemented_at, notes)
                VALUES (?, ?, ?, ?)
            """, (version, description, implemented_at, notes))
            logger.info(f"Versión registrada: {version}")

    def get_learning_context(self, symbol: str, direction: str, trend_1d: str = None,
                              volume_ratio: float = None, score: float = None) -> dict:
        """
        Consulta el historial de trades para generar contexto de aprendizaje.
        Retorna estadísticas por condición para incluir en el prompt de Claude.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Total de trades cerrados
            cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed'")
            total_closed = cursor.fetchone()[0]

            if total_closed < 5:
                return {"insufficient_data": True, "total_trades": total_closed}

            # Win rate general (últimos 30 trades)
            cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins
                FROM trades WHERE status='closed'
                ORDER BY closed_at DESC LIMIT 30
            """)
            row = cursor.fetchone()
            general_total = row["total"] or 0
            general_wins  = row["wins"] or 0

            # Win rate por dirección
            cursor.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins
                FROM trades WHERE status='closed' AND direction=?
            """, (direction,))
            row = cursor.fetchone()
            dir_total = row["total"] or 0
            dir_wins  = row["wins"] or 0

            # Win rate en tendencia 1D similar
            trend_stats = None
            if trend_1d:
                cursor.execute("""
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins
                    FROM trades WHERE status='closed' AND direction=? AND trend_1d=?
                """, (direction, trend_1d))
                row = cursor.fetchone()
                if row["total"] and row["total"] >= 2:
                    trend_stats = {"total": row["total"], "wins": row["wins"] or 0}

            # Win rate por rango de volumen
            vol_stats = None
            if volume_ratio is not None:
                vol_bucket = "high" if volume_ratio >= 1.5 else "low" if volume_ratio < 0.8 else "normal"
                if vol_bucket == "high":
                    vol_cond = "volume_ratio >= 1.5"
                elif vol_bucket == "low":
                    vol_cond = "volume_ratio < 0.8"
                else:
                    vol_cond = "volume_ratio >= 0.8 AND volume_ratio < 1.5"
                cursor.execute(f"""
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins
                    FROM trades WHERE status='closed' AND direction=? AND {vol_cond}
                """, (direction,))
                row = cursor.fetchone()
                if row["total"] and row["total"] >= 2:
                    vol_stats = {"bucket": vol_bucket, "total": row["total"], "wins": row["wins"] or 0}

            # Win rate por rango de score
            score_stats = None
            if score is not None:
                score_low = int(score // 10) * 10
                score_high = score_low + 10
                cursor.execute("""
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins
                    FROM trades WHERE status='closed' AND score >= ? AND score < ?
                """, (score_low, score_high))
                row = cursor.fetchone()
                if row["total"] and row["total"] >= 2:
                    score_stats = {"range": f"{score_low}-{score_high}", "total": row["total"], "wins": row["wins"] or 0}

            # Últimos 3 trades del mismo símbolo
            cursor.execute("""
                SELECT direction, pnl_usd, close_reason, trend_1d, score
                FROM trades WHERE status='closed' AND symbol=?
                ORDER BY closed_at DESC LIMIT 3
            """, (symbol,))
            symbol_trades = [dict(r) for r in cursor.fetchall()]

            return {
                "insufficient_data": False,
                "total_trades": total_closed,
                "general": {"total": general_total, "wins": general_wins},
                "by_direction": {"total": dir_total, "wins": dir_wins},
                "by_trend_1d": trend_stats,
                "by_volume": vol_stats,
                "by_score": score_stats,
                "recent_same_symbol": symbol_trades,
            }

    def get_performance_stats(self, days: int = 30) -> dict:
        """Estadísticas de rendimiento de los últimos N días."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as winners,
                    SUM(pnl_usd) as total_pnl,
                    MAX(pnl_usd) as best_trade,
                    MIN(pnl_usd) as worst_trade,
                    AVG(pnl_usd) as avg_pnl
                FROM trades
                WHERE status = 'closed'
                AND opened_at >= datetime('now', ?)
            """, (f"-{days} days",))

            row = cursor.fetchone()
            total = row["total_trades"] or 0
            winners = row["winners"] or 0

            return {
                "period_days": days,
                "total_trades": total,
                "winning_trades": winners,
                "losing_trades": total - winners,
                "win_rate": round((winners / total * 100) if total > 0 else 0, 1),
                "total_pnl_usd": round(row["total_pnl"] or 0, 2),
                "best_trade_usd": round(row["best_trade"] or 0, 2),
                "worst_trade_usd": round(row["worst_trade"] or 0, 2),
                "avg_pnl_usd": round(row["avg_pnl"] or 0, 2),
            }
