"""
levels.py — Detección de soportes y resistencias
Responsabilidad: identificar los niveles de precio más importantes
donde el mercado ha rebotado históricamente.

Un soporte es un precio donde el mercado ha rebotado hacia arriba.
Una resistencia es un precio donde el mercado ha rebotado hacia abajo.

El stop-loss dinámico se coloca justo debajo del soporte más cercano.
"""

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from collector.models import CandleData

logger = logging.getLogger(__name__)


@dataclass
class PriceLevel:
    """Un nivel de soporte o resistencia."""
    price: float           # Precio del nivel
    level_type: str        # "support" | "resistance"
    strength: int          # Cuántas veces el precio rebotó aquí (más = más fuerte)
    distance_pct: float    # Distancia porcentual al precio actual


@dataclass
class SupportResistanceResult:
    """
    Resultado del análisis de soportes y resistencias.
    Incluye los niveles más relevantes y el stop-loss dinámico sugerido.
    """
    current_price: float
    supports: list[PriceLevel]      # Soportes ordenados por cercanía
    resistances: list[PriceLevel]   # Resistencias ordenadas por cercanía
    nearest_support: float          # Soporte más cercano por debajo
    nearest_resistance: float       # Resistencia más cercana por encima
    dynamic_stop_loss_long: float   # SL para posición LONG (debajo del soporte)
    dynamic_stop_loss_short: float  # SL para posición SHORT (encima de la resistencia)
    risk_pct_long: float            # % de riesgo si entras LONG ahora
    risk_pct_short: float           # % de riesgo si entras SHORT ahora


