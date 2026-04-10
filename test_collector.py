"""
test_collector.py — Script de prueba del colector
Ejecuta un ciclo completo de recoleccion y muestra los resultados.

Uso:
    python test_collector.py

Esto te permite verificar que:
  1. Las credenciales de Binance y CoinMarketCap son correctas
  2. La conexion con ambos servicios funciona
  3. Los datos se recopilan y estructuran correctamente
  4. El snapshot se construye sin errores criticos
"""

import asyncio
import logging
import sys
import os

# Configurar logging para ver los mensajes en consola
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

# Agregar el directorio raiz al path para importar los modulos
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector import DataCollector


async def main():
    print("\n" + "="*60)
    print("  TRADING AGENT — Test del Colector")
    print("="*60 + "\n")

    collector = DataCollector()

    try:
        # ── Paso 1: Conectar ──────────────────────────────────────────────────
        print("Conectando con Binance y CoinMarketCap...")
        await collector.initialize()
        print("✓ Conexiones establecidas\n")

        # ── Paso 2: Ejecutar un ciclo de recoleccion ──────────────────────────
        print("Ejecutando ciclo de recoleccion...")
        snapshot = await collector.collect()

        if snapshot is None:
            print("✗ Error critico — no se pudo construir el snapshot")
            return

        # ── Paso 3: Mostrar resultados ────────────────────────────────────────
        print("\n" + "-"*60)
        print("RESULTADOS DEL SNAPSHOT")
        print("-"*60)

        print(f"\nTimestamp: {snapshot.snapshot_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        print(f"\nTICKERS RECOPILADOS ({len(snapshot.tickers)}):")
        for symbol, ticker in snapshot.tickers.items():
            change_sign = "+" if ticker.change_24h_pct >= 0 else ""
            print(
                f"  {symbol:<12} "
                f"${ticker.price:>12,.4f}  "
                f"{change_sign}{ticker.change_24h_pct:.2f}%  "
                f"Vol: ${ticker.volume_24h / 1e6:.1f}M"
            )

        print(f"\nVELAS RECOPILADAS:")
        for symbol in snapshot.candles:
            timeframes_ok = list(snapshot.candles[symbol].keys())
            counts = [len(snapshot.candles[symbol][tf]) for tf in timeframes_ok]
            print(f"  {symbol:<12} Timeframes: {timeframes_ok} — Velas: {counts}")

        print(f"\nCONTEXTO MACRO:")
        ctx = snapshot.market_context
        print(f"  BTC Dominance:    {ctx.btc_dominance:.1f}%")
        print(f"  Market Cap total: ${ctx.total_market_cap_usd / 1e12:.2f}T USD")
        print(f"  Volumen 24h:      ${ctx.total_volume_24h_usd / 1e9:.1f}B USD")
        print(f"  Fear & Greed:     {ctx.fear_greed_index} — {ctx.fear_greed_label}")
        print(f"  Sentimiento:      {ctx.market_sentiment}")

        print(f"\nERRORES EN ESTE CICLO: {len(snapshot.collection_errors)}")
        for err in snapshot.collection_errors:
            print(f"  - {err}")

        print(f"\nGAPS CRITICOS: {'SI — el agente NO operaria' if snapshot.has_critical_gaps else 'NO — datos suficientes para operar'}")
        print(f"ACTIVOS DISPONIBLES: {snapshot.available_symbols}")

        print("\n" + "="*60)
        print("  Test completado exitosamente")
        print("="*60 + "\n")

    except EnvironmentError as e:
        print(f"\n✗ Error de configuracion: {e}")
        print("\nRevisa tu archivo .env y asegurate de que tenga todas las credenciales.")

    except Exception as e:
        print(f"\n✗ Error inesperado: {e}")
        logging.exception("Detalle del error:")

    finally:
        await collector.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
