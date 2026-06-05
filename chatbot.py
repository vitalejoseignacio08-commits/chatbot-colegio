from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import os
import io
import requests

app = Flask(__name__)

# Google Drive config
DRIVE_FOLDER_ID = "1_o3PbEP9KOaJ4kN-0eu3j3JyKUgG0vSj"

def get_drive_service():
    refresh_token = "1//" + os.environ.get("GOOGLE_REFRESH_TOKEN", "")
    client_id = os.environ.get("GOOGLE_CLIENT_ID_PREFIX", "") + os.environ.get("GOOGLE_CLIENT_ID_SUFFIX", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    print(f"refresh_token: {'OK' if refresh_token else 'MISSING'}")
    print(f"client_id: {'OK' if client_id else 'MISSING'}")
    print(f"client_secret: {'OK' if client_secret else 'MISSING'}")
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token"
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds)

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

MENU = """👋 ¡Hola! Soy el asistente del Colegio Agrotécnico.

¿En qué puedo ayudarte?

1️⃣ Horarios
2️⃣ Inscripción
3️⃣ Subir un documento
4️⃣ Agendar una consulta
5️⃣ Contacto con preceptoría

Respondé con el número de tu consulta."""

def responder(mensaje):
    msg = mensaje.strip().lower()

    if msg in ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "inicio", "menu", "menú"]:
        return MENU
    elif msg == "1":
        return "🕐 El horario del colegio es de 7:30 a 13:00hs, de lunes a viernes."
    elif msg == "2":
        return "📋 Para inscripción necesitás:\n- DNI\n- Constancia de estudios\n- Partida de nacimiento\n\nPresentate en secretaría de lunes a viernes de 8:00 a 12:00hs."
    elif msg == "3":
        return "📎 Enviá el documento directamente por este chat (PDF o imagen) y lo recibiremos automáticamente."
    elif msg == "4":
        return "📅 Para agendar una consulta, decinos tu nombre, curso y el motivo y te contactaremos a la brevedad."
    elif msg == "5":
        return "📞 Podés comunicarte con preceptoría al:\n*Teléfono:* (a completar)\n*Horario:* Lunes a viernes de 7:30 a 13:00hs."
    else:
        return MENU

@app.route("/webhook", methods=["POST"])
def webhook():
    mensaje = request.form.get("Body", "")
    num_media = int(request.form.get("NumMedia", 0))
    respuesta = MessagingResponse()
    msg = respuesta.message()

    if num_media > 0:
        media_url = request.form.get("MediaUrl0")
        media_type = request.form.get("MediaContentType0", "application/octet-stream")
        extension = media_type.split("/")[-1]
        nombre = f"documento_{request.form.get('From', 'desconocido').replace('+', '')}.{extension}"

        exito = subir_a_drive(media_url, nombre, media_type)
        if exito:
            msg.body("✅ ¡Documento recibido y guardado correctamente!")
        else:
            msg.body("❌ Hubo un error al guardar el documento. Intentá de nuevo.")
    else:
        msg.body(responder(mensaje))

    return str(respuesta)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
