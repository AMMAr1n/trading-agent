# Trading Agent — Contexto del Proyecto
**Última actualización: 18 Abril 2026 (post-cierre XRP Trade #8)**

---

## INFRAESTRUCTURA
- **VM GCP**: `instance-20260410-045221` | IP: `34.78.81.17` | Usuario: `amonroymarin`
- **Repositorio**: `github.com/AMMAr1n/trading-agent` | **Rama**: `main`
- **Servicio**: `trading-agent.service` (systemd)
- **DB**: `~/trading-agent/database/trading_agent.db` (SQLite)
- **Binance**: Futuros USD-M, Cross Margin, HMAC API, producción
- **Saldo actual**: ~$60.19 USDT en Futuros (operable: $54.17)

### Comandos útiles
```bash
# Ver logs en tiempo real
sudo journalctl -u trading-agent -f

# Deploy completo
cd ~/trading-agent && git pull && sudo systemctl restart trading-agent

# Consultar BD
sqlite3 ~/trading-agent/database/trading_agent.db "SELECT id, symbol, direction, status, entry_price, pnl_usd, close_reason FROM trades;"

# Ver diagnóstico de un ciclo completo
sudo journalctl -u trading-agent --since "1 min ago" --no-pager | grep -i "neutral\|usando dirección\|Score\|Penalty\|señal\|insuficiente\|VETO"

# Verificar barrido de huérfanas (v0.8.1)
sudo journalctl -u trading-agent --since "5 min ago" | grep -i "sweep\|barrido\|huérfan\|algo order"

# Matar proceso fantasma
ps aux | grep python | grep -v grep
```

---

## VERSIÓN ACTUAL: v0.8.1 (18 Abril 2026)

### POSICIONES ABIERTAS: 0

Última posición cerrada: **XRPUSDT LONG (Trade #8)** — cerrada en SL a $1.4254 el 18 abril 12:01 UTC. La orden huérfana de TP ($1.5731) se canceló manualmente; el fix de v0.8.1 previene este escenario a futuro.

### HISTORIAL DE TRADES (8 cerrados)
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

**Stats:** WR 62.5% (5/8) | Net +$0.57 | Etapa 1 (Aprendiz)

---

## FLUJO DE DECISIÓN DEL AGENTE

```
┌─ COLLECTOR ─────────────────────────────────────────┐
│  Binance API → 15 pares × 7 TFs (1h,2h,4h,1d,1w)  │
│  CoinMarketCap → Fear & Greed, BTC Dominance       │
│  CoinGecko → Sentiment por par                      │
│  RSS → Noticias (CoinTelegraph, Decrypt)            │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─ ANALYZER ──────────────────────────────────────────┐
│                                                      │
│  1. Indicadores 1h (RSI, MACD, BB, EMA, Vol, ATR)   │
│           ▼                                          │
│  2. Dirección multi-TF (v0.8.0)                      │
│     1h → si neutral → 2h → 1d → 1w                  │
│     Si todos neutral → DESCARTA                      │
│           ▼                                          │
│  3. MTF Alignment + Chart Patterns (16 patrones)     │
│     Breakout validation, Market regime               │
│     VETO si TF mayor contradice con alta confianza   │
│           ▼                                          │
│  4. Scorer (EMA+Vol+MACD+RSI+BB+Pattern+Breakout)    │
│     MIN_SCORE dinámico por etapa (50→45→42→40)       │
│           ▼                                          │
│  5. Penalización adaptativa (v0.8.0)                 │
│     penalty = 15 × alignment × strength(ADX)         │
│     Bonus +5 daily alineado, +8 weekly alineado      │
│           ▼                                          │
│  6. Confirmación 2h (contradicción → descarta)       │
│                                                      │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─ BRAIN (Claude Sonnet 4.6) ─────────────────────────┐
│  Recibe: score, indicadores, patrones, régimen,      │
│          noticias, sentiment, learning context,       │
│          historial de trades, etapa del agente        │
│  Decide: OPERAR (symbol, direction, amount, SL, TP)  │
│          o NO OPERAR (razón detallada)               │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─ EXECUTOR ──────────────────────────────────────────┐
│  OrderExecutor → Market order + Algo API (SL/TP)    │
│  BalanceChecker → Capital dinámico, RISK_PCT/etapa  │
│  PositionMonitor → Detecta cierres, cancela huérf.  │
│                    + barrido defensivo cada ciclo    │
│  Notifier → Telegram (apertura, cierre, reportes)    │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─ DATABASE ──────────────────────────────────────────┐
│  trades (50 campos) → historial completo             │
│  pattern_detections → cada patrón detectado          │
│  cycle_summary → resumen de cada ciclo               │
│  versions → trazabilidad de mejoras                  │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─ LEARNING ENGINE ───────────────────────────────────┐
│  Analiza: WR por patrón, por condición, por hora    │
│  Detecta sesgos (LONG bias, horario, etc.)           │
│  Genera reglas adaptativas para Claude               │
│  Avanza/retrocede etapas automáticamente             │
│  Campos penalty → auto-ajuste futuro (Fase 3)       │
└─────────────────────────────────────────────────────┘
```

### 15 Pares monitoreados
```
BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, DOGEUSDT, XRPUSDT, ADAUSDT,
AVAXUSDT, DOTUSDT, LINKUSDT, LTCUSDT, NEARUSDT, TRUMPUSDT, AAVEUSDT, SUIUSDT
```

### Variables .env
```
AGENT_STAGE=1, RISK_PCT=2.0, MIN_SCORE=45, MAX_OPEN_TRADES=10,
RESERVE_PCT=10, ATR_MULTIPLIER=1.5, LOOP_INTERVAL_MIN=20,
BINANCE_TESTNET=false, DAILY_REPORT_TIMEZONE=America/Mexico_City
```

---

## CANCELACIÓN DE ÓRDENES HUÉRFANAS (v0.8.1) ✅ VALIDADO

### Triple protección
El sistema cancela órdenes algo huérfanas en tres momentos:

1. **En caliente** — `position_monitor.run()` detecta cierre → `_cancel_algo_orders(symbol)` usa `OrderExecutor.cancel_algo_order()` (httpx directo)
2. **Al restaurar** — `_restore_tracked_positions()` al reiniciar, si un trade de BD no está en Binance → cancela sus órdenes algo
3. **Barrido defensivo** — `sweep_orphan_algo_orders()` al inicio de cada ciclo del monitor: detecta órdenes algo de símbolos sin posición abierta y las cancela

### Detalles técnicos críticos
- **ccxt 4.3.89 NO expone `fapiPrivate*AlgoOrder`** — solo existen `sapi*` para TWAP/VP
- Toda la lógica HTTP está en `order_executor.py` con httpx directo + firma HMAC
- Endpoints confirmados de Binance Futuros Algo API:
  - `GET /fapi/v1/openAlgoOrders` — listar abiertas
  - `POST /fapi/v1/algoOrder` — colocar (SL/TP)
  - `DELETE /fapi/v1/algoOrder` — cancelar
- El path de listar NO es espejo del de cancelar (asimetría de Binance)
- Errores se notifican por Telegram (`huerfana {symbol}: ...`)
- El barrido se ejecuta incluso con `_tracked` vacío (después de un reinicio)

### Validación post-deploy (18-abr-2026 15:23 UTC)
- `list_open_algo_orders()` → 0 huérfanas, sin errores
- Servicio arranca limpio sin trazas de `'binance' object has no attribute...`
- Snapshot completo con 15/15 activos procesados

---

## SISTEMA DE PROGRESIÓN AUTOMÁTICA

| Etapa | Trades | Leverage | RISK_PCT | MIN_SCORE | Min R:R |
|-------|--------|----------|----------|-----------|---------|
| 1. Aprendiz | 0-20 | 1x | 2% | 50 | 2.0 |
| 2. Practicante | 20-50 | 2x | 3% | 45 | 1.8 |
| 3. Competente | 50-100 | 3x | 4% | 42 | 1.5 |
| 4. Experto | 100+ | 5x | 5% | 40 | 1.3 |

Avance/retroceso automático: learning engine actualiza AGENT_STAGE en .env.

---

## PENALIZACIÓN ADAPTATIVA (v0.8.0)

`penalty = 15 × alignment_factor × strength_factor`

- alignment_factor: 0.5 (weekly aligned), 1.0 (neutral), 1.5 (weekly contra)
- strength_factor: ADX/40 (cap 1.5), fallback 0.8/0.5 sin ADX
- Campos en BD: daily_penalty_applied, weekly_penalty_applied, alignment_context

---

## DIRECCIÓN MULTI-TIMEFRAME (v0.8.0)

Antes: si 1h era neutral, el par se descartaba silenciosamente.
Ahora: si 1h es neutral, consulta 2h → 1d → 1w antes de descartar.

```
1h tiene dirección? → Sí → usar dirección de 1h
                      → No → 2h tiene dirección? → Sí → usar 2h
                                                   → No → 1d tiene? → Sí → usar 1d
                                                                      → No → 1w tiene? → Sí → usar 1w
                                                                                         → No → DESCARTAR
```

El score sigue calculándose con indicadores de 1h. Cuando el 1h está dormido (4 AM), los scores serán bajos aunque la dirección venga de un TF mayor. Cuando el 1h está activo (horario de trading), la dirección multi-TF amplía las oportunidades.

---

## BUGS RESUELTOS
### En v0.8.1 ✅
- [x] ccxt 4.3.89 NO expone `fapiPrivate*AlgoOrder` — solución con httpx directo
- [x] `_cancel_algo_orders` fallaba silenciosamente desde v0.6.0 (nunca funcionó realmente)
- [x] Endpoint correcto de listar: `/fapi/v1/openAlgoOrders` (no `/fapi/v1/algoOrder/openOrders`)
- [x] `_restore_tracked_positions` tenía `except: pass` que ocultaba fallos
- [x] No había protección contra órdenes huérfanas legacy de cierres previos
- [x] Barrido defensivo `sweep_orphan_algo_orders` añadido al ciclo del monitor

### En v0.8.0
- [x] position_monitor: símbolo no normalizado (XRP/USDT:USDT vs XRPUSDT)
- [x] position_monitor: fetch_open_orders sin símbolo → rate limit
- [x] _restore_tracked_positions: cerraba posiciones que sí existían en Binance
- [x] Targets incoherentes de patrones macro sin breakout
- [x] Notificaciones "cerrada_por_binance" en vez de TP/SL
- [x] Reporte periódico con 0 posiciones cuando BD desincronizada
- [x] Proceso fantasma PID enviando reportes a las 10 PM
- [x] Penalización fija -25 bloqueaba 80% de señales
- [x] 15 pares descartados silenciosamente sin log
- [x] Dirección solo de 1h mataba señales cuando mercado dormido

## BUGS PENDIENTES
- [ ] Parser de Claude falla con ```json al inicio de respuesta

## VALIDACIONES PENDIENTES
- [ ] Validar triple protección con próximo cierre real de trade (SL o TP)
- [ ] Confirmar Open Orders > Conditional queda vacío post-cierre sin intervención manual

---

## PRÓXIMOS PASOS
1. Validar v0.8.1 con próximo cierre de trade (SL o TP)
2. Monitorear dirección multi-TF con mercado activo
3. Fase 3 v0.8.0: auto-ajuste de penalties por learning engine (50+ trades)
4. Fase 4: pesos del scorer adaptativos (100+ trades)
5. Dashboard VM, FMP noticias, Web Search

---

## COSTOS
| Google Cloud VM | ~$6/mes |
| Claude Sonnet 4.6 | ~$7-15/mes |
| **Total** | **~$13-21/mes** |

---

## NOTAS IMPORTANTES
- Algo API de Binance para SL/TP — NO aparecen en fetch_open_orders
- position_monitor usa `self.order_executor.exchange` para Algo API
- DELETE de Algo API requiere `symbol` + `algoId` (no solo `algoId`)
- Símbolos ccxt: "XRP/USDT:USDT" → se normalizan a "XRPUSDT"
- MIN_SCORE y RISK_PCT son dinámicos por AGENT_STAGE
- Penalización diaria: `15 × alignment × strength` (no fija -25)
- Dirección: 1h → 2h → 1d → 1w (multi-TF fallback)
- Al reiniciar, verificar que no queden procesos fantasma
- A las 4 AM UTC el mercado crypto está dormido — scores bajos es normal
- El barrido defensivo corre cada ciclo incluso sin posiciones abiertas
