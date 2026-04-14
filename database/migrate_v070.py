"""
migrate_v070.py — Migración de BD para v0.7.0
Agrega campos nuevos a la tabla trades para tracking de patrones,
régimen de mercado, y métricas de calidad de trade.

Ejecutar una sola vez en la VM:
    cd ~/trading-agent && python3 database/migrate_v070.py

v0.7.0
"""

import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_agent.db")

NEW_COLUMNS = [
    # Pattern tracking
    ("pattern_type", "TEXT"),           # "double_top", "ascending_triangle", etc.
    ("pattern_confidence", "REAL"),     # 0-100
    ("breakout_quality", "TEXT"),       # "strong", "moderate", "weak", "failed"
    ("breakout_score", "REAL"),         # 0-100

    # Regime
    ("regime", "TEXT"),                 # "trending", "ranging", "volatile"
    ("regime_adx", "REAL"),

    # Target tracking
    ("projected_rr", "REAL"),          # R:R al abrir (basado en geometría)
    ("actual_rr", "REAL"),             # R:R real al cerrar

    # Trade quality metrics
    ("max_favorable_excursion", "REAL"),  # MFE — mejor momento del trade (%)
    ("max_adverse_excursion", "REAL"),    # MAE — peor momento del trade (%)
    ("efficiency", "REAL"),              # % del TP que se alcanzó

    # MTF alignment
    ("mtf_alignment_score", "INTEGER"),  # -30 a +30
    ("mtf_consensus", "TEXT"),           # "bullish", "bearish", "neutral"

    # Agent stage
    ("agent_stage", "INTEGER"),          # 1-4
]


def migrate():
    """Agrega las columnas nuevas si no existen."""
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Base de datos no encontrada en {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Obtener columnas existentes
    cursor.execute("PRAGMA table_info(trades)")
    existing = {row[1] for row in cursor.fetchall()}

    added = 0
    for col_name, col_type in NEW_COLUMNS:
        if col_name not in existing:
            cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
            print(f"  ✅ Agregada: {col_name} ({col_type})")
            added += 1
        else:
            print(f"  ⏭️  Ya existe: {col_name}")

    conn.commit()
    conn.close()

    print(f"\nMigración completada: {added} columnas nuevas agregadas.")
    print(f"Total columnas en trades: {len(existing) + added}")


if __name__ == "__main__":
    print("=== Migración v0.7.0 ===")
    print(f"DB: {DB_PATH}\n")
    migrate()
