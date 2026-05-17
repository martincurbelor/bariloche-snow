# Bariloche-snow

Scraper de pronósticos de nieve + dashboard + email diario para Cerro Catedral, Las Leñas y Valle Nevado. Corre 100% en GitHub Actions (migrado desde Windows Scheduled Tasks el 2026-05-13). Repo público: `martincurbelor/bariloche-snow`. Dashboard servido por GitHub Pages: https://martincurbelor.github.io/bariloche-snow

## Estructura

- `fetch_forecast.py` — scrapea Open-Meteo + snow-forecast.com + webcams varitech, escribe `docs/datos.json` y descarga snapshots a `docs/cameras/`.
- `notify/analyze_and_notify.py` — lee `docs/datos.json`, le pide a Claude (Sonnet 4.6) un resumen humorístico en tono narrador uruguayo (máx 60 palabras), lo manda por Gmail SMTP.
- `docs/` — servido por GitHub Pages. `index.html` es el dashboard, `datos.json` el snapshot actual, `cameras/` las imágenes de webcams.
- `.github/workflows/forecast.yml` — cron `17 */2 * * *` UTC (cada 2h). Corre `fetch_forecast.py` y commitea cambios en `docs/`.
- `.github/workflows/notify.yml` — cron `45 7 * * *` UTC = **04:45 ART**. Corre el notificador.
- `update.bat` — script legacy para correr local desde Windows (todavía sirve para debug manual).

## Cronograma diario

| Hora UTC | Hora ART | Qué pasa |
|---|---|---|
| `*/2:17` | cada 2h | Forecast actualiza `docs/datos.json` |
| `07:45` | 04:45 | Workflow `notify` manda el email |
| `11:00` | 08:00 | Bot externo (en servidor cloud) lee Gmail y reenvía a WhatsApp |

El bot de WhatsApp es **single-shot, sin idempotencia ni catch-up**. Si el mail no está en la inbox a las 11:00 UTC, ese día se pierde. Por eso el notify está a las 04:45 ART — para absorber delays de hasta ~3h del cron de GitHub Actions (el 2026-05-15 el delay fue de 2h18min y justo se pasó del buffer anterior de 2h15min).

## Secrets (GitHub repo-level)

`ANTHROPIC_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `TO_EMAIL`. Configurados como **Repository secrets**, no environment secrets. Si alguien crea un environment por error, los jobs necesitan `environment: <name>` para verlos.

Los secrets se `.strip()`ean al cargar — GitHub Secrets pegados del clipboard pueden traer `\n` al final que rompe headers HTTP (nos pasó durante la migración).

## Gotchas conocidos

- **Open-Meteo SSL handshake timeouts** en runners de Actions (más flaky que red local). Mitigado con retry/backoff en helper `http_get()`.
- **`fetch_forecast.py` saltea su propio `git_push()` cuando `GITHUB_ACTIONS=true`** — el workflow se encarga del commit/push. Si lo corrés local, sí hace push.
- **Windows Scheduled Tasks `BarilocheSnowForecast` y `BarilocheSnowNotify` están Disabled, no borradas** — rollback path si Actions falla. Re-enable con `Enable-ScheduledTask -TaskName <name>`.
- **`notify/.env`** existe local para debug manual desde la PC. Gitignored.

## Debugging

Empezar por los **logs del workflow en GitHub Actions**, no por correr el script local — el script suele andar bien standalone, los problemas casi siempre son del entorno del runner (red, secrets, paths).

## Downstream

El email lo lee `bot.py` (template en `C:\Users\marti\Downloads\bot.py`, copia deployada en servidor cloud con creds reales). Busca `(ON <today-UTC> SUBJECT "Reporte de nieve")` via IMAP a las 11:00 UTC y reenvía a un grupo de WhatsApp + 2 JIDs personales.

## Convenciones

- Tono del email: humorístico, narrador uruguayo, máx 60 palabras. No tocar sin pensarlo — el usuario lo ajustó iterando.
- Commits del bot tienen formato `update: forecast YYYY-MM-DD HH:MM UTC` (lo hace el workflow, no tocar).
