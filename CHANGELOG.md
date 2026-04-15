# Trading Agent — CHANGELOG
**Última actualización: 15 de abril de 2026**

---

## CHANGELOG

### v0.7.1 — 15 Abril 2026

**Chart Patterns — Enfoque top-down**
- Feat: enfoque top-down en MTFAligner — 1W/1D mandan dirección, 4h confirma, 1h solo timing
- Feat: veto del TF mayor cuando tiene patrón claro (≥60% conf) en dirección contraria
- Feat: parámetros de detección ajustados por timeframe (min_bars, tolerance, swing_window)
- Feat: MTFAligner integrado en `analyzer.analyze_symbol()` — patrones se detectan ANTES del score
- Refactor: `main.py` simplificado — MTFAligner ya no corre en process_signal

**6 patrones nuevos (10 → 16 total)**
- Feat: Bull Flag — pole alcista + consolidación descendente (~75% success rate)
- Feat: Bear Flag — pole bajista + consolidación ascendente (~75% success rate)
- Feat: Pennant — movimiento fuerte + triángulo simétrico compacto (~56% success rate)
- Feat: Cup & Handle — U-shape redondeado + pullback < 50% (~70% success rate)
- Feat: Triple Top — 3 rechazos en resistencia (~85% success rate)
- Feat: Triple Bottom — 3 rebotes en soporte (~85% success rate)

---

### v0.7.0 — 14-15 Abril 2026

**Chart Pattern Engine (6 módulos nuevos)**
- Feat: `patterns.py` — detector de 10 formaciones chartistas multi-vela
  - Reversal: double_top, double_bottom, head_and_shoulders, inverse_H&S
  - Continuation: ascending_triangle, descending_triangle, symmetrical_triangle, rising_wedge, falling_wedge, rectangle
- Feat: `breakout.py` — validador de breakouts (volumen ≥1.5x, body ratio, retest detection)
- Feat: `targets.py` — calculador de TP/SL por geometría del patrón (proyección de altura)
- Feat: `mtf_alignment.py` — alineación multi-timeframe con consenso ponderado
- Feat: `regime.py` — detector de régimen (trending/ranging/volatile) usando ADX + ATR + BB width
- Feat: `learning.py` — motor de aprendizaje evolutivo (reemplaza `get_learning_context`)

**Score re-ponderado**
- Change: EMAs 25→20, Volumen 25→15, MACD 20→10, RSI 15→10, BB 15→5
- Feat: Chart Patterns 25pts (NUEVO) — confianza del patrón + alineación MTF
- Feat: Breakout Quality 15pts (NUEVO) — strong/moderate/weak/failed

**Learning Engine**
- Feat: 4 etapas de evolución (Aprendiz → Practicante → Competente → Experto)
- Feat: avance/retroceso automático basado en win rate y profit factor
- Feat: pattern performance tracking (win rate por patrón + régimen)
- Feat: bias detector (sesgo long, overtrading, horas malas)
- Feat: reglas adaptativas generadas del historial propio
- Feat: `AGENT_STAGE=1` en .env (empieza como Aprendiz)

**Prompt de Claude enriquecido**
- Feat: sección `=== CHART PATTERNS DETECTADOS ===` por timeframe
- Feat: sección `=== VALIDACIÓN DE BREAKOUT ===` con calidad y métricas
- Feat: sección `=== TARGETS POR GEOMETRÍA DEL PATRÓN ===`
- Feat: sección `=== RÉGIMEN DE MERCADO ===` con recomendaciones
- Feat: sección `=== TU EXPERIENCIA COMO TRADER ===` (reemplaza historial simple)
- Change: system prompt incluye reglas de chart patterns y régimen

**Base de datos**
- Feat: migración `migrate_v070.py` — 14 columnas nuevas (33 → 47 campos)
- Feat: campos de pattern tracking: `pattern_type`, `pattern_confidence`, `breakout_quality`, `breakout_score`
- Feat: campos de régimen: `regime`, `regime_adx`
- Feat: campos de calidad: `projected_rr`, `actual_rr`, `max_favorable_excursion`, `max_adverse_excursion`, `efficiency`
- Feat: campos MTF: `mtf_alignment_score`, `mtf_consensus`
- Feat: campo de etapa: `agent_stage`

**Config**
- Change: `MAX_OPEN_TRADES=10` (era 3)
- Feat: `AGENT_STAGE=1` en .env

---

### v0.6.0 — 13-14 Abril 2026

**Análisis técnico**
- Feat: ta-lib patrones de velas (+40 patrones con fallback a numpy)
- Feat: timeframe 1W — tendencia macro (+8pts alineado, -15pts contradicción)
- Feat: patrones de velas en prompt de Claude con emojis 📈/📉
- Fix: SL=TP en pares de precio bajo — precisión dinámica + distancia mínima 1%/2%
- Config: MIN_SCORE subido a 45 en `.env`

