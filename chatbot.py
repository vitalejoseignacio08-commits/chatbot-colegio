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

def formatear_numero(numero):
    # numero llega como "5493467123456" → "+54 9 3467 12-3456"
    n = numero.lstrip("+")
    if n.startswith("54") and len(n) == 13:
        area = n[3:7]
        p1 = n[7:9]
        p2 = n[9:]
        return f"+54 9 {area} {p1}-{p2}"
    return numero

def obtener_o_crear_carpeta(nombre_cliente, numero_ws):
    try:
        service = get_drive_service()
        numero_fmt = formatear_numero(numero_ws)
        nombre_carpeta = f"{nombre_cliente} ({numero_fmt})"
        # Buscar por número exacto en el nombre (único e inmutable)
        query = f"name='{nombre_carpeta}' and mimeType='application/vnd.google-apps.folder' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(id)").execute()
        carpetas = results.get("files", [])
        if carpetas:
            return carpetas[0]["id"], nombre_carpeta
        carpeta = service.files().create(body={
            "name": nombre_carpeta,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [DRIVE_FOLDER_ID]
        }).execute()
        return carpeta["id"], nombre_carpeta
    except Exception as e:
        print(f"Error creando carpeta: {e}")
        return DRIVE_FOLDER_ID, nombre_cliente

def obtener_nombre_archivo(carpeta_id, tipo_doc):
    # Cuenta cuántos archivos del mismo tipo ya existen en la carpeta
    try:
        service = get_drive_service()
        query = f"name contains '{tipo_doc}' and '{carpeta_id}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(name)").execute()
        existentes = results.get("files", [])
        count = len(existentes)
        if count == 0:
            return tipo_doc
        return f"{tipo_doc}{count + 1}"
    except Exception as e:
        print(f"Error contando archivos: {e}")
        return tipo_doc

def subir_a_drive(contenido, tipo_doc, mimetype, nombre_cliente, numero_ws):
    try:
        service = get_drive_service()
        carpeta_id, _ = obtener_o_crear_carpeta(nombre_cliente, numero_ws)
        nombre_archivo = obtener_nombre_archivo(carpeta_id, tipo_doc)
        extension = mimetype.split("/")[-1]
        nombre_final = f"{nombre_archivo}.{extension}"
        file_stream = io.BytesIO(contenido)
        media = MediaIoBaseUpload(file_stream, mimetype=mimetype)
        file_metadata = {"name": nombre_final, "parents": [carpeta_id]}
        service.files().create(body=file_metadata, media_body=media).execute()
        return True, nombre_archivo
    except Exception as e:
        print(f"Error subiendo a Drive: {e}")
        return False, tipo_doc

def verificar_disponibilidad(fecha_str, hora_str):
    try:
        service = get_calendar_service()
        fecha = datetime.strptime(fecha_str, "%d/%m/%Y")
        hora = datetime.strptime(hora_str, "%H:%M").time()
        inicio_pedido = datetime.combine(fecha, hora)
        fin_pedido = inicio_pedido + timedelta(minutes=30)

        # Buscar eventos del día
        inicio_dia = fecha.replace(hour=0, minute=0, second=0).isoformat() + "-03:00"
        fin_dia = fecha.replace(hour=23, minute=59, second=59).isoformat() + "-03:00"
        eventos = service.events().list(
            calendarId="primary",
            timeMin=inicio_dia,
            timeMax=fin_dia,
            singleEvents=True
        ).execute().get("items", [])

        # Verificar si el horario pedido choca con algún evento
        for evento in eventos:
            inicio_ev = datetime.fromisoformat(evento["start"].get("dateTime", "").replace("-03:00", ""))
            fin_ev = datetime.fromisoformat(evento["end"].get("dateTime", "").replace("-03:00", ""))
            if inicio_pedido < fin_ev and fin_pedido > inicio_ev:
                # Buscar próximos horarios libres
                proximos = []
                candidato = fin_ev
                # Redondear al próximo múltiplo de 5
                minutos = candidato.minute
                resto = minutos % 5
                if resto != 0:
                    candidato = candidato + timedelta(minutes=(5 - resto))
                candidato = candidato.replace(second=0, microsecond=0)
                while len(proximos) < 3:
                    fin_candidato = candidato + timedelta(minutes=30)
                    libre = True
                    for ev2 in eventos:
                        ini2 = datetime.fromisoformat(ev2["start"].get("dateTime", "").replace("-03:00", ""))
                        fin2 = datetime.fromisoformat(ev2["end"].get("dateTime", "").replace("-03:00", ""))
                        if candidato < fin2 and fin_candidato > ini2:
                            libre = False
                            break
                    if libre and candidato.time() >= datetime.strptime("10:00", "%H:%M").time() and candidato.time() <= datetime.strptime("20:00", "%H:%M").time():
                        proximos.append(candidato.strftime("%H:%M"))
                    candidato += timedelta(minutes=5)
                return False, proximos
        return True, []
    except Exception as e:
        print(f"Error verificando disponibilidad: {e}")
        return True, []

