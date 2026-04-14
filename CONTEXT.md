# Trading Agent — Contexto del Proyecto
**Última actualización: 14 Abril 2026**

---

## INFRAESTRUCTURA
- **VM GCP**: `instance-20260410-045221` | IP: `34.78.81.17` | Usuario: `amonroymarin`
- **Repositorio**: `github.com/AMMAr1n/trading-agent` | **Rama**: `main`
- **Servicio**: `trading-agent.service` (systemd)
- **DB**: `~/trading-agent/database/trading_agent.db` (SQLite)
- **Binance**: Futuros USD-M, Cross Margin, HMAC API, producción
- **Saldo actual**: ~$59.96 USDT en Futuros

### Comandos útiles
```bash
# Ver logs en tiempo real
sudo journalctl -u trading-agent -f

# Ver últimos 30 logs
sudo journalctl -u trading-agent -n 30

# Reiniciar agente
sudo systemctl restart trading-agent

# Deploy completo
cd ~/trading-agent && git pull && sudo systemctl restart trading-agent

# Consultar BD
sqlite3 ~/trading-agent/database/trading_agent.db "SELECT id, symbol, direction, status, entry_price, stop_loss, take_profit, pnl_usd FROM trades;"

# Ver versiones
sqlite3 ~/trading-agent/database/trading_agent.db "SELECT * FROM versions;"
```

---

## VERSIÓN ACTUAL: v0.6.0 (14 Abril 2026)

### POSICIONES ABIERTAS (3)
| Par | Dir | Entrada | SL | TP | Estado |
|-----|-----|---------|----|----|--------|
| DOGEUSDT | LONG 20x | $0.09406 | $0.090 | $0.100 | Abierta |
| SOLUSDT | LONG 1x | $83.67 | $81.55 | $87.91 | Abierta |
| BNBUSDT | LONG 1x | $612.71 | $596.27 | $633.00 | Abierta |

---

## ARQUITECTURA

```
collector/ → analyzer/ → brain/ → executor/ → database/
Binance      Indicators   Claude    OrderExec   SQLite
CMC          TA-Lib       Prompt    Monitor     Versions
CoinGecko    Scorer       Learning  Balance
RSS          1h/2h/1d/1w
```

### Variables .env relevantes
```
MIN_SCORE=45
MAX_OPEN_TRADES=3
RISK_PCT=1.0
ATR_MULTIPLIER=1.5
MIN_TRADE_AMOUNT_USD=5.5
MAX_CAPITAL_PCT=60
LOOP_INTERVAL_MIN=20
BINANCE_TESTNET=false
RESERVE_PCT=10
```

---

## CHANGELOG RESUMIDO

### v0.6.0 — 13-14 Abril 2026
- ta-lib patrones de velas (+40 patrones)
- Timeframe 1W — tendencia macro
- Fix SL=TP en pares de precio bajo
- MIN_SCORE subido a 45
- Pipeline de aprendizaje desde BD (get_learning_context)
- Notificaciones con desglose completo de saldo
- Fix doble descuento de margen en balance.py
- Cancelación automática de órdenes huérfanas SL/TP
- Sincronización automática Binance→BD al iniciar
- Schema BD ampliado: 33 campos + tabla versions
- Fix: timezone import en main.py
- Fix: reporte periódico usa BD en vez de _daily_trades
- Fix: _notify_closed obtiene P&L real desde fetch_my_trades
- Fix: notify_skipped unifica notify_no_funds y notify_insufficient_amount
- Dashboard React (artifact) — pendiente deploy en VM

### v0.5.0 — 10-12 Abril 2026
- SHORT habilitado en todos los pares
- ATR-based position sizing
- CoinGecko sentiment, RSS feeds
- Timeframe 1D como modificador de score
- Persistencia de posiciones al reiniciar

### v0.4.0 — 10 Abril 2026
- Deploy en Google Cloud VM (IP estática)
- Migración de Railway a GCP

