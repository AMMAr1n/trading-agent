# Trading Agent — Contexto del Proyecto
**Última actualización: 15 Abril 2026**

---

## INFRAESTRUCTURA
- **VM GCP**: `instance-20260410-045221` | IP: `34.78.81.17` | Usuario: `amonroymarin`
- **Repositorio**: `github.com/AMMAr1n/trading-agent` | **Rama**: `main`
- **Servicio**: `trading-agent.service` (systemd)
- **DB**: `~/trading-agent/database/trading_agent.db` (SQLite)
- **Binance**: Futuros USD-M, Cross Margin, HMAC API, producción
- **Saldo actual**: ~$59.74 USDT en Futuros

### Comandos útiles
```bash
# Ver logs en tiempo real
sudo journalctl -u trading-agent -f

# Ver últimos 30 logs
sudo journalctl -u trading-agent -n 30 --no-pager

# Reiniciar agente
sudo systemctl restart trading-agent

# Deploy completo (desde VS Code → GitHub → VM)
cd ~/trading-agent && git pull && sudo systemctl restart trading-agent

# Consultar BD
sqlite3 ~/trading-agent/database/trading_agent.db "SELECT id, symbol, direction, status, entry_price, stop_loss, take_profit, pnl_usd, pattern_type, regime FROM trades;"

# Ver versiones
sqlite3 ~/trading-agent/database/trading_agent.db "SELECT * FROM versions;"

# Ver columnas de trades
sqlite3 ~/trading-agent/database/trading_agent.db "PRAGMA table_info(trades);"
```

---

## VERSIÓN ACTUAL: v0.7.1 (15 Abril 2026)

### POSICIONES ABIERTAS (4)
| Par | Dir | Entrada | SL | TP | Estado |
|-----|-----|---------|----|----|--------|
| DOGEUSDT | LONG 20x | $0.09406 | $0.090 | $0.100 | Abierta |
| SOLUSDT | LONG 1x | $83.67 | $81.55 | $87.91 | Abierta |
| BNBUSDT | LONG 1x | $612.71 | $596.27 | $633.00 | Abierta |
| ADAUSDT | SHORT 1x | $0.2394 | — | — | Abierta |

---

## ARQUITECTURA

```
collector/ → analyzer/ ──────────────────────→ brain/ → executor/ → database/
Binance      Indicators    MTFAligner            Claude    OrderExec   SQLite (47 cols)
CMC          TA-Lib        PatternDetector(16)    Prompt    Monitor     Versions
CoinGecko    Scorer        BreakoutValidator      Learning  Balance
RSS          Levels        TargetCalculator
             1h/2h/4h/     RegimeDetector
             1d/1w         LearningEngine
```

### Flujo de análisis (v0.7.1 — top-down)
```
1. Collector recopila velas en 7 TFs para 7 activos
2. Analyzer por cada activo:
   a. Calcula indicadores (RSI, MACD, BB, EMAs, volumen)
   b. Detecta S/R con fractales
   c. MTFAligner (NUEVO):
      - Detecta chart patterns en cada TF (top-down: 1W→1D→4h→1h)
      - Parámetros ajustados por TF (min_bars, tolerance, swing_window)
      - Valida breakouts (volumen, body ratio, retest)
      - Calcula targets geométricos (TP/SL por patrón)
      - Detecta régimen de mercado (trending/ranging/volatile)
      - Aplica veto si TF mayor contradice TF menor
   d. Scorer calcula score con:
      - EMAs (20pts) + Volumen (15pts) + MACD (10pts) + RSI (10pts)
      - BB (5pts) + Chart Patterns (25pts) + Breakout (15pts) = 100
   e. Penalizaciones/bonus 1D y 1W
3. Brain: Claude recibe prompt con 30+ puntos de datos
4. Executor: abre posición con SL/TP via Algo API
5. Database: registra patrón, breakout, régimen, stage
```

### Variables .env relevantes
```
MIN_SCORE=45
MAX_OPEN_TRADES=10
RISK_PCT=1.0
ATR_MULTIPLIER=1.5
MIN_TRADE_AMOUNT_USD=5.5
MAX_CAPITAL_PCT=60
LOOP_INTERVAL_MIN=20
BINANCE_TESTNET=false
RESERVE_PCT=10
AGENT_STAGE=1
```

---

## MÓDULOS DEL ANALYZER (v0.7.1)