def agendar_cita(nombre, fecha_str, motivo, telefono, sucursal, hora_str="10:00", email=""):
    try:
        service = get_calendar_service()
        fecha = datetime.strptime(fecha_str, "%d/%m/%Y")
        hora = datetime.strptime(hora_str, "%H:%M").time()
        inicio = fecha.replace(hour=hora.hour, minute=hora.minute)
        fin = inicio + timedelta(minutes=30)
        evento = {
            "summary": f"Consulta Gen Viajero: {nombre}",
            "description": f"Motivo: {motivo}\nTeléfono: {telefono}\nEmail: {email}\nSucursal: {sucursal}",
            "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
            "end": {"dateTime": fin.isoformat(), "timeZone": "America/Argentina/Buenos_Aires"},
        }
        service.events().insert(calendarId="primary", body=evento).execute()
        return True
    except Exception as e:
        print(f"Error agendando cita: {e}")
        return False

MENU = """¡Hola! 👋✈️ Soy *Atlas*, el asistente virtual de *Gen Viajero* 🌍

Estoy acá para ayudarte a planear tu próxima aventura. ¿En qué puedo ayudarte?

2️⃣ 🕐 Horarios de atención
3️⃣ 📅 Agendar una consulta
4️⃣ 📁 Enviar documentación
5️⃣ 🚨 Emergencia durante un viaje

*Respondé con el número de tu consulta.*
_(Soy un bot — para hablar con una persona, agendá una consulta 😊)_"""

