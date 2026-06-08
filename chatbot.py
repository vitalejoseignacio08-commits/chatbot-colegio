from flask import Flask, request, jsonify
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import os
import io
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

# Google config
DRIVE_FOLDER_ID = "1_o3PbEP9KOaJ4kN-0eu3j3JyKUgG0vSj"

# Meta config
PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID", "1201535816372087")
META_TOKEN = os.environ.get("META_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "colegio_agrotecnico_bot")

# Estado de conversación para agendar citas
conversaciones = {}

def get_credentials():
    refresh_token = "1//" + os.environ.get("GOOGLE_REFRESH_TOKEN", "")
    client_id = os.environ.get("GOOGLE_CLIENT_ID_PREFIX", "") + os.environ.get("GOOGLE_CLIENT_ID_SUFFIX", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token"
    )
    creds.refresh(Request())
    return creds

def get_drive_service():
    return build("drive", "v3", credentials=get_credentials())

def get_calendar_service():
    return build("calendar", "v3", credentials=get_credentials())

def enviar_mensaje(numero, texto):
    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {"body": texto}
    }
    requests.post(url, headers=headers, json=data)

def descargar_media_meta(media_id):
    url = f"https://graph.facebook.com/v25.0/{media_id}"
    headers = {"Authorization": f"Bearer {META_TOKEN}"}
    response = requests.get(url, headers=headers).json()
    media_url = response.get("url")
    mime_type = response.get("mime_type", "application/octet-stream")
    media_content = requests.get(media_url, headers=headers).content
    return media_content, mime_type

def subir_a_drive(contenido, nombre_archivo, mimetype):
    try:
        service = get_drive_service()
        file_stream = io.BytesIO(contenido)
        media = MediaIoBaseUpload(file_stream, mimetype=mimetype)
        file_metadata = {"name": nombre_archivo, "parents": [DRIVE_FOLDER_ID]}
        service.files().create(body=file_metadata, media_body=media).execute()
        return True
    except Exception as e:
        print(f"Error subiendo a Drive: {e}")
        return False

def agendar_cita(nombre, fecha_str, motivo, numero, hora_str="09:00"):
    try:
        service = get_calendar_service()
        fecha = datetime.strptime(fecha_str, "%d/%m/%Y")
        hora = datetime.strptime(hora_str, "%H:%M").time()
        inicio = fecha.replace(hour=hora.hour, minute=hora.minute)
        fin = inicio + timedelta(hours=1)
        evento = {
            "summary": f"Consulta: {nombre}",
            "description": f"Motivo: {motivo}\nWhatsApp: {numero}",
            "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
            "end": {"dateTime": fin.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
        }
        service.events().insert(calendarId="primary", body=evento).execute()
        return True
    except Exception as e:
        print(f"Error agendando cita: {e}")
        return False

MENU = """👋 ¡Hola! Soy el asistente del Colegio Agrotécnico.

¿En qué puedo ayudarte?

1️⃣ Horarios
2️⃣ Inscripción
3️⃣ Subir un documento
4️⃣ Agendar una consulta
5️⃣ Contacto con preceptoría

Respondé con el número de tu consulta."""

def responder(mensaje, numero):
    msg = mensaje.strip().lower()
    estado = conversaciones.get(numero, {})

    # Flujo de agendar cita
    if estado.get("paso") == "nombre":
        conversaciones[numero] = {"paso": "fecha", "nombre": mensaje.strip()}
        return "📅 ¿Qué fecha preferís para la consulta? Escribila en formato *DD/MM/AAAA*"

    if estado.get("paso") == "fecha":
        try:
            datetime.strptime(mensaje.strip(), "%d/%m/%Y")
            conversaciones[numero] = {"paso": "hora", "nombre": estado["nombre"], "fecha": mensaje.strip()}
            return "🕐 ¿A qué hora preferís la consulta? El horario disponible es de *7:30 a 13:00hs*. Escribila así: *HH:MM* (ejemplo: 09:00)"
        except:
            return "❌ Formato de fecha incorrecto. Escribila así: *DD/MM/AAAA* (ejemplo: 15/06/2026)"

    if estado.get("paso") == "hora":
        try:
            hora = datetime.strptime(mensaje.strip(), "%H:%M").time()
            if hora < datetime.strptime("07:30", "%H:%M").time() or hora > datetime.strptime("13:00", "%H:%M").time():
                return "❌ El horario debe ser entre las *7:30 y las 13:00hs*. Intentá de nuevo."
            conversaciones[numero] = {"paso": "motivo", "nombre": estado["nombre"], "fecha": estado["fecha"], "hora": mensaje.strip()}
            return "📝 ¿Cuál es el motivo de la consulta?"
        except:
            return "❌ Formato de hora incorrecto. Escribila así: *HH:MM* (ejemplo: 09:00)"

    if estado.get("paso") == "motivo":
        nombre = estado["nombre"]
        fecha = estado["fecha"]
        hora = estado["hora"]
        motivo = mensaje.strip()
        conversaciones.pop(numero, None)
        exito = agendar_cita(nombre, fecha, motivo, numero, hora)
        if exito:
            return f"✅ ¡Consulta agendada!\n\n*Nombre:* {nombre}\n*Fecha:* {fecha}\n*Hora:* {hora}hs\n*Motivo:* {motivo}"
        else:
            return "❌ Hubo un error al agendar. Intentá de nuevo o contactá a preceptoría directamente."

    # Menú principal
    if msg in ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "inicio", "menu", "menú"]:
        return MENU
    elif msg == "1":
        return "🕐 El horario del colegio es de 7:30 a 13:00hs, de lunes a viernes."
    elif msg == "2":
        return "📋 Para inscripción necesitás:\n- DNI\n- Constancia de estudios\n- Partida de nacimiento\n\nPresentate en secretaría de lunes a viernes de 8:00 a 12:00hs."
    elif msg == "3":
        return "📎 Enviá el documento en formato *PDF* directamente por este chat y lo recibiremos automáticamente. No se aceptan archivos Word ni Excel."
    elif msg == "4":
        conversaciones[numero] = {"paso": "nombre"}
        return "👤 Para agendar una consulta, ¿cuál es tu nombre completo?"
    elif msg == "5":
        return "📞 Podés comunicarte con preceptoría al:\n*Teléfono:* (a completar)\n*Horario:* Lunes a viernes de 7:30 a 13:00hs."
    else:
        return MENU

@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            return "OK", 200

        message = value["messages"][0]
        numero = message["from"]
        tipo = message.get("type")

        if tipo == "text":
            texto = message["text"]["body"]
            respuesta = responder(texto, numero)
            enviar_mensaje(numero, respuesta)

        elif tipo in ["document", "image"]:
            media_id = message[tipo]["id"]
            mime_type = message[tipo].get("mime_type", "application/octet-stream")
            extension = mime_type.split("/")[-1]
            nombre = f"documento_{numero}.{extension}"
            contenido, mime = descargar_media_meta(media_id)
            exito = subir_a_drive(contenido, nombre, mime)
            if exito:
                enviar_mensaje(numero, "✅ ¡Documento recibido y guardado correctamente!")
            else:
                enviar_mensaje(numero, "❌ Hubo un error al guardar el documento. Intentá de nuevo.")

    except Exception as e:
        print(f"Error procesando mensaje: {e}")

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
