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
PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID", "1064536793419567")
META_TOKEN = os.environ.get("META_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "colegio_agrotecnico_bot")

# Estado de conversación
conversaciones = {}

NUMERO_GUARDIA = "+5493467415772"
HORARIO_ATENCION = "lunes a viernes de 10:00 a 20:00hs"

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
    r = requests.post(url, headers=headers, json=data)
    print(f"Respuesta Meta: {r.status_code} - {r.text}")

def descargar_media_meta(media_id):
    url = f"https://graph.facebook.com/v25.0/{media_id}"
    headers = {"Authorization": f"Bearer {META_TOKEN}"}
    response = requests.get(url, headers=headers).json()
    media_url = response.get("url")
    mime_type = response.get("mime_type", "application/octet-stream")
    media_content = requests.get(media_url, headers=headers).content
    return media_content, mime_type

def obtener_o_crear_carpeta(nombre_cliente):
    try:
        service = get_drive_service()
        query = f"name='{nombre_cliente}' and mimeType='application/vnd.google-apps.folder' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(id)").execute()
        carpetas = results.get("files", [])
        if carpetas:
            return carpetas[0]["id"]
        carpeta = service.files().create(body={
            "name": nombre_cliente,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [DRIVE_FOLDER_ID]
        }).execute()
        return carpeta["id"]
    except Exception as e:
        print(f"Error creando carpeta: {e}")
        return DRIVE_FOLDER_ID

def subir_a_drive(contenido, nombre_archivo, mimetype, nombre_cliente):
    try:
        service = get_drive_service()
        carpeta_id = obtener_o_crear_carpeta(nombre_cliente)
        file_stream = io.BytesIO(contenido)
        media = MediaIoBaseUpload(file_stream, mimetype=mimetype)
        file_metadata = {"name": nombre_archivo, "parents": [carpeta_id]}
        service.files().create(body=file_metadata, media_body=media).execute()
        return True
    except Exception as e:
        print(f"Error subiendo a Drive: {e}")
        return False

