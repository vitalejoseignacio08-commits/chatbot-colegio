from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
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

def subir_a_drive(url_archivo, nombre_archivo, mimetype):
    try:
        service = get_drive_service()
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        response = requests.get(url_archivo, auth=(account_sid, auth_token))
        file_stream = io.BytesIO(response.content)
        media = MediaIoBaseUpload(file_stream, mimetype=mimetype)
        file_metadata = {"name": nombre_archivo, "parents": [DRIVE_FOLDER_ID]}
        service.files().create(body=file_metadata, media_body=media).execute()
        return True
    except Exception as e:
        print(f"Error subiendo a Drive: {e}")
        return False

def agendar_cita(nombre, fecha_str, motivo, numero):
    try:
        service = get_calendar_service()
        fecha = datetime.strptime(fecha_str, "%d/%m/%Y")
        inicio = fecha.replace(hour=9, minute=0)
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
            conversaciones[numero] = {"paso": "motivo", "nombre": estado["nombre"], "fecha": mensaje.strip()}
            return "📝 ¿Cuál es el motivo de la consulta?"
        except:
            return "❌ Formato de fecha incorrecto. Escribila así: *DD/MM/AAAA* (ejemplo: 15/06/2026)"

    if estado.get("paso") == "motivo":
        nombre = estado["nombre"]
        fecha = estado["fecha"]
        motivo = mensaje.strip()
        conversaciones.pop(numero, None)
        exito = agendar_cita(nombre, fecha, motivo, numero)
        if exito:
            return f"✅ ¡Consulta agendada!\n\n*Nombre:* {nombre}\n*Fecha:* {fecha}\n*Motivo:* {motivo}\n\nTe esperamos a las 9:00hs."
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

@app.route("/webhook", methods=["POST"])
def webhook():
    mensaje = request.form.get("Body", "")
    numero = request.form.get("From", "")
    num_media = int(request.form.get("NumMedia", 0))
    respuesta = MessagingResponse()
    msg = respuesta.message()

    if num_media > 0:
        media_url = request.form.get("MediaUrl0")
        media_type = request.form.get("MediaContentType0", "application/octet-stream")
        extension = media_type.split("/")[-1]
        nombre = f"documento_{numero.replace('+', '')}.{extension}"
        exito = subir_a_drive(media_url, nombre, media_type)
        if exito:
            msg.body("✅ ¡Documento recibido y guardado correctamente!")
        else:
            msg.body("❌ Hubo un error al guardar el documento. Intentá de nuevo.")
    else:
        msg.body(responder(mensaje, numero))

    return str(respuesta)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
