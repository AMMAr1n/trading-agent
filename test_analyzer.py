"""
test_analyzer.py — Script de prueba del analizador técnico
Ejecuta el colector y luego el analizador para ver señales reales.

Uso:
    python3.11 test_analyzer.py
"""

import asyncio
import logging
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector import DataCollector
from analyzer import TechnicalAnalyzer


async def main():
    print("\n" + "="*60)
    print("  TRADING AGENT — Test del Analizador Técnico")
    print("="*60 + "\n")

    collector = DataCollector()
    analyzer = TechnicalAnalyzer()

    try:
        # Paso 1: Recopilar datos
        print("Conectando y recopilando datos del mercado...")
        await collector.initialize()
        snapshot = await collector.collect()

        if snapshot is None or snapshot.has_critical_gaps:
            print("Error: datos insuficientes para analizar")
            return

        print(f"Datos recopilados: {snapshot.summary()}\n")

        # Paso 2: Analizar
        print("Analizando indicadores técnicos...")
        print("-" * 60)

        result = analyzer.analyze(snapshot)

        # Paso 3: Mostrar resultados
        print(f"\nACTIVOS ANALIZADOS: {result.analyzed_symbols}")
        print(f"SEÑALES DETECTADAS: {len(result.signals)}")

        if result.has_signals:
            print("\n" + "="*60)
            print("SEÑALES VÁLIDAS (score >= 65):")
            print("="*60)

            for i, signal in enumerate(result.signals, 1):
                print(f"\n{'─'*50}")
                print(f"#{i} {signal.symbol} — {signal.direction.upper()}")
                print(f"{'─'*50}")
                print(f"  Score:         {signal.score:.0f}/100")
                print(f"  Modo:          {signal.trading_mode}")
                print(f"  Precio actual: ${signal.current_price:,.4f}")
                print(f"  Stop-loss:     ${signal.suggested_sl:,.4f}")
                print(f"  Take-profit:   ${signal.suggested_tp:,.4f}")
                print(f"  Riesgo:        {signal.risk_pct:.1f}%")
                print(f"  Apalancamiento:{signal.leverage}")
                print(f"\n  Análisis:")

                # Mostrar indicadores del timeframe 1h
                ind = signal.indicators_1h
                print(f"    RSI 1h:      {ind.rsi.value:.1f} ({ind.rsi.signal})")
                print(f"    MACD 1h:     {ind.macd.signal}")
                print(f"    Bollinger:   {ind.bollinger.signal} ({ind.bollinger.percent_b*100:.0f}% de las bandas)")
                print(f"    Volumen:     {ind.volume.ratio:.1f}x el promedio ({ind.volume.signal})")
                print(f"    Tendencia:   {ind.trend}")

                if signal.indicators_4h:
                    print(f"    RSI 4h:      {signal.indicators_4h.rsi.value:.1f} ({signal.indicators_4h.rsi.signal})")

                print(f"\n  Niveles clave:")
                print(f"    Soporte:     ${signal.levels.nearest_support:,.4f}")
                print(f"    Resistencia: ${signal.levels.nearest_resistance:,.4f}")

        else:
            print("\nNingún activo superó el score mínimo de 65/100 en este ciclo.")
            print("El agente esperaría al siguiente ciclo (5 minutos).")

            # Mostrar los mejores scores aunque no sean suficientes
            print("\nMejores scores del ciclo (referencia):")
            for symbol in snapshot.available_symbols[:5]:
                if symbol in [s for s in snapshot.candles]:
                    candles = snapshot.candles.get(symbol, {}).get("1h", [])
                    if candles:
                        ind = analyzer.indicator_calc.calculate(symbol, "1h", candles)
                        if ind:
                            print(f"  {symbol:<12} RSI: {ind.rsi.value:.0f} | "
                                  f"MACD: {ind.macd.signal:<15} | "
                                  f"Vol: {ind.volume.ratio:.1f}x | "
                                  f"Dir: {ind.suggested_direction}")

        print("\n" + "="*60)
        print("  Test completado exitosamente")
        print("="*60 + "\n")

    except Exception as e:
        print(f"\nError: {e}")
        logging.exception("Detalle:")

    finally:
        await collector.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