### Archivos originales
| Archivo | Función |
|---------|---------|
| `analyzer.py` | Orquestador — integra MTFAligner en analyze_symbol |
| `indicators.py` | RSI, MACD, Bollinger, volumen, EMAs, patrones ta-lib |
| `levels.py` | Soportes/resistencias con fractales |
| `scorer.py` | Score 0-100 con 7 componentes (incluye patterns + breakout) |

### Archivos v0.7.0/v0.7.1 (nuevos)
| Archivo | Función |
|---------|---------|
| `patterns.py` | Detector de 16 chart patterns multi-vela |
| `breakout.py` | Validador de breakouts (volumen, body, retest) |
| `targets.py` | Calculador de TP/SL por geometría del patrón |
| `mtf_alignment.py` | Alineación multi-TF top-down con veto |
| `regime.py` | Detector de régimen (trending/ranging/volatile) |
| `learning.py` | Motor de aprendizaje evolutivo (4 etapas) |

### 16 Chart Patterns detectados
**Reversal (8):** double_top, double_bottom, triple_top, triple_bottom, head_and_shoulders, inverse_head_and_shoulders, cup_and_handle, rising/falling_wedge (como reversal)

**Continuation (8):** ascending_triangle, descending_triangle, symmetrical_triangle, rising/falling_wedge (como continuation), rectangle, bull_flag, bear_flag, pennant

### Parámetros por timeframe
| TF | Rol | min_bars | tolerance | swing_window |
|----|-----|----------|-----------|-------------|
| 1W | Macro (veto) | 8 | 2.0% | 3 |
| 1D | Primario | 12 | 1.8% | 4 |
| 4h | Confirmación | 15 | 1.5% | 5 |
| 2h | Confirmación | 15 | 1.5% | 5 |
| 1h | Entry timing | 20 | 1.2% | 5 |

### Ponderación del Score (v0.7.1)
| Componente | Puntos | Era (v0.6.0) |
|-----------|--------|-------------|
| EMA trend | 20 | 25 |
| Volumen | 15 | 25 |
| MACD | 10 | 20 |
| RSI | 10 | 15 |
| Bollinger | 5 | 15 |
| **Chart Patterns** | **25** | 0 (nuevo) |
| **Breakout Quality** | **15** | 0 (nuevo) |
| **Total** | **100** | 100 |

---

## SCHEMA DE BD

### Tabla `trades` (47 campos — era 33 en v0.6.0)
```sql
-- Campos originales (33)
id, symbol, direction, trading_mode, amount_usd,
entry_price, stop_loss, take_profit, leverage, score,
reasoning, status, opened_at, closed_at, exit_price,
pnl_usd, pnl_pct, close_reason, order_id,
volume_ratio, trend_1h, trend_1d, trend_1w, patterns,
hour_opened, fear_greed, score_breakdown,
balance_total, balance_reserve, balance_operable,
duration_min, sl_tp_method, version

-- Campos v0.7.0 (14 nuevos)
pattern_type, pattern_confidence, breakout_quality, breakout_score,
regime, regime_adx, projected_rr, actual_rr,
max_favorable_excursion, max_adverse_excursion, efficiency,
mtf_alignment_score, mtf_consensus, agent_stage
```

### Tabla `versions`
```sql
id, version, description, implemented_at, notes
```

---

## SISTEMA DE APRENDIZAJE (v0.7.0+)

### 4 Etapas de evolución
| Etapa | Trades | Max Leverage | MIN_SCORE | Min R:R |
|-------|--------|-------------|-----------|---------|
| 1. Aprendiz | 0-20 | 1x | 50 | 2.0 |
| 2. Practicante | 20-50 | 2x | 45 | 1.8 |
| 3. Competente | 50-100 | 3x | 42 | 1.5 |
| 4. Experto | 100+ | 5x | 40 | 1.3 |

**Avance:** win rate > 55% + profit factor > 1.5 + mín N trades
**Retroceso:** drawdown > 15% o win rate < 40% en últimos 20 trades

### Learning Engine genera para el prompt:
- Stats por tipo de patrón (win rate, profit factor)
- Sesgos detectados (long bias, overtrading, horas malas)
- Reglas adaptativas del historial propio
- Progreso hacia siguiente etapa

---

## CHANGELOG RESUMIDO