class SupportResistanceDetector:
    """
    Detecta soportes y resistencias usando dos métodos combinados:

    1. Fractales — máximos y mínimos locales donde el precio rebotó
    2. Zonas de alto volumen — precios donde se negociaron grandes volúmenes

    Uso:
        detector = SupportResistanceDetector()
        levels = detector.detect("BTCUSDT", candles_1h)
    """

    def candles_to_dataframe(self, candles: list[CandleData]) -> pd.DataFrame:
        """Convierte velas a DataFrame."""
        data = {
            "timestamp": [c.timestamp for c in candles],
            "open":   [c.open for c in candles],
            "high":   [c.high for c in candles],
            "low":    [c.low for c in candles],
            "close":  [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
        df = pd.DataFrame(data)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df

    def find_fractal_levels(
        self,
        df: pd.DataFrame,
        window: int = 5
    ) -> tuple[list[float], list[float]]:
        """
        Detecta máximos y mínimos locales (fractales).

        Un mínimo local (soporte) es una vela cuyo mínimo es menor
        que los N mínimos anteriores y posteriores.

        Un máximo local (resistencia) es una vela cuyo máximo es mayor
        que los N máximos anteriores y posteriores.
        """
        supports = []
        resistances = []
        half = window // 2

        for i in range(half, len(df) - half):
            # Ventana alrededor del punto actual
            window_lows = df["low"].iloc[i - half: i + half + 1]
            window_highs = df["high"].iloc[i - half: i + half + 1]

            current_low = df["low"].iloc[i]
            current_high = df["high"].iloc[i]

            # Mínimo local — posible soporte
            if current_low == window_lows.min():
                supports.append(float(current_low))

            # Máximo local — posible resistencia
            if current_high == window_highs.max():
                resistances.append(float(current_high))

        return supports, resistances

    def cluster_levels(
        self,
        levels: list[float],
        tolerance_pct: float = 0.005
    ) -> list[PriceLevel]:
        """
        Agrupa niveles cercanos en zonas.

        Si dos soportes están a menos del 0.5% de distancia entre sí,
        se consideran el mismo nivel — más fuerte porque el precio
        rebotó ahí varias veces.
        """
        if not levels:
            return []

        sorted_levels = sorted(levels)
        clusters = []
        current_cluster = [sorted_levels[0]]

        for level in sorted_levels[1:]:
            # Si está dentro de la tolerancia del último en el cluster, agregar
            if (level - current_cluster[-1]) / current_cluster[-1] <= tolerance_pct:
                current_cluster.append(level)
            else:
                clusters.append(current_cluster)
                current_cluster = [level]

        clusters.append(current_cluster)

        # Cada cluster se convierte en un nivel con su precio promedio y fortaleza
        result = []
        for cluster in clusters:
            result.append({
                "price": np.mean(cluster),
                "strength": len(cluster)  # Cuántas veces rebotó aquí
            })

        return result

    def detect(
        self,
        symbol: str,
        candles: list[CandleData],
        stop_loss_buffer_pct: float = 0.003  # 0.3% debajo del soporte
    ) -> SupportResistanceResult:
        """
        Detecta soportes y resistencias y calcula el stop-loss dinámico.

        stop_loss_buffer_pct: margen adicional debajo del soporte para el SL
                              evita ser liquidado por un spike momentáneo
        """
        df = self.candles_to_dataframe(candles)
        current_price = float(df["close"].iloc[-1])

        # Detectar fractales
        raw_supports, raw_resistances = self.find_fractal_levels(df)

        # Agrupar niveles cercanos
        support_clusters = self.cluster_levels(raw_supports)
        resistance_clusters = self.cluster_levels(raw_resistances)

        # Separar soportes (por debajo del precio) y resistencias (por encima)
        supports = []
        for s in support_clusters:
            if s["price"] < current_price:
                distance = (current_price - s["price"]) / current_price * 100
                supports.append(PriceLevel(
                    price=round(s["price"], 2),
                    level_type="support",
                    strength=s["strength"],
                    distance_pct=round(distance, 2)
                ))

        resistances = []
        for r in resistance_clusters:
            if r["price"] > current_price:
                distance = (r["price"] - current_price) / current_price * 100
                resistances.append(PriceLevel(
                    price=round(r["price"], 2),
                    level_type="resistance",
                    strength=r["strength"],
                    distance_pct=round(distance, 2)
                ))

        # Ordenar por cercanía al precio actual
        supports.sort(key=lambda x: x.distance_pct)
        resistances.sort(key=lambda x: x.distance_pct)

        # Soporte y resistencia más cercanos
        nearest_support = supports[0].price if supports else current_price * 0.97
        nearest_resistance = resistances[0].price if resistances else current_price * 1.03

        # Stop-loss dinámico
        # Para LONG: justo debajo del soporte más cercano
        dynamic_sl_long = nearest_support * (1 - stop_loss_buffer_pct)

        # Para SHORT: justo encima de la resistencia más cercana
        dynamic_sl_short = nearest_resistance * (1 + stop_loss_buffer_pct)

        # Calcular % de riesgo
        risk_pct_long = (current_price - dynamic_sl_long) / current_price * 100
        risk_pct_short = (dynamic_sl_short - current_price) / current_price * 100

        logger.debug(
            f"{symbol} — Precio: ${current_price:,.2f} | "
            f"Soporte: ${nearest_support:,.2f} ({supports[0].distance_pct:.1f}% abajo) | "
            f"Resistencia: ${nearest_resistance:,.2f} ({resistances[0].distance_pct:.1f}% arriba) | "
            f"SL Long: ${dynamic_sl_long:,.2f} ({risk_pct_long:.1f}% riesgo) | "
            f"SL Short: ${dynamic_sl_short:,.2f} ({risk_pct_short:.1f}% riesgo)"
            if supports and resistances else
            f"{symbol} — Precio: ${current_price:,.2f} | Sin niveles detectados"
        )

        return SupportResistanceResult(
            current_price=current_price,
            supports=supports[:5],       # Top 5 soportes más cercanos
            resistances=resistances[:5], # Top 5 resistencias más cercanas
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
            dynamic_stop_loss_long=round(dynamic_sl_long, 2),
            dynamic_stop_loss_short=round(dynamic_sl_short, 2),
            risk_pct_long=round(risk_pct_long, 2),
            risk_pct_short=round(risk_pct_short, 2)
        )
