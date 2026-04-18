# Trading Agent — CHANGELOG
**Última actualización: 18 de abril de 2026**

---

## v0.8.1 — 18 Abril 2026

**Fix crítico: cancelación de órdenes huérfanas**
- Fix: `_cancel_algo_orders` incluye `symbol` en el payload del DELETE (Binance lo requiere)
- Fix: errores de cancelación ahora se loggean con `logger.error` (antes warning silencioso)
- Fix: notificación Telegram cuando la cancelación automática falla
- Fix: `_restore_tracked_positions` quita el `except: pass` silencioso al cancelar órdenes
- Feat: `sweep_orphan_algo_orders` — barrido defensivo al inicio de cada ciclo
- Feat: barrido detecta órdenes algo de símbolos sin posición abierta y las cancela
- Fix: `sweep_orphan_algo_orders` usa `order_executor.exchange` (tiene métodos `fapiPrivate*`)
- Resultado: bloquea definitivamente órdenes huérfanas como la del Trade #8 (XRP TP $1.5731)

**Archivos modificados**
- `executor/position_monitor.py` — `_cancel_algo_orders` + nuevo `sweep_orphan_algo_orders`
- `main.py` — `_restore_tracked_positions` cancelación robusta con logs

---

## v0.8.0 — 17-18 Abril 2026

**Penalización adaptativa**
- Feat: Penalización dinámica `penalty = base(15) × alignment_factor × strength_factor`
- Feat: alignment_factor basado en alineación semanal (0.5/1.0/1.5)
- Feat: strength_factor basado en ADX diario normalizado (ADX/40, cap 1.5)
- Feat: Campos en BD: `daily_penalty_applied`, `weekly_penalty_applied`, `alignment_context`
- Resultado: de 1 señal por ciclo a 6 señales en el mismo mercado

**Dirección multi-timeframe**
- Feat: Si 1h es neutral, consulta 2h → 1d → 1w antes de descartar
- Feat: Log indica qué TF proporcionó la dirección
- Resultado: 6 pares adicionales llegan al análisis completo (antes morían silenciosamente)

**Logs diagnósticos**
- Feat: 4 puntos de descarte silencioso en analyzer.py ahora tienen logs
- Feat: indicators.py reporta cuál indicador específico falló
- Resultado: visibilidad completa de por qué cada par se descarta en cada ciclo

**Progresión automática de etapas**
- Feat: RISK_PCT dinámico por etapa (2% → 3% → 4% → 5%)
- Feat: MIN_SCORE dinámico por etapa (50 → 45 → 42 → 40)
- Feat: Leverage máximo limitado por etapa (1x → 2x → 3x → 5x)
- Feat: AGENT_STAGE se actualiza automáticamente en .env
- Feat: Etapa visible en reportes periódicos ("🎓 Etapa: Aprendiz")

**Expansión de pares**
- Feat: 15 pares monitoreados (antes 7): +AVAX, DOT, LINK, LTC, NEAR, TRUMP, AAVE, SUI
- Feat: Símbolos dinámicos en mensaje de inicio de Telegram

**Fixes críticos — position_monitor**
- Fix: Normalización de símbolos (XRP/USDT:USDT → XRPUSDT)
- Fix: fetch_open_orders por símbolo (no global) — evita rate limit
- Fix: _cancel_algo_orders usa order_executor.exchange
- Fix: SL/TP verificación confía en valores registrados de Algo API

**Fixes críticos — _restore_tracked_positions**
- Fix: Usa executor exchange para algo orders
- Fix: No cierra posiciones que sí existen en Binance
- Fix: Filtra trades de cierre por side
- Fix: No envía notificaciones fantasma durante restore

**Fixes — targets y reporte**
- Fix: Targets incoherentes de patrones macro sin breakout (max 10% distancia)
- Fix: Targets negativos y SL/TP del lado equivocado rechazados
- Fix: MTF fallback a TF bajo cuando targets del alto son inválidos
- Fix: Detección TP vs SL por distancia
- Fix: Reporte periódico consulta Binance directamente si BD vacía
- Fix: Proceso fantasma PID 1065 eliminado

---

## v0.7.0-v0.7.2 — 14-16 Abril 2026

**Chart patterns y análisis**
- Feat: 16 chart patterns + breakout validation + market regime
- Feat: Multi-timeframe top-down alignment con veto del TF mayor
- Feat: SMA50 filter, skewness-adjusted t-test
- Feat: ta-lib patrones de velas (+40 patrones con fallback numpy)

**Learning engine**
- Feat: 4 etapas de progresión (Aprendiz → Experto)
- Feat: get_learning_context, pattern statistics, bias detection

**Base de datos**
- Feat: Schema ampliado a 50 campos por trade
- Feat: Tablas pattern_detections y cycle_summary

---

## v0.6.0 — 13-14 Abril 2026
- ta-lib patrones de velas, timeframe 1W, pipeline de aprendizaje
- Cancelación automática de órdenes huérfanas SL/TP (incompleta — completada en v0.8.1)

## v0.5.0 — 10-12 Abril 2026
- SHORT habilitado, ATR position sizing, CoinGecko, RSS feeds

## v0.4.0 — 10 Abril 2026
- Deploy GCP VM, IP estática, systemd

## v0.3.0 — 10 Abril 2026
- Binance Futuros, Claude Sonnet, Telegram, SQLite

---

## ESTADO ACTUAL (18 Abril 2026 — post-cierre XRP)

| Campo | Valor |
|-------|-------|
| Versión | v0.8.1 |
| Saldo total | ~$60.19 USDT |
| Saldo operable | ~$54.17 USDT |
| Posiciones abiertas | 0 |
| Trades cerrados | 8 |
| Win rate | 62.5% (5 TP / 3 SL) |
| Net P&L acumulado | +$0.57 |
| Etapa | 1 (Aprendiz) |
| Pares monitoreados | 15 |
| Ciclo | Cada 20 minutos |

### Historial de trades
| ID | Par | Dir | P&L | Razón |
|----|-----|-----|-----|-------|
| 1 | SOL | LONG | +$0.26 | TP |
| 2 | BNB | LONG | +$0.41 | TP |
| 3 | DOGE | LONG | +$0.38 | TP |
| 4 | ADA | SHORT | -$0.13 | SL |
| 5 | XRP | LONG | -$0.24 | SL |
| 6 | XRP | LONG | +$0.23 | TP |
| 7 | XRP | LONG | +$0.22 | TP |
| 8 | XRP | LONG | -$0.56 | SL |

---

## COSTOS ACTUALES

| Componente | Costo mensual |
|-----------|---------------|
| Google Cloud VM (e2-micro) | ~$6 USD |
| Anthropic Claude Sonnet 4.6 | ~$7-15 USD |
| **Total** | **~$13-21 USD/mes** |
