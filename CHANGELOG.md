# Trading Agent — Control de Versiones

## Ambientes
| Ambiente | Descripción | Estado |
|----------|-------------|--------|
| **DEV** | Local en VS Code — desarrollo y pruebas de código | 🔜 Pendiente configurar |
| **TEST** | VM GCP separada — validación antes de producción | 🔜 Pendiente configurar |
| **PROD** | VM GCP actual (`instance-20260410-045221`) — dinero real | ✅ Activo |

---

## v0.4.0 — 2026-04-11
### Nuevas funcionalidades
- **Timeframe 1D** como filtro de tendencia mayor — el agente ya no abre LONGs contra la tendencia diaria
- **ATR-based position sizing** — tamaño de posición basado en volatilidad real del par (Fixed Fractional 1% de riesgo)
- **Scorer rediseñado** con framework de análisis técnico profesional:
  - Nuevo componente: alineación de EMAs (25 pts) — reemplaza RSI como componente principal
  - RSI ahora puntúa en modo tendencial (RSI 50-65 = momentum alcista saludable) Y reversión (RSI < 35 = sobrevendido)
  - Bollinger actualizado — da puntos en zona media, no solo extremos
- **ATR calculado** en `indicators.py` y disponible para position sizing
- **Mensajes de Telegram divididos** — razonamiento completo en mensaje separado, sin truncar
- **Errores categorizados** en Telegram: ⚠️ Monto insuficiente, 📡 Error de conexión, 🚨 Error inesperado
- **DB sincronizada con Binance al reiniciar** — posiciones abiertas se restauran en el monitor automáticamente
- **`_restore_tracked_positions()`** — al iniciar, carga posiciones de DB y cierra las que ya no están en Binance

### Correcciones
- Fix crítico: `trade_id` ahora se registra en DB **antes** de pasarlo al monitor
- PEPE y SHIB removidos del mensaje de "Agente Iniciado"
- `position_monitor.py` ahora cierra posiciones en DB cuando Binance las cierra

### Archivos modificados
- `analyzer/scorer.py` — rediseño completo
- `analyzer/analyzer.py` — filtro 1D, indicators_1d en TradingSignal
- `analyzer/indicators.py` — ATR agregado
- `collector/models.py` — `"1d"` agregado a CANDLE_TIMEFRAMES
- `brain/prompt_builder.py` — contexto 1D en prompt de Claude
- `executor/executor.py` — ATR-based sizing, control de capital comprometido
- `executor/notifier.py` — mensajes divididos, errores categorizados
- `executor/position_monitor.py` — sincronización DB, acepta `db` y `trade_id`
- `main.py` — `_restore_tracked_positions()`, orden correcto trade_id/register

---

## v0.3.0 — 2026-04-10 (tarde)
### Nuevas funcionalidades
- **SL/TP embebido** en la orden de apertura — compatible con API key HMAC de Binance
- **Capital comprometido** rastreado internamente y sincronizado con margen real de Binance
- **Position Monitor** — detecta cierres de Binance y notifica por Telegram
- **Cierre de emergencia** — si precio cruza SL/TP y Binance no ejecutó, el monitor cierra manualmente
- **MAX_OPEN_TRADES** sincronizado con posiciones reales de Binance (no DB)
- **Mínimo absoluto** `MIN_TRADE_AMOUNT_USD=$5.50` por operación
- **Ajuste automático al mínimo** — si monto calculado < $5.50, sube automáticamente
- **Validación de mínimos de Binance** antes de ejecutar (min_amount, min_cost por par)

### Correcciones
- Fix error `-4120` SL/TP — resuelto con método embebido en create_order
- Fix saldo $0.00 en notificaciones — `account_balance` ahora se pasa correctamente
- Fix `MAX_OPEN_TRADES=10` — la DB acumulaba posiciones cerradas manualmente
- BNB: cantidad ajustada al mínimo de Binance en vez de rechazar

### Archivos modificados
- `executor/order_executor.py` — SL/TP embebido, validación mínimos, `quantity` en OrderResult
- `executor/executor.py` — capital comprometido, saldo real en notificaciones
- `executor/balance.py` — margen real de Binance descontado del capital operable, `MIN_TRADE_AMOUNT_USD`
- `executor/position_monitor.py` — nuevo archivo
- `executor/__init__.py` — exporta PositionMonitor
- `main.py` — integración del monitor

---

## v0.2.0 — 2026-04-10 (mañana)
### Nuevas funcionalidades
- **Deploy en Google Cloud** con IP estática — resuelve problema de whitelist de Binance Futuros
- **Migración de Railway a GCP** — VM e2-micro, ~$7/mes
- **Systemd service** — agente corre 24/7, se reinicia automáticamente
- **VoBo desactivado** (`VOBO_MIN_PCT=100`) — agente opera autónomamente
- **Notificaciones por Telegram** — migrado desde WhatsApp/Twilio
- **Timeframe de confirmación** cambiado de 4h a 2h — menos restrictivo en mercados laterales
- **PEPE y SHIB** removidos de `SPOT_TIER3` — pendiente implementar Spot

### Correcciones
- Fix símbolo PEPEUSDT — no disponible en Binance Futuros
- Fix `MIN_SCORE=30` leyendo correctamente del `.env`

### Archivos modificados
- `collector/models.py` — SPOT_TIER3 vacío
- `analyzer/analyzer.py` — CONFIRMATION_TIMEFRAME = "2h"
- `executor/notifier.py` — migración a Telegram
- `.env` — VOBO_MIN_PCT=100, credenciales Telegram

---

## v0.1.0 — 2026-04-09
### Release inicial
- Arquitectura completa de 5 capas: Collector → Analyzer → Brain → Executor → Notifier
- Colector de datos: Binance (precios + velas) + CoinMarketCap (Fear & Greed, dominancia)
- Analizador técnico: RSI, MACD, Bollinger Bands, EMAs, volumen, soportes/resistencias
- Brain: Claude Sonnet como motor de decisión con prompt estructurado
- Executor: órdenes de mercado en Binance Futuros con apalancamiento dinámico
- Base de datos SQLite para tracking de operaciones y señales
- Loop principal cada 5 minutos con reportes cada 6 horas
- Deploy inicial en Railway (descartado por falta de IP estática)

---

## Roadmap — Próximas versiones

### v0.5.0 — Pendiente
- [ ] Web Search en cada decisión de Claude (noticias recientes del par)
- [ ] CryptoPanic API — noticias por crypto (llamada condicional cuando score ≥ 30)
- [ ] Bots de X/Twitter — sentimiento de influencers crypto
- [ ] Cierre de posición si SL/TP falla al colocarse (seguridad nocturna)
- [ ] Ambientes DEV/TEST/PROD separados

### Futuro
- [ ] Spot trading para PEPE y SHIB
- [ ] Signal postmortem — aprendizaje de operaciones pasadas
- [ ] Kelly Criterion para sizing (requiere 100+ trades de historial)
- [ ] Dashboard web para monitoreo visual
