"""
test_brain.py — Script de prueba del cerebro IA
Ejecuta el stack completo: colector → balance → analizador → Claude

Uso:
    python3.11 test_brain.py
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
from analyzer.analyzer import TradingSignal
from analyzer.scorer import SignalScorer
from brain import ClaudeBrain
from executor import TradingExecutor


async def main():
    print("\n" + "="*60)
    print("  TRADING AGENT — Test Completo (Balance + Claude)")
    print("="*60 + "\n")

    collector = DataCollector()
    analyzer = TechnicalAnalyzer()

    try:
        brain = ClaudeBrain()
    except EnvironmentError as e:
        print(f"Error Claude API: {e}")
        return

    try:
        # Paso 1: Conectar y recopilar
        print("Conectando con Binance...")
        await collector.initialize()

        # Paso 2: Verificar saldo REAL de Binance
        print("Verificando saldo real en Binance...")
        executor = TradingExecutor(
            exchange=collector.binance.exchange,
            testnet=collector.binance.testnet
        )

        balance = await executor.check_balance()

        if balance is None:
            print("\nSin saldo suficiente o error de conexion.")
            print("Se envio notificacion por WhatsApp si Twilio esta configurado.")
            return

        print(f"\nSaldo disponible: {balance.summary}\n")

        # Paso 3: Recopilar datos de mercado
        print("Recopilando datos del mercado...")
        snapshot = await collector.collect()

        if not snapshot or snapshot.has_critical_gaps:
            print("Error: datos de mercado insuficientes")
            return

        print(f"Datos OK: {snapshot.summary()}\n")

        # Paso 4: Analizar
        print("Analizando indicadores tecnicos...")
        result = analyzer.analyze(snapshot)

        if not result.has_signals:
            print(f"Sin senales validas (score minimo: 65/100)")
            print("\nSimulando senal de BTC para probar Claude con saldo real...")

            test_symbol = "BTCUSDT"
            if test_symbol in snapshot.candles:
                candles_1h = snapshot.candles[test_symbol].get("1h", [])
                candles_4h = snapshot.candles[test_symbol].get("4h", [])

                ind_1h = analyzer.indicator_calc.calculate(test_symbol, "1h", candles_1h)
                ind_4h = analyzer.indicator_calc.calculate(test_symbol, "4h", candles_4h) if candles_4h else None
                levels = analyzer.level_detector.detect(test_symbol, candles_1h)

                if ind_1h:
                    scorer = SignalScorer()
                    direction = "long" if ind_1h.rsi.value < 50 else "short"
                    score = scorer.calculate(ind_1h, levels)

                    test_signal = TradingSignal(
                        symbol=test_symbol,
                        trading_mode="futures",
                        direction=direction,
                        score=max(score.total, 65.0),
                        current_price=ind_1h.current_price,
                        suggested_sl=score.suggested_sl or levels.dynamic_stop_loss_long,
                        suggested_tp=score.suggested_tp or ind_1h.current_price * 1.03,
                        risk_pct=levels.risk_pct_long,
                        leverage="1x",
                        reasoning=f"Senal de prueba — {score.reasoning}",
                        indicators_1h=ind_1h,
                        indicators_4h=ind_4h,
                        levels=levels,
                    )
                    signals_to_test = [test_signal]
                else:
                    print("No hay datos suficientes para simular")
                    return
        else:
            signals_to_test = result.signals
            print(f"{len(signals_to_test)} senal(es) detectada(s)\n")

        # Paso 5: Claude decide usando el saldo REAL
        print("="*60)
        print("CONSULTANDO A CLAUDE (con saldo real)...")
        print("="*60)

        for signal in signals_to_test[:1]:
            print(f"\nSenal: {signal.symbol} {signal.direction.upper()} | Score: {signal.score:.0f}/100")
            print(f"Capital disponible real: ${balance.operable:.2f} USD")
            print("Enviando a Claude...\n")

            decision = brain.decide(signal, snapshot, balance.operable)

            if not decision:
                print("Claude no pudo tomar una decision")
                continue

            print(f"{'─'*50}")
            print(f"DECISION DE CLAUDE:")
            print(f"{'─'*50}")
            print(f"  Opera:    {'SI' if decision.should_trade else 'NO'}")

            if decision.should_trade:
                print(f"  Activo:   {decision.symbol}")
                print(f"  Monto:    ${decision.amount_usd:.2f} USD")
                print(f"  SL:       ${decision.stop_loss:,.4f}")
                print(f"  TP:       ${decision.take_profit:,.4f}")
                print(f"  VoBo:     {'REQUERIDO' if decision.requires_vobo else 'No — opera solo'}")
                print(f"  Razon:    {decision.reasoning[:200]}")

                print(f"\n  Mensaje WhatsApp:")
                print(f"  {'─'*40}")
                if decision.requires_vobo:
                    print(decision.whatsapp_vobo_message)
                else:
                    print(decision.whatsapp_entry_message)
            else:
                print(f"  Razon: {decision.reason_not_trade}")

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