---

## SCHEMA DE BD

### Tabla `trades` (33 campos)
```sql
id, symbol, direction, trading_mode, amount_usd,
entry_price, stop_loss, take_profit, leverage, score,
reasoning, status, opened_at, closed_at, exit_price,
pnl_usd, pnl_pct, close_reason, order_id,
volume_ratio, trend_1h, trend_1d, trend_1w, patterns,
hour_opened, fear_greed, score_breakdown,
balance_total, balance_reserve, balance_operable,
duration_min, sl_tp_method, version
```

### Tabla `versions`
```sql
id, version, description, implemented_at, notes
```

---

## BUGS PENDIENTES

### Críticos
- [ ] Notificaciones de cierre a veces no llegan (fix implementado en v0.6.0, pendiente validar)
- [ ] SL/TP huérfano al cierre (fix implementado en v0.6.0, pendiente validar)

### Menores
- [ ] Reporte periódico muestra posiciones abiertas en 0 (fix implementado, pendiente validar)
- [ ] R/R en mensaje vs razonamiento de Claude a veces inconsistente

---

## PRÓXIMOS PASOS

### Inmediato
1. **Validar** fixes de notificaciones de cierre y SL/TP huérfano con próximo trade cerrado
2. **Deploy dashboard** en VM (Flask + React + HTTPS)

### Dashboard (pendiente deploy)
- Archivo: `dashboard_v2.jsx` (artifact React con datos mock)
- Stack: Flask (backend API) + React (frontend) + HTTPS autofirmado
- Puerto: 443
- Auth: usuario/contraseña en .env (`DASHBOARD_USER`, `DASHBOARD_PASSWORD`)
- Firewall GCP: puerto 443 ya abierto
- SSL: certificado autofirmado ya generado en `~/trading-dashboard/ssl/`
- Estructura creada: `~/trading-dashboard/frontend/src/` y `~/trading-dashboard/backend/`

### Pasos para deploy del dashboard
```bash
# 1. Instalar Node.js (ya instalado)
# 2. Crear proyecto React en ~/trading-dashboard/frontend/
cd ~/trading-dashboard/frontend
npx create-react-app . --template minimal

# 3. Copiar dashboard_v2.jsx → src/App.js
# 4. npm install recharts lucide-react
# 5. npm run build

# 6. Crear API Flask en ~/trading-dashboard/backend/app.py
# 7. Crear servicio systemd trading-dashboard.service
# 8. Acceder en https://34.78.81.17
```

### Mediano plazo
- P3.2 — Financial Modeling Prep (FMP) noticias por ticker
- P3.3 — Reactivar Web Search cuando el agente sea rentable
- Análisis de historial de trades para backtesting

---

## COSTOS ACTUALES
| Componente | Costo mensual |
|-----------|---------------|
| Google Cloud VM (e2-micro) | ~$6 USD |
| Anthropic Claude Sonnet 4.6 | ~$7-15 USD |
| **Total** | **~$13-21 USD/mes** |

---

## CREDENCIALES Y ACCESOS
- **SSH**: `ssh amonroymarin@34.78.81.17`
- **GitHub**: `github.com/AMMAr1n/trading-agent`
- **Dashboard (pendiente)**: `https://34.78.81.17` | user: `.env DASHBOARD_USER`
- **Telegram**: notificaciones activas
- **Binance**: Futuros USD-M producción

---

## NOTAS IMPORTANTES
- El agente usa **Algo API** de Binance para SL/TP (`POST /fapi/v1/algoOrder`)
- `usdt_free` de Binance ya descuenta el margen — NO restar `margin_in_use` de nuevo
- La BD se sincroniza automáticamente con Binance al reiniciar (`_restore_tracked_positions`)
- El aprendizaje requiere mínimo 5 trades cerrados para activarse (`get_learning_context`)
- MIN_SCORE=45 está en `.env` de la VM, no en el código
