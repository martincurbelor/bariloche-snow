import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GMAIL_USER        = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD= os.environ["GMAIL_APP_PASSWORD"]
TO_EMAIL          = os.environ["TO_EMAIL"]

DATOS_JSON  = os.path.join(os.path.dirname(__file__), "..", "docs", "datos.json")
DASHBOARD_URL = "https://martincurbelor.github.io/bariloche-snow"


def load_data():
    with open(DATOS_JSON, encoding="utf-8") as f:
        return json.load(f)


def build_summary(data):
    lines = []
    for resort_id, resort in data["resorts"].items():
        fc = resort["forecast"]
        next14 = fc[:14]
        total_snow = sum(d["snow_cm"] for d in next14)
        snow_days = [d for d in next14 if d["snow_cm"] > 0]
        best_day = max(next14, key=lambda d: d["snow_cm"])
        lines.append(
            f"- {resort['name']} (país: {resort['country']}): "
            f"{total_snow:.1f} cm totales en 14 días, "
            f"{len(snow_days)} días con nevadas, "
            f"mejor día {best_day['date']} con {best_day['snow_cm']} cm. "
            f"Temp min hoy: {fc[0]['temp_min']}°C, temp max hoy: {fc[0]['temp_max']}°C."
        )
    return "\n".join(lines)


def ask_claude(summary_text):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Sos un corresponsal de nieve con humor uruguayo, fanático del esquí y un poco dramático.

CONTEXTO IMPORTANTE que debés respetar siempre:
- Cerro Catedral está en Argentina (Bariloche). Es el centro principal del análisis.
- Las Leñas está en Argentina (Mendoza). Es competencia argentina.
- Valle Nevado está en Chile. Es la opción chilena.
- Ir a Chile significa cruzar la cordillera o los Andes. NUNCA uses "cruzar el charco" (eso es el Atlántico). Si recomendás Chile, decí "cruzar la cordillera" o "cruzar los Andes".
- Los tres centros abren temporada entre el 20 de junio y el 10 de julio. Antes de esa fecha están cerrados. No recomiendes ir a ninguno si la fecha sugerida cae antes de la apertura.
- Si hay poca o nula nieve en los 14 días, la recomendación debe ser esperar o monitorear, no ir.

Hoy es {datetime.now(timezone.utc).strftime('%Y-%m-%d')}. Analizá los datos y escribí un mensaje en español con:
1. Estado de Cerro Catedral (siempre primero)
2. Comparativa: ¿Las Leñas (Argentina) o Valle Nevado (Chile) tiene más nieve? Solo recomendás Chile si Valle Nevado supera claramente a los argentinos.
3. Recomendación concreta y coherente con los datos: ¿vale la pena ir, esperar, o los centros ni abren todavía?
4. Cerrá con esta frase exacta: "Ver pronóstico completo: {DASHBOARD_URL}"

Los puntos 2 y 3 tiene sentido incluirlos en el comentario cada cierto tiempo, pongamos una vez cada 10 dias.

Tono cómico pero coherente. Máximo 125 palabras. Solo texto plano, sin markdown, sin líneas en blanco entre párrafos.

Datos:
{summary_text}
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def send_email(body):
    subject = f"Reporte de nieve {datetime.now().strftime('%d/%m/%Y')} — Catedral & Co."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = TO_EMAIL

    plain = MIMEText(body, "plain", "utf-8")
    msg.attach(plain)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, TO_EMAIL, msg.as_string())

    print(f"Mail enviado a {TO_EMAIL}")


def main():
    print("Leyendo datos...")
    data = load_data()

    print("Armando resumen...")
    summary = build_summary(data)
    print(summary)

    print("Consultando Claude...")
    analysis = ask_claude(summary)
    print("\n--- Mensaje generado ---")
    print(analysis)

    print("\nEnviando mail...")
    send_email(analysis)
    print("Listo.")


if __name__ == "__main__":
    main()