def responder(mensaje, numero):
    msg = mensaje.strip().lower()
    estado = conversaciones.get(numero, {})

    # Flujo de agendar cita
    if estado.get("paso") == "cita_sucursal":
        if mensaje.strip() == "1":
            conversaciones[numero] = {**estado, "paso": "cita_nombre", "sucursal": "Monte Buey"}
            return "👤 ¿Cuál es tu nombre completo?"
        elif mensaje.strip() == "2":
            conversaciones[numero] = {**estado, "paso": "cita_nombre", "sucursal": "Justiniano Posse"}
            return "👤 ¿Cuál es tu nombre completo?"
        else:
            return "Por favor respondé *1* para Monte Buey o *2* para Justiniano Posse."

    if estado.get("paso") == "cita_nombre":
        conversaciones[numero] = {**estado, "paso": "cita_telefono", "nombre": mensaje.strip()}
        return "📱 ¿Cuál es tu número de WhatsApp?\nEscribilo así: *3467123456* (sin el +54 9 adelante)"

    if estado.get("paso") == "cita_telefono":
        tel = mensaje.strip().replace(" ", "").replace("-", "")
        intentos = estado.get("intentos_tel", 0)
        if tel.isdigit() and len(tel) == 10:
            tel_formateado = f"+54 9 {tel[:4]} {tel[4:6]}-{tel[6:]}"
            conversaciones[numero] = {**estado, "paso": "cita_fecha", "telefono": tel_formateado}
            return "📅 ¿Qué fecha preferís?\nEscribila así: *DD/MM/AAAA* — de lunes a viernes, hasta 15 días adelante."
        else:
            intentos += 1
            if intentos >= 3:
                conversaciones.pop(numero, None)
                return "❌ No pudimos registrar tu número. Escribí *menú* para empezar de nuevo o contactanos directamente."
            conversaciones[numero] = {**estado, "paso": "cita_telefono", "intentos_tel": intentos}
            return f"❌ Ese número no es válido. Recordá: sin el +54 9, solo los 10 números.\nEjemplo: *3467123456* — Te quedan *{3 - intentos} intentos*."

    if estado.get("paso") == "cita_fecha":
        intentos = estado.get("intentos_fecha", 0)
        hoy = datetime.now().date()
        fecha_max = hoy + timedelta(days=15)
        try:
            fecha = datetime.strptime(mensaje.strip(), "%d/%m/%Y").date()
            if fecha < hoy:
                intentos += 1
                if intentos >= 3:
                    conversaciones.pop(numero, None)
                    return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
                conversaciones[numero] = {**estado, "paso": "cita_fecha", "intentos_fecha": intentos}
                return f"❌ Esa fecha ya pasó. Elegí una fecha desde hoy hasta el *{fecha_max.strftime('%d/%m/%Y')}* — Te quedan *{3 - intentos} intentos*."
            if fecha > fecha_max:
                intentos += 1
                if intentos >= 3:
                    conversaciones.pop(numero, None)
                    return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
                conversaciones[numero] = {**estado, "paso": "cita_fecha", "intentos_fecha": intentos}
                return f"❌ Solo podés agendar hasta 15 días adelante. La fecha máxima es *{fecha_max.strftime('%d/%m/%Y')}* — Te quedan *{3 - intentos} intentos*."
            if fecha.weekday() >= 5:
                intentos += 1
                if intentos >= 3:
                    conversaciones.pop(numero, None)
                    return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
                conversaciones[numero] = {**estado, "paso": "cita_fecha", "intentos_fecha": intentos}
                return f"❌ Esa fecha es fin de semana. Elegí un día de lunes a viernes — Te quedan *{3 - intentos} intentos*."
            conversaciones[numero] = {**estado, "paso": "cita_hora", "fecha": mensaje.strip()}
            return "🕐 ¿A qué hora preferís? El horario es de *10:00 a 20:00hs*. Escribila así: *HH:MM* (ejemplo: 14:00)"
        except ValueError:
            intentos += 1
            if intentos >= 3:
                conversaciones.pop(numero, None)
                return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
            conversaciones[numero] = {**estado, "paso": "cita_fecha", "intentos_fecha": intentos}
            return f"❌ Esa fecha no existe. Escribila así: *DD/MM/AAAA* — Ejemplo: *15/06/XXXX* — Te quedan *{3 - intentos} intentos*."

    if estado.get("paso") == "cita_hora":
        try:
            hora = datetime.strptime(mensaje.strip(), "%H:%M").time()
            if hora < datetime.strptime("10:00", "%H:%M").time() or hora > datetime.strptime("20:00", "%H:%M").time():
                return "❌ El horario debe ser entre las *10:00 y las 20:00hs*. Intentá de nuevo."
            if hora.minute % 5 != 0:
                return "❌ El horario debe terminar en 0 o 5. Ejemplo: *10:00*, *10:15*, *10:30*. Intentá de nuevo."
            # Verificar disponibilidad en Google Calendar
            fecha_str = estado["fecha"]
            disponible, proximos = verificar_disponibilidad(fecha_str, mensaje.strip())
            if not disponible:
                proximos_txt = "\n".join([f"🟢 {h}" for h in proximos])
                return f"❌ Ese horario ya está reservado. Los próximos horarios disponibles son:\n{proximos_txt}\n\nEscribí el horario que preferís."
            conversaciones[numero] = {**estado, "paso": "cita_email", "hora": mensaje.strip()}
            return "📧 ¿Cuál es tu email?\nEscribilo así: *nombreapellido@gmail.com*"
        except:
            return "❌ Formato de hora incorrecto. Escribila así: *HH:MM* (ejemplo: 14:00)"

    if estado.get("paso") == "cita_email":
        email = mensaje.strip()
        intentos = estado.get("intentos_email", 0)
        if "@" in email and "." in email.split("@")[-1] and len(email.split("@")) == 2:
            conversaciones[numero] = {**estado, "paso": "cita_motivo", "email": email}
            return "📝 ¿Sobre qué querés consultar? (ejemplo: viaje a Europa, luna de miel, viaje grupal...)"
        else:
            intentos += 1
            if intentos >= 3:
                conversaciones.pop(numero, None)
                return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
            conversaciones[numero] = {**estado, "paso": "cita_email", "intentos_email": intentos}
            return f"❌ Ese email no es válido. Tiene que tener @ y un dominio.\nEjemplo: *nombreapellido@gmail.com* — Te quedan *{3 - intentos} intentos*."

    if estado.get("paso") == "cita_confirmar":
        if mensaje.strip().lower() in ["si", "sí", "s"]:
            nombre = estado["nombre"]
            fecha = estado["fecha"]
            hora = estado["hora"]
            telefono = estado.get("telefono", numero)
            sucursal = estado.get("sucursal", "Monte Buey")
            email = estado.get("email", "")
            motivo = estado.get("motivo", "")
            conversaciones.pop(numero, None)
            exito = agendar_cita(nombre, fecha, motivo, telefono, sucursal, hora, email)
            if exito:
                return (f"✅ ¡Listo! Tu consulta quedó agendada 🎉\n\n"
                        f"👤 *Nombre:* {nombre}\n"
                        f"📱 *Teléfono:* {telefono}\n"
                        f"📧 *Email:* {email}\n"
                        f"📍 *Sucursal:* {sucursal}\n"
                        f"📅 *Fecha:* {fecha}\n"
                        f"🕐 *Hora:* {hora}hs\n"
                        f"📝 *Motivo:* {motivo}\n\n"
                        f"¡Un agente de *Gen Viajero* te va a estar esperando! 🌍")
            else:
                return "❌ Hubo un error al agendar. Intentá de nuevo o contactanos directamente."
        else:
            conversaciones.pop(numero, None)
            return "❌ Consulta cancelada. Escribí *menú* para empezar de nuevo."

    if estado.get("paso") == "cita_motivo":
        nombre = estado["nombre"]
        fecha = estado["fecha"]
        hora = estado["hora"]
        telefono = estado.get("telefono", numero)
        sucursal = estado.get("sucursal", "Monte Buey")
        email = estado.get("email", "")
        motivo = mensaje.strip()
        conversaciones[numero] = {**estado, "paso": "cita_confirmar", "motivo": motivo}
        return (f"📋 *Revisá tu consulta antes de confirmar:*\n\n"
                f"👤 *Nombre:* {nombre}\n"
                f"📱 *Teléfono:* {telefono}\n"
                f"📧 *Email:* {email}\n"
                f"📍 *Sucursal:* {sucursal}\n"
                f"📅 *Fecha:* {fecha}\n"
                f"🕐 *Hora:* {hora}hs\n"
                f"📝 *Motivo:* {motivo}\n\n"
                f"¿Confirmás? Respondé *SI* o *NO*")
    # Flujo de documentación
    if estado.get("paso") == "doc_nombre":
        conversaciones[numero] = {**estado, "paso": "doc_tipo", "nombre_cliente": mensaje.strip()}
        return (f"📄 ¿Qué tipo de documento vas a enviar?\n\n"
                f"1️⃣ Pasaporte\n"
                f"2️⃣ Visa\n"
                f"3️⃣ DNI")

    if estado.get("paso") == "doc_tipo":
        tipos = {"1": "Pasaporte", "2": "Visa", "3": "DNI"}
        tipo = tipos.get(mensaje.strip())
        if not tipo:
            return "Por favor respondé *1* para Pasaporte, *2* para Visa o *3* para DNI."
        conversaciones[numero] = {**estado, "paso": "doc_esperar", "tipo_doc": tipo}
        return f"📎 Perfecto. Ahora enviá tu *{tipo}* y lo guardamos en tu carpeta personal 🔒"

    # Menú principal
    if msg in ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "inicio", "menu", "menú", "start"]:
        return MENU
    elif msg == "2":
        return f"🕐 ¡Buena pregunta! Estamos disponibles de *{HORARIO_ATENCION}* en nuestras dos sucursales 📍"
    elif msg == "3":
        conversaciones[numero] = {"paso": "cita_sucursal"}
        return ("¡Genial, vamos a planear algo juntos! 🗓️✨\n\n"
                "Primero, ¿en qué sucursal preferís atenderte?\n\n"
                "1️⃣ 📍 Monte Buey\n"
                "2️⃣ 📍 Justiniano Posse")
    elif msg == "4":
        conversaciones[numero] = {"paso": "doc_nombre"}
        return "📁 ¡Claro! Para guardar tu documentación de forma segura, ¿cuál es tu nombre y apellido completo? 👤"
    elif msg == "5":
        return (f"🚨 *¿Estás en una emergencia durante tu viaje?*\n\n"
                f"Contactá ahora a nuestra línea de guardia:\n"
                f"📞 *{NUMERO_GUARDIA}*\n\n"
                f"¡Estamos para vos las 24hs! 💪")
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

            if estado.get("paso") == "doc_esperar":
                nombre_cliente = estado["nombre_cliente"]
                tipo_doc = estado.get("tipo_doc", "Documento")
                media_id = message[tipo]["id"]
                contenido, mime = descargar_media_meta(media_id)
                exito, nombre_guardado = subir_a_drive(contenido, tipo_doc, mime, nombre_cliente, numero)
                conversaciones.pop(numero, None)
                if exito:
                    enviar_mensaje(numero, f"✅ *{nombre_guardado}* guardado correctamente en tu carpeta personal 🔒")
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