**Pipeline de aprendizaje**
- Feat: `get_learning_context()` en BD — win rate por condición (dirección, tendencia 1D, volumen, score)
- Feat: sección `=== TU HISTORIAL RECIENTE ===` en prompt de Claude
- Feat: Claude ajusta criterio basado en historial propio (confirmado en logs)
- Feat: 8 columnas nuevas en tabla `trades`

**Base de datos**
- Feat: schema ampliado a 33 campos por trade
- Feat: tabla `versions` para trazabilidad de mejoras vs rendimiento
- Feat: sincronización automática Binance→BD al iniciar (`_restore_tracked_positions`)
- Feat: obtención de SL/TP desde Algo API al sincronizar posiciones

**Notificaciones**
- Feat: `notify_skipped` unifica `notify_no_funds` y `notify_insufficient_amount`
- Fix: doble descuento de margen en `balance.py`
- Fix: reporte periódico usa BD en vez de `_daily_trades`
- Fix: `_notify_closed` obtiene precio real desde `fetch_my_trades`

**SL/TP**
- Fix: cancelación automática de órdenes huérfanas al cerrar posición
- Fix: `place_sl_tp` usa `POST /fapi/v1/algoOrder` con `algoType=CONDITIONAL`

**Infraestructura**
- Fix: `timezone` import faltante en `main.py`
- Config: web search desactivado para reducir costos
- Config: ciclo 20 minutos

---

### v0.5.0 — 10-12 Abril 2026
- Feat: SHORT habilitado, ATR position sizing, CoinGecko, RSS feeds
- Feat: Timeframe 1D como modificador de score
- Fix: precio entrada real, P&L estimado, persistencia al reiniciar

---

### v0.4.0 — 11 Abril 2026
- Feat: Deploy GCP VM, IP estática, systemd

---

### v0.3.0 — 10 Abril 2026
- Feat: Binance Futuros, Claude Sonnet, Telegram, SQLite, reporte periódico

---

## PLAN DE IMPLEMENTACIÓN

### ✅ Completados
| ID | Mejora | Versión |
|----|-------|---------|
| P1.1 | Bug SL=TP pares precio bajo | v0.6.0 |
| P1.2 | SL/TP via Algo API | v0.6.0 |
| P1.3 | MIN_SCORE=45 | v0.6.0 |
| P2.1 | Timeframes 1W + 4h | v0.6.0 |
| P2.2 | ta-lib patrones de velas | v0.6.0 |
| P3.1 | Pipeline aprendizaje desde BD | v0.6.0 |
| P4.1 | Chart pattern engine (10 patrones) | v0.7.0 |
| P4.2 | Breakout validator | v0.7.0 |
| P4.3 | Target calculator geométrico | v0.7.0 |
| P4.4 | Market regime detector | v0.7.0 |
| P4.5 | Learning engine evolutivo (4 etapas) | v0.7.0 |
| P4.6 | Score re-ponderado (patterns + breakout) | v0.7.0 |
| P5.1 | Top-down MTF alignment | v0.7.1 |
| P5.2 | 6 patrones nuevos (16 total) | v0.7.1 |
| P5.3 | Parámetros por timeframe | v0.7.1 |
| P5.4 | Pipeline integrado (MTF en analyzer) | v0.7.1 |

### ⏳ Pendientes validación
- [ ] Notificaciones de cierre con P&L real
- [ ] Cancelación SL/TP huérfano automática
- [ ] Reporte periódico posiciones abiertas
- [ ] Chart patterns mejoran win rate vs v0.6.0

### 🔜 Próximos
- **Tier 2 patterns**: Channel Up/Down, Broadening Wedge, Rounding Top, Three Drives (4 más → 20 total)
- **Dashboard VM**: Flask + React + HTTPS
- **P3.2**: Financial Modeling Prep (FMP) noticias por ticker
- **P3.3**: Reactivar Web Search cuando el agente sea rentable
- **Patrones armónicos**: Gartley, Butterfly, Bat, Crab, ABCD, Shark (largo plazo)

---

## ESTADO ACTUAL (15 Abril 2026)

| Campo | Valor |
|-------|-------|
| Versión | v0.7.1 |
| Saldo total | ~$59.74 USDT |
| Saldo operable | ~$25.22 USDT |
| Posiciones abiertas | 4 (DOGE, SOL, BNB, ADA) |
| Win rate histórico | ~33% (pendiente mejorar con v0.7.x) |
| Ciclo | Cada 20 minutos |
| Chart patterns activos | 16 |
| Régimen detectado | volatile (ADX: 13, ATR: 5.3%) |
| Etapa del agente | 1 (Aprendiz) |
| Aprendizaje activo | Sí (LearningEngine) |

---

## COSTOS ACTUALES

| Componente | Costo mensual |
|-----------|---------------|
| Google Cloud VM (e2-micro) | ~$6 USD |
| Anthropic Claude Sonnet 4.6 | ~$7-15 USD |
| Binance / CoinGecko / RSS / CMC | $0 |
| Chart pattern detection (Python) | $0 |
| **Total** | **~$13-21 USD/mes** |