### v0.7.1 — 15 Abril 2026
- Top-down MTF: 1W/1D mandan dirección, 4h confirma, 1h timing
- 16 chart patterns (+6 nuevos: bull/bear flag, pennant, cup&handle, triple top/bottom)
- Parámetros de detección ajustados por timeframe
- MTFAligner integrado en analyzer.analyze_symbol (antes del score)
- Veto del TF mayor si contradice dirección del menor

### v0.7.0 — 14-15 Abril 2026
- Chart pattern engine: 10 formaciones (double top/bottom, H&S, triangles, wedges, rectangle)
- Breakout validator: volumen, body ratio, retest
- Target calculator: TP/SL por geometría del patrón
- Market regime detector: trending/ranging/volatile
- Learning engine: 4 etapas, bias detector, pattern performance
- Score re-ponderado: patterns 25pts + breakout 15pts
- BD ampliada a 47 campos (14 nuevos para patterns/regime/learning)
- Prompt de Claude con 5 secciones nuevas

### v0.6.0 — 13-14 Abril 2026
- ta-lib patrones de velas (+40 patrones)
- Timeframe 1W, pipeline de aprendizaje, schema 33 campos
- Fixes: SL=TP, doble descuento margen, órdenes huérfanas

### v0.5.0 — 10-12 Abril 2026
- SHORT habilitado, ATR sizing, CoinGecko, RSS, 1D modificador

### v0.4.0 — 10 Abril 2026
- Deploy en Google Cloud VM

---

## BUGS PENDIENTES

### Críticos
- [ ] Notificaciones de cierre a veces no llegan (fix v0.6.0, pendiente validar)
- [ ] SL/TP huérfano al cierre (fix v0.6.0, pendiente validar)

### Menores
- [ ] Reporte periódico muestra posiciones abiertas en 0 (fix implementado, pendiente validar)
- [ ] R/R en mensaje vs razonamiento de Claude a veces inconsistente

---

## PRÓXIMOS PASOS

### Inmediato
1. Validar fixes de notificaciones y SL/TP huérfano con próximo trade cerrado
2. Monitorear que los chart patterns mejoren el win rate vs v0.6.0
3. Esperar 20+ trades cerrados para que el learning engine genere insights

### Mediano plazo
- Agregar 4 patrones Tier 2: Channel Up/Down, Broadening Wedge, Rounding Top, Three Drives
- Deploy dashboard en VM (Flask + React + HTTPS)
- P3.2 — Financial Modeling Prep (FMP) noticias por ticker
- P3.3 — Reactivar Web Search cuando el agente sea rentable

### Largo plazo
- 6 patrones armónicos (Gartley, Butterfly, Bat, Crab, ABCD, Shark)
- Backtesting con historial de trades
- Portfolio management (correlaciones entre activos)

---

## COSTOS ACTUALES
| Componente | Costo mensual |
|-----------|---------------|
| Google Cloud VM (e2-micro) | ~$6 USD |
| Anthropic Claude Sonnet 4.6 | ~$7-15 USD |
| Binance / CoinGecko / RSS / CMC | $0 |
| **Total** | **~$13-21 USD/mes** |

---

## CREDENCIALES Y ACCESOS
- **SSH**: `ssh amonroymarin@34.78.81.17`
- **GitHub**: `github.com/AMMAr1n/trading-agent`
- **Dashboard (pendiente)**: `https://34.78.81.17`
- **Telegram**: notificaciones activas
- **Binance**: Futuros USD-M producción

---

## NOTAS IMPORTANTES
- El agente usa **Algo API** de Binance para SL/TP (`POST /fapi/v1/algoOrder`)
- `usdt_free` de Binance ya descuenta el margen — NO restar `margin_in_use` de nuevo
- La BD se sincroniza automáticamente con Binance al reiniciar (`_restore_tracked_positions`)
- El aprendizaje requiere mínimo 5 trades cerrados para activarse (`LearningEngine`)
- MIN_SCORE=45 y AGENT_STAGE=1 están en `.env` de la VM
- Los chart patterns se detectan en Python puro (numpy/pandas) — NO consumen tokens de Claude
- El enfoque es top-down: el TF mayor (1W/1D) tiene poder de veto sobre el menor (1h)
- MAX_OPEN_TRADES=10 en .env

## FLUJO DE DEPLOY
1. Modificar archivos en VS Code (Mac)
2. `git add -A && git commit -m "mensaje" && git push origin main`
3. SSH a VM: `cd ~/trading-agent && git pull && sudo systemctl restart trading-agent`
4. Verificar: `sudo journalctl -u trading-agent -n 30 --no-pager`