def agendar_cita(nombre, fecha_str, motivo, numero, hora_str="10:00"):
    try:
        service = get_calendar_service()
        fecha = datetime.strptime(fecha_str, "%d/%m/%Y")
        hora = datetime.strptime(hora_str, "%H:%M").time()
        inicio = fecha.replace(hour=hora.hour, minute=hora.minute)
        fin = inicio + timedelta(minutes=30)
        evento = {
            "summary": f"Consulta Gen Viajero: {nombre}",
            "description": f"Motivo: {motivo}\nWhatsApp: {numero}",
            "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
            "end": {"dateTime": fin.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
        }
        service.events().insert(calendarId="primary", body=evento).execute()
        return True
    except Exception as e:
        print(f"Error agendando cita: {e}")
        return False

MENU = """👋 ¡Hola! Soy *Atlas*, el asistente virtual de *Gen Viajero* 🌍

Soy un bot automatizado. Para hablar con un agente, agendá una consulta o escribinos en horario de atención.

¿En qué puedo ayudarte?

1️⃣ Destinos y servicios
2️⃣ Horarios de atención
3️⃣ Agendar una consulta
4️⃣ Enviar documentación
5️⃣ 🚨 Emergencia durante un viaje

Respondé con el número de tu consulta."""

def responder(mensaje, numero):
    msg = mensaje.strip().lower()
    estado = conversaciones.get(numero, {})

    # Flujo de agendar cita
    if estado.get("paso") == "cita_nombre":
        conversaciones[numero] = {"paso": "cita_telefono", "nombre": mensaje.strip()}
        return "📱 ¿Cuál es tu número de teléfono de contacto?"

    if estado.get("paso") == "cita_telefono":
        conversaciones[numero] = {"paso": "cita_fecha", "nombre": estado["nombre"], "telefono": mensaje.strip()}
        return "📅 ¿Qué fecha preferís para la consulta? Escribila en formato *DD/MM/AAAA*"

    if estado.get("paso") == "cita_fecha":
        try:
            datetime.strptime(mensaje.strip(), "%d/%m/%Y")
            conversaciones[numero] = {"paso": "cita_hora", "nombre": estado["nombre"], "fecha": mensaje.strip()}
            return f"🕐 ¿A qué hora preferís? El horario de atención es de *10:00 a 20:00hs*. Escribila así: *HH:MM* (ejemplo: 14:00)"
        except:
            return "❌ Formato de fecha incorrecto. Escribila así: *DD/MM/AAAA* (ejemplo: 15/06/2026)"

    if estado.get("paso") == "cita_hora":
        try:
            hora = datetime.strptime(mensaje.strip(), "%H:%M").time()
            if hora < datetime.strptime("10:00", "%H:%M").time() or hora > datetime.strptime("20:00", "%H:%M").time():
                return "❌ El horario debe ser entre las *10:00 y las 20:00hs*. Intentá de nuevo."
            conversaciones[numero] = {"paso": "cita_motivo", "nombre": estado["nombre"], "telefono": estado.get("telefono", numero), "fecha": estado["fecha"], "hora": mensaje.strip()}
            return "📝 ¿Cuál es el motivo de la consulta? (ejemplo: viaje a Europa, luna de miel, viaje grupal, etc.)"
        except:
            return "❌ Formato de hora incorrecto. Escribila así: *HH:MM* (ejemplo: 14:00)"

    if estado.get("paso") == "cita_motivo":
        nombre = estado["nombre"]
        fecha = estado["fecha"]
        hora = estado["hora"]
        telefono = estado.get("telefono", numero)
        motivo = mensaje.strip()
        conversaciones.pop(numero, None)
        exito = agendar_cita(nombre, fecha, motivo, telefono, hora)
        if exito:
            return (f"✅ ¡Consulta agendada!\n\n"
                    f"*Nombre:* {nombre}\n"
                    f"*Teléfono:* {telefono}\n"
                    f"*Fecha:* {fecha}\n"
                    f"*Hora:* {hora}hs\n"
                    f"*Motivo:* {motivo}\n\n"
                    f"Un agente de *Gen Viajero* te estará esperando. ¡Hasta pronto! 🌍")
        else:
            return "❌ Hubo un error al agendar. Intentá de nuevo o contactanos directamente."

    # Flujo de documentación
    if estado.get("paso") == "doc_nombre":
        conversaciones[numero] = {"paso": "doc_esperar", "nombre_cliente": mensaje.strip()}
        return f"📎 Perfecto, *{mensaje.strip()}*. Ahora enviá el documento en formato *PDF* y lo guardaremos en tu carpeta."

    # Menú principal
    if msg in ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "inicio", "menu", "menú", "start"]:
        return MENU
    elif msg == "1":
        return ("🌍 *Gen Viajero* te ofrece:\n\n"
                "✈️ Paquetes de viaje nacionales e internacionales\n"
                "🏨 Reservas de hotel\n"
                "🚢 Cruceros\n"
                "💍 Viajes de luna de miel\n"
                "👥 Viajes grupales y corporativos\n"
                "📋 Asesoramiento en visas y documentación\n\n"
                "Para consultar precios y disponibilidad, agendá una consulta con un agente (opción 3️⃣).")
    elif msg == "2":
        return f"🕐 Nuestro horario de atención es de *{HORARIO_ATENCION}*.\n\nFuera de ese horario podés dejarnos tu consulta y te respondemos al día siguiente."
    elif msg == "3":
        conversaciones[numero] = {"paso": "cita_nombre"}
        return "👤 Para agendar una consulta, ¿cuál es tu nombre completo?"
    elif msg == "4":
        conversaciones[numero] = {"paso": "doc_nombre"}
        return "👤 Para organizar tu documentación, ¿cuál es tu nombre y apellido completo?"
    elif msg == "5":
        return (f"🚨 *EMERGENCIA DURANTE UN VIAJE*\n\n"
                f"Comunicáte de inmediato con nuestra línea de guardia:\n\n"
                f"📞 *{NUMERO_GUARDIA}*\n\n"
                f"Disponible las 24hs para asistirte.")
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
    print("=== MENSAJE RECIBIDO ===")
    data = request.get_json()
    print(f"Data: {data}")
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            return "OK", 200

        message = value["messages"][0]
        numero = message["from"]
        tipo = message.get("type")
        estado = conversaciones.get(numero, {})

        if tipo == "text":
            texto = message["text"]["body"]
            respuesta = responder(texto, numero)
            enviar_mensaje(numero, respuesta)

        elif tipo in ["document", "image"]:
            mime_type = message[tipo].get("mime_type", "application/octet-stream")
            extension = mime_type.split("/")[-1]

            if estado.get("paso") == "doc_esperar":
                nombre_cliente = estado["nombre_cliente"]
                media_id = message[tipo]["id"]
                contenido, mime = descargar_media_meta(media_id)
                nombre_archivo = f"{nombre_cliente}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{extension}"
                exito = subir_a_drive(contenido, nombre_archivo, mime, nombre_cliente)
                conversaciones.pop(numero, None)
                if exito:
                    enviar_mensaje(numero, f"✅ Documento guardado correctamente en la carpeta de *{nombre_cliente}*.")
                else:
                    enviar_mensaje(numero, "❌ Hubo un error al guardar el documento. Intentá de nuevo.")
            else:
                enviar_mensaje(numero, "👤 Para enviar documentación usá la opción *4* del menú primero, así lo guardamos en tu carpeta personal.")

    except Exception as e:
        print(f"Error procesando mensaje: {e}")

    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
