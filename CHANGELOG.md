# Trading Agent — CHANGELOG
**Última actualización: 14 de abril de 2026**

---

## CHANGELOG

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
- Feat: 8 columnas nuevas en tabla `trades`: `volume_ratio`, `trend_1h`, `trend_1d`, `trend_1w`, `patterns`, `hour_opened`, `fear_greed`, `score_breakdown`

**Base de datos**
- Feat: schema ampliado a 33 campos por trade
- Feat: tabla `versions` para trazabilidad de mejoras vs rendimiento
- Feat: campos de saldo: `balance_total`, `balance_reserve`, `balance_operable`
- Feat: campos operativos: `duration_min`, `sl_tp_method`, `version`
- Feat: índice único en `order_id` (trazabilidad con Binance)
- Feat: sincronización automática Binance→BD al iniciar (`_restore_tracked_positions`)
- Feat: obtención de SL/TP desde Algo API al sincronizar posiciones

**Notificaciones**
- Feat: `notify_skipped` unifica `notify_no_funds` y `notify_insufficient_amount`
- Fix: doble descuento de margen en `balance.py`
- Fix: balance refrescado post-apertura y post-cierre
- Fix: reporte periódico usa BD en vez de `_daily_trades`
- Fix: `_notify_closed` obtiene precio real desde `fetch_my_trades`
- Fix: `_notify_closed` detecta si fue TP o SL comparando con niveles
- Fix: título del reporte dinámico (Mañana / Mediodía / Tarde / Noche)

**SL/TP**
- Fix: cancelación automática de órdenes huérfanas al cerrar posición
- Fix: `place_sl_tp` usa `POST /fapi/v1/algoOrder` con `algoType=CONDITIONAL`
- Fix: `_close_in_db` no sobreescribe si ya fue actualizado por `_notify_closed`

**Infraestructura**
- Fix: `timezone` import faltante en `main.py`
- Fix: `executor.db` referencia asignada desde `main.py`
- Fix: R/R consistente — instrucción explícita en prompt
- Config: web search desactivado para reducir costos
- Config: ciclo 20 minutos

**Dashboard (artifact — pendiente deploy VM)**
- Feat: Login, posiciones en tiempo real, P&L, breakeven, historial
- Feat: Changelog visual, arquitectura interactiva con panel lateral

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
| ID | Fix | Versión |
|----|-----|---------|
| P1.1 | Bug SL=TP pares precio bajo | v0.6.0 |
| P1.2 | SL/TP via Algo API | v0.6.0 ✅ |
| P1.3 | MIN_SCORE=45 | v0.6.0 |
| P2.1 | Timeframes 1W + 4h | v0.6.0 |
| P2.2 | ta-lib patrones de velas | v0.6.0 |
| P3.1 | Pipeline aprendizaje desde BD | v0.6.0 |

### ⏳ Pendientes validación
- [ ] Notificaciones de cierre con P&L real
- [ ] Cancelación SL/TP huérfano automática
- [ ] Reporte periódico posiciones abiertas

### 🔜 Próximos
- **Dashboard VM**: Flask + React + HTTPS (puerto 443 abierto, SSL generado)
- **P3.2**: Financial Modeling Prep (FMP) noticias por ticker
- **P3.3**: Reactivar Web Search cuando el agente sea rentable

---

## ESTADO ACTUAL (14 Abril 2026)

| Campo | Valor |
|-------|-------|
| Versión | v0.6.0 |
| Saldo total | ~$59.96 USDT |
| Saldo operable | ~$36.14 USDT |
| Posiciones abiertas | 3 (DOGE, SOL, BNB) |
| Win rate histórico | ~33% (2 TP / 4 SL aprox.) |
| Ciclo | Cada 20 minutos |
| Aprendizaje activo | Sí (desde 5+ trades cerrados) |

---

## COSTOS ACTUALES

| Componente | Costo mensual |
|-----------|---------------|
| Google Cloud VM (e2-micro) | ~$6 USD |
| Anthropic Claude Sonnet 4.6 | ~$7-15 USD |
| Binance / CoinGecko / RSS / CMC | $0 |
| **Total** | **~$13-21 USD/mes** |
