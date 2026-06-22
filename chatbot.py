from flask import Flask, request, jsonify
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from apscheduler.schedulers.background import BackgroundScheduler
from collections import OrderedDict
import os
import io
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

DRIVE_FOLDER_ID = "1_o3PbEP9KOaJ4kN-0eu3j3JyKUgG0vSj"
SHEET_ID = "1QqcnKBut02VRa-3jBJ45V3NLDhjADSoblA6EUORNhfg"
PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID", "1064536793419567")
META_TOKEN = os.environ.get("META_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "colegio_agrotecnico_bot")
conversaciones = {}
NUMERO_GUARDIA = "+5493467415772"
NUMERO_GUARDIA_MONTE_BUEY = "+5493467415772"
NUMERO_GUARDIA_POSSE = "+5493467434284"
HORARIO_ATENCION = "lunes a viernes de 10:00 a 20:00hs"
eventos_notificados = set()

# Anti-spam: limite de mensajes por usuario
mensajes_recientes = {}
LIMITE_MENSAJES = 8
VENTANA_SEGUNDOS = 10

# Anti-duplicados: evita procesar dos veces el mismo mensaje (reintentos de Meta)
mensajes_procesados = OrderedDict()
LIMITE_IDS_PROCESADOS = 500

# Reservas temporales: bloquea un horario mientras un cliente lo está completando,
# para que dos personas no puedan agendar la misma fecha/hora al mismo tiempo.
reservas_temporales = {}  # numero -> {"fecha": str, "hora": str, "expira": timestamp}
TTL_RESERVA_SEGUNDOS = 600  # 10 minutos

def es_spam(numero):
    ahora = datetime.now().timestamp()
    timestamps = mensajes_recientes.get(numero, [])
    timestamps = [t for t in timestamps if ahora - t < VENTANA_SEGUNDOS]
    timestamps.append(ahora)
    mensajes_recientes[numero] = timestamps
    return len(timestamps) > LIMITE_MENSAJES

def ya_procesado(msg_id):
    if not msg_id:
        return False
    if msg_id in mensajes_procesados:
        return True
    mensajes_procesados[msg_id] = True
    if len(mensajes_procesados) > LIMITE_IDS_PROCESADOS:
        mensajes_procesados.popitem(last=False)
    return False

def _limpiar_reservas_vencidas():
    ahora = datetime.now().timestamp()
    vencidas = [num for num, r in reservas_temporales.items() if r["expira"] < ahora]
    for num in vencidas:
        reservas_temporales.pop(num, None)

def reservar_slot(numero, fecha_str, hora_str):
    """Bloquea fecha_str/hora_str para 'numero' durante TTL_RESERVA_SEGUNDOS."""
    _limpiar_reservas_vencidas()
    reservas_temporales[numero] = {
        "fecha": fecha_str,
        "hora": hora_str,
        "expira": datetime.now().timestamp() + TTL_RESERVA_SEGUNDOS,
    }

def liberar_slot(numero):
    """Libera la reserva temporal de 'numero', si tenía alguna."""
    reservas_temporales.pop(numero, None)

def slot_reservado_por_otro(numero, fecha_str, hora_str):
    """True si otro número (no 'numero') tiene reservada esa fecha/hora en este momento."""
    _limpiar_reservas_vencidas()
    for num, r in reservas_temporales.items():
        if num != numero and r["fecha"] == fecha_str and r["hora"] == hora_str:
            return True
    return False

def get_credentials():
    refresh_token = "1//" + os.environ.get("GOOGLE_REFRESH_TOKEN", "")
    client_id = os.environ.get("GOOGLE_CLIENT_ID_PREFIX", "") + os.environ.get("GOOGLE_CLIENT_ID_SUFFIX", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    creds = Credentials(token=None, refresh_token=refresh_token, client_id=client_id,
                        client_secret=client_secret, token_uri="https://oauth2.googleapis.com/token")
    creds.refresh(Request())
    return creds

def get_drive_service():
    return build("drive", "v3", credentials=get_credentials())

def get_calendar_service():
    return build("calendar", "v3", credentials=get_credentials())

def get_sheets_service():
    return build("sheets", "v4", credentials=get_credentials())

def loguear_consulta(numero, opcion):
    """Agrega una fila al Sheet de métricas: Fecha, Hora, Numero, Opcion elegida."""
    try:
        service = get_sheets_service()
        ahora = datetime.now()
        fila = [[ahora.strftime("%d/%m/%Y"), ahora.strftime("%H:%M"), formatear_numero(numero), opcion]]
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range="A:D",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": fila}
        ).execute()
    except Exception as e:
        print(f"Error logueando consulta: {e}")

def enviar_mensaje(numero, texto):
    url = f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": numero, "type": "text", "text": {"body": texto}}
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
    n = numero.lstrip("+")
    if n.startswith("54") and len(n) == 13:
        area = n[3:7]
        p1 = n[7:9]
        p2 = n[9:]
        return f"+54 9 {area} {p1}-{p2}"
    return numero

def buscar_carpeta_existente(numero_ws):
    """Devuelve el ID de la carpeta si ya existe para este número, o None."""
    try:
        service = get_drive_service()
        numero_fmt = formatear_numero(numero_ws)
        query = f"name contains '{numero_fmt}' and mimeType='application/vnd.google-apps.folder' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        carpetas = results.get("files", [])
        if carpetas:
            return carpetas[0]["id"]
        return None
    except Exception as e:
        print(f"Error buscando carpeta: {e}")
        return None

def obtener_o_crear_carpeta(nombre_cliente, numero_ws):
    try:
        service = get_drive_service()
        numero_fmt = formatear_numero(numero_ws)
        # Buscar por número (único e inmutable), ignorar el nombre
        query = f"name contains '{numero_fmt}' and mimeType='application/vnd.google-apps.folder' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        carpetas = results.get("files", [])
        if carpetas:
            return carpetas[0]["id"], carpetas[0]["name"]
        # Si no existe, crear con nombre + número
        nombre_carpeta = f"{nombre_cliente} ({numero_fmt})"
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
    try:
        service = get_drive_service()
        query = f"name contains '{tipo_doc}' and '{carpeta_id}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(name)").execute()
        count = len(results.get("files", []))
        if count == 0:
            return tipo_doc
        return f"{tipo_doc}{count + 1}"
    except Exception as e:
        print(f"Error contando archivos: {e}")
        return tipo_doc

def listar_documentos(numero_ws):
    try:
        service = get_drive_service()
        numero_fmt = formatear_numero(numero_ws)
        query = f"name contains '{numero_fmt}' and mimeType='application/vnd.google-apps.folder' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        carpetas = results.get("files", [])
        if not carpetas:
            return None
        carpeta_id = carpetas[0]["id"]
        archivos = service.files().list(
            q=f"'{carpeta_id}' in parents and trashed=false",
            fields="files(name)"
        ).execute().get("files", [])
        if not archivos:
            return {}
        conteos = {}
        for archivo in archivos:
            nombre = archivo["name"]
            for tipo in ["Pasaporte", "Visa", "DNI"]:
                if nombre.startswith(tipo):
                    conteos[tipo] = conteos.get(tipo, 0) + 1
                    break
        return conteos
    except Exception as e:
        print(f"Error listando documentos: {e}")
        return None

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
        inicio_dia = fecha.replace(hour=0, minute=0, second=0).isoformat() + "-03:00"
        fin_dia = fecha.replace(hour=23, minute=59, second=59).isoformat() + "-03:00"
        eventos = service.events().list(
            calendarId="primary", timeMin=inicio_dia, timeMax=fin_dia, singleEvents=True
        ).execute().get("items", [])
        for evento in eventos:
            inicio_ev = datetime.fromisoformat(evento["start"].get("dateTime", "").replace("-03:00", ""))
            fin_ev = datetime.fromisoformat(evento["end"].get("dateTime", "").replace("-03:00", ""))
            if inicio_pedido < fin_ev and fin_pedido > inicio_ev:
                proximos = []
                candidato = fin_ev
                resto = candidato.minute % 5
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
                    hora_min = datetime.strptime("10:00", "%H:%M").time()
                    hora_max = datetime.strptime("20:00", "%H:%M").time()
                    if libre and hora_min <= candidato.time() <= hora_max:
                        proximos.append(candidato.strftime("%H:%M"))
                    candidato += timedelta(minutes=5)
                return False, proximos
        return True, []
    except Exception as e:
        print(f"Error verificando disponibilidad: {e}")
        return True, []

def verificar_recordatorios():
    try:
        service = get_calendar_service()
        ahora = datetime.utcnow()
        ventana_inicio = ahora + timedelta(minutes=28)
        ventana_fin = ahora + timedelta(minutes=32)
        print(f"[Scheduler] Corriendo a {ahora.isoformat()}Z — ventana {ventana_inicio.strftime('%H:%M')} a {ventana_fin.strftime('%H:%M')} UTC")
        eventos = service.events().list(
            calendarId="primary",
            timeMin=ventana_inicio.isoformat() + "Z",
            timeMax=ventana_fin.isoformat() + "Z",
            singleEvents=True
        ).execute().get("items", [])
        print(f"[Scheduler] Eventos encontrados: {len(eventos)}")
        for evento in eventos:
            evento_id = evento.get("id")
            if evento_id in eventos_notificados:
                continue
            titulo = evento.get("summary", "Consulta")
            descripcion = evento.get("description", "")
            inicio_str = evento["start"].get("dateTime", "")
            hora_evento = datetime.fromisoformat(inicio_str.replace("-03:00", "")).strftime("%H:%M")
            # Leer sucursal de la descripción
            sucursal = "Monte Buey"
            for linea in descripcion.splitlines():
                if linea.startswith("Sucursal:"):
                    sucursal = linea.replace("Sucursal:", "").strip()
                    break
            numero_destino = NUMERO_GUARDIA_POSSE if "Posse" in sucursal or "posse" in sucursal else NUMERO_GUARDIA_MONTE_BUEY
            nombre_cliente = titulo.replace("Consulta Gen Viajero: ", "")
            mensaje = (f"🔔 *Recordatorio Gen Viajero*\n\n"
                       f"Tenés una consulta en *30 minutos*\n"
                       f"👤 {nombre_cliente}\n"
                       f"🕐 {hora_evento}hs\n"
                       f"📍 {sucursal}")
            enviar_mensaje(numero_destino, mensaje)
            eventos_notificados.add(evento_id)
            print(f"Recordatorio enviado a {numero_destino} para evento {evento_id}")
    except Exception as e:
        print(f"Error en verificar_recordatorios: {e}")

def agendar_cita(nombre, fecha_str, motivo, telefono, sucursal, hora_str="10:00", email=""):
    try:
        service = get_calendar_service()
        fecha = datetime.strptime(fecha_str, "%d/%m/%Y")
        hora = datetime.strptime(hora_str, "%H:%M").time()
        inicio = fecha.replace(hour=hora.hour, minute=hora.minute)
        fin = inicio + timedelta(minutes=30)
        evento = {
            "summary": f"Consulta Gen Viajero: {nombre}",
            "description": f"Motivo: {motivo}\nTelefono: {telefono}\nEmail: {email}\nSucursal: {sucursal}",
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

    if msg in ["cancelar", "salir"] and estado.get("paso"):
        liberar_slot(numero)
        conversaciones.pop(numero, None)
        return "❌ Operación cancelada. Escribí *menú* para empezar de nuevo."

    if estado.get("paso") == "cita_sucursal":
        if mensaje.strip() == "1":
            conversaciones[numero] = {**estado, "paso": "cita_nombre", "sucursal": "Monte Buey"}
            return "👤 ¿Cuál es tu nombre completo?"
        elif mensaje.strip() == "2":
            conversaciones[numero] = {**estado, "paso": "cita_nombre", "sucursal": "Justiniano Posse"}
            return "Cual es tu nombre completo?"
        else:
            intentos = estado.get("intentos_sucursal", 0) + 1
            if intentos >= 3:
                conversaciones.pop(numero, None)
                return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
            conversaciones[numero] = {**estado, "intentos_sucursal": intentos}
            return f"Por favor respondé *1* para Monte Buey o *2* para Justiniano Posse. — Te quedan *{3 - intentos} intentos*."

    if estado.get("paso") == "cita_nombre":
        conversaciones[numero] = {**estado, "paso": "cita_telefono", "nombre": mensaje.strip()}
        return "📱 ¿Cuál es tu número de WhatsApp?\nEscribilo así: *3467123456* (sin el +54 9 adelante)"

    if estado.get("paso") == "cita_telefono":
        tel = mensaje.strip().replace(" ", "").replace("-", "")
        intentos = estado.get("intentos_tel", 0)
        if tel.isdigit() and len(tel) == 10:
            tel_formateado = f"+54 9 {tel[:4]} {tel[4:6]}-{tel[6:]}"
            conversaciones[numero] = {**estado, "paso": "cita_fecha", "telefono": tel_formateado}
            return "Que fecha preferis?\nEscribila asi: *DD/MM/AAAA* - de lunes a viernes, hasta 15 dias adelante."
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
                    return "Demasiados intentos. Escribi *menu* para empezar de nuevo."
                conversaciones[numero] = {**estado, "paso": "cita_fecha", "intentos_fecha": intentos}
                return f"❌ Solo podés agendar hasta 15 días adelante. La fecha máxima es *{fecha_max.strftime('%d/%m/%Y')}* — Te quedan *{3 - intentos} intentos*."
            if fecha.weekday() >= 5:
                intentos += 1
                if intentos >= 3:
                    conversaciones.pop(numero, None)
                    return "Demasiados intentos. Escribi *menu* para empezar de nuevo."
                conversaciones[numero] = {**estado, "paso": "cita_fecha", "intentos_fecha": intentos}
                return f"❌ Esa fecha es fin de semana. Elegí un día de lunes a viernes — Te quedan *{3 - intentos} intentos*."
            conversaciones[numero] = {**estado, "paso": "cita_hora", "fecha": mensaje.strip()}
            return "🕐 ¿A qué hora preferís? El horario es de *10:00 a 20:00hs*. Escribila así: *HH:MM* (ejemplo: 14:00)"
        except ValueError:
            intentos += 1
            if intentos >= 3:
                conversaciones.pop(numero, None)
                return "Demasiados intentos. Escribi *menu* para empezar de nuevo."
            conversaciones[numero] = {**estado, "paso": "cita_fecha", "intentos_fecha": intentos}
            return f"❌ Esa fecha no existe. Escribila así: *DD/MM/AAAA* — Te quedan *{3 - intentos} intentos*."

    if estado.get("paso") == "cita_hora":
        intentos = estado.get("intentos_hora", 0)
        try:
            hora = datetime.strptime(mensaje.strip(), "%H:%M").time()
            if hora < datetime.strptime("10:00", "%H:%M").time() or hora > datetime.strptime("20:00", "%H:%M").time():
                intentos += 1
                if intentos >= 5:
                    conversaciones.pop(numero, None)
                    return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
                conversaciones[numero] = {**estado, "intentos_hora": intentos}
                return "❌ El horario debe ser entre las *10:00 y las 20:00hs*. Intentá de nuevo."
            if hora.minute % 5 != 0:
                intentos += 1
                if intentos >= 5:
                    conversaciones.pop(numero, None)
                    return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
                conversaciones[numero] = {**estado, "intentos_hora": intentos}
                return "❌ El horario debe terminar en 0 o 5. Ejemplo: *10:00*, *10:15*, *10:30*. Intentá de nuevo."
            if slot_reservado_por_otro(numero, estado["fecha"], mensaje.strip()):
                return "❌ Ese horario lo está reservando otra persona en este momento. Probá con otro horario."
            disponible, proximos = verificar_disponibilidad(estado["fecha"], mensaje.strip())
            if not disponible:
                proximos_txt = "\n".join([f"🟢 {h}" for h in proximos])
                return f"❌ Ese horario ya está reservado. Los próximos horarios disponibles son:\n{proximos_txt}\n\nEscribí el horario que preferís."
            reservar_slot(numero, estado["fecha"], mensaje.strip())
            conversaciones[numero] = {**estado, "paso": "cita_email", "hora": mensaje.strip()}
            return "📧 ¿Cuál es tu email?\nEscribilo así: *nombreapellido@gmail.com*"
        except:
            intentos += 1
            if intentos >= 5:
                conversaciones.pop(numero, None)
                return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
            conversaciones[numero] = {**estado, "intentos_hora": intentos}
            return f"❌ Formato de hora incorrecto. Escribila así: *HH:MM* (ejemplo: 14:00) — Te quedan *{5 - intentos} intentos*."

    if estado.get("paso") == "cita_email":
        email = mensaje.strip()
        intentos = estado.get("intentos_email", 0)
        if "@" in email and "." in email.split("@")[-1] and len(email.split("@")) == 2:
            conversaciones[numero] = {**estado, "paso": "cita_motivo", "email": email}
            return "📝 ¿Sobre qué querés consultar? (ejemplo: viaje a Europa, luna de miel, viaje grupal...)"
        else:
            intentos += 1
            if intentos >= 3:
                liberar_slot(numero)
                conversaciones.pop(numero, None)
                return "Demasiados intentos. Escribi *menu* para empezar de nuevo."
            conversaciones[numero] = {**estado, "paso": "cita_email", "intentos_email": intentos}
            return f"❌ Ese email no es válido. Tiene que tener @ y un dominio.\nEjemplo: *nombreapellido@gmail.com* — Te quedan *{3 - intentos} intentos*."

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

    if estado.get("paso") == "cita_confirmar":
        if mensaje.strip().lower() in ["si", "s"]:
            nombre = estado["nombre"]
            fecha = estado["fecha"]
            hora = estado["hora"]
            telefono = estado.get("telefono", numero)
            sucursal = estado.get("sucursal", "Monte Buey")
            email = estado.get("email", "")
            motivo = estado.get("motivo", "")
            # Re-chequeo final: por si el horario se ocupó mientras completaba los datos
            disponible, proximos = verificar_disponibilidad(fecha, hora)
            if not disponible:
                liberar_slot(numero)
                conversaciones.pop(numero, None)
                proximos_txt = "\n".join([f"🟢 {h}" for h in proximos])
                return (f"❌ Justo se ocupó ese horario mientras completabas los datos.\n\n"
                        f"Horarios cercanos disponibles ese día:\n{proximos_txt}\n\n"
                        f"Escribí *menú* para volver a agendar.")
            exito = agendar_cita(nombre, fecha, motivo, telefono, sucursal, hora, email)
            liberar_slot(numero)
            conversaciones.pop(numero, None)
            if exito:
                return (f"✅ ¡Listo! Tu consulta quedó agendada 🎉\n\n"
                        f"Nombre: {nombre}\n"
                        f"Telefono: {telefono}\n"
                        f"Email: {email}\n"
                        f"Sucursal: {sucursal}\n"
                        f"Fecha: {fecha}\n"
                        f"Hora: {hora}hs\n"
                        f"Motivo: {motivo}\n\n"
                        f"¡Un agente de *Gen Viajero* te va a estar esperando! 🌍")
            else:
                return "❌ Hubo un error al agendar. Intentá de nuevo o contactanos directamente."
        else:
            liberar_slot(numero)
            conversaciones.pop(numero, None)
            return "❌ Consulta cancelada. Escribí *menú* para empezar de nuevo."

    if estado.get("paso") == "doc_menu":
        if mensaje.strip() == "1":
            carpeta_id = buscar_carpeta_existente(numero)
            if carpeta_id:
                conversaciones[numero] = {**estado, "paso": "doc_tipo", "nombre_cliente": ""}
                return ("📄 ¿Qué tipo de documento vas a enviar?\n\n"
                        "1️⃣ Pasaporte\n"
                        "2️⃣ Visa\n"
                        "3️⃣ DNI")
            else:
                conversaciones[numero] = {**estado, "paso": "doc_nombre"}
                return "👤 ¿Cuál es tu nombre y apellido completo?"
        elif mensaje.strip() == "2":
            conteos = listar_documentos(numero)
            conversaciones.pop(numero, None)
            if conteos is None or conteos == {}:
                conversaciones[numero] = {"paso": "doc_menu_vacio"}
                return ("📂 Todavía no tenés documentos guardados.\n\n"
                        "¿Querés subir uno ahora?\n\n"
                        "1️⃣ Sí, subir documentación\n"
                        "2️⃣ No, volver al menú")
            emojis = {"Pasaporte": "🛂", "Visa": "✈️", "DNI": "🪪"}
            lineas = [f"{emojis.get(t, '📄')} {t}: {c}" for t, c in conteos.items()]
            return "📂 *Tu documentación guardada:*\n\n" + "\n".join(lineas)
        else:
            intentos = estado.get("intentos_doc_menu", 0) + 1
            if intentos >= 3:
                conversaciones.pop(numero, None)
                return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
            conversaciones[numero] = {**estado, "intentos_doc_menu": intentos}
            return f"Por favor respondé *1* para subir o *2* para ver tus documentos. — Te quedan *{3 - intentos} intentos*."

    if estado.get("paso") == "doc_menu_vacio":
        if mensaje.strip() == "1":
            conversaciones[numero] = {"paso": "doc_nombre"}
            return "Cual es tu nombre y apellido completo?"
        else:
            conversaciones.pop(numero, None)
            return MENU

    if estado.get("paso") == "doc_nombre":
        conversaciones[numero] = {**estado, "paso": "doc_tipo", "nombre_cliente": mensaje.strip()}
        return ("📄 ¿Qué tipo de documento vas a enviar?\n\n"
                "1️⃣ Pasaporte\n"
                "2️⃣ Visa\n"
                "3️⃣ DNI")

    if estado.get("paso") == "doc_tipo":
        tipos = {"1": "Pasaporte", "2": "Visa", "3": "DNI"}
        tipo = tipos.get(mensaje.strip())
        if not tipo:
            intentos = estado.get("intentos_doc_tipo", 0) + 1
            if intentos >= 3:
                conversaciones.pop(numero, None)
                return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
            conversaciones[numero] = {**estado, "intentos_doc_tipo": intentos}
            return f"Por favor respondé *1* para Pasaporte, *2* para Visa o *3* para DNI. — Te quedan *{3 - intentos} intentos*."
        conversaciones[numero] = {**estado, "paso": "doc_esperar", "tipo_doc": tipo}
        return f"📎 Perfecto. Ahora enviá tu *{tipo}* y lo guardamos en tu carpeta personal 🔒"

    if estado.get("paso") == "doc_esperar":
        return "📎 Estoy esperando que envíes tu documento (foto o archivo). Si querés cancelar, escribí *cancelar*."

    if estado.get("paso") == "doc_post":
        if mensaje.strip() == "1":
            conversaciones.pop(numero, None)
            return MENU
        elif mensaje.strip() == "2":
            carpeta_id = buscar_carpeta_existente(numero)
            if carpeta_id:
                conversaciones[numero] = {**estado, "paso": "doc_tipo", "nombre_cliente": ""}
            else:
                conversaciones[numero] = {**estado, "paso": "doc_nombre"}
                return "👤 ¿Cuál es tu nombre y apellido completo?"
            return ("📄 ¿Qué tipo de documento vas a enviar?\n\n"
                    "1️⃣ Pasaporte\n"
                    "2️⃣ Visa\n"
                    "3️⃣ DNI")
        elif mensaje.strip() == "3":
            conversaciones.pop(numero, None)
            return ("¡Fue un placer ayudarte! 😊✈️\n\n"
                    "Recordá que *Atlas* está disponible las 24hs para lo que necesites.\n"
                    "¡Hasta la próxima aventura! 🌍")
        else:
            intentos = estado.get("intentos_doc_post", 0) + 1
            if intentos >= 3:
                conversaciones.pop(numero, None)
                return "❌ Demasiados intentos. Escribí *menú* para empezar de nuevo."
            conversaciones[numero] = {**estado, "intentos_doc_post": intentos}
            return f"Por favor respondé *1*, *2* o *3*. — Te quedan *{3 - intentos} intentos*."

    if msg in ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "inicio", "menu", "menú", "start"]:
        return MENU
    elif msg == "2":
        loguear_consulta(numero, "Horarios de atención")
        return f"🕐 ¡Buena pregunta! Estamos disponibles de *{HORARIO_ATENCION}* en nuestras dos sucursales 📍"
    elif msg == "3":
        loguear_consulta(numero, "Agendar una consulta")
        conversaciones[numero] = {"paso": "cita_sucursal"}
        return ("¡Genial, vamos a planear algo juntos! 🗓️✨\n\n"
                "Primero, ¿en qué sucursal preferís atenderte?\n\n"
                "1️⃣ 📍 Monte Buey\n"
                "2️⃣ 📍 Justiniano Posse")
    elif msg == "4":
        loguear_consulta(numero, "Enviar documentación")
        conversaciones[numero] = {"paso": "doc_menu"}
        return ("📁 ¿Qué querés hacer?\n\n"
                "1️⃣ Subir documentación\n"
                "2️⃣ Ver mis documentos guardados")
    elif msg == "5":
        loguear_consulta(numero, "Emergencia durante un viaje")
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
        msg_id = message.get("id")

        if ya_procesado(msg_id):
            print(f"Mensaje duplicado ignorado: {msg_id}")
            return "OK", 200

        if es_spam(numero):
            print(f"Spam detectado, ignorando mensaje de {numero}")
            return "OK", 200

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
                if exito:
                    conversaciones[numero] = {**estado, "paso": "doc_post"}
                    enviar_mensaje(numero, (f"✅ *{nombre_guardado}* guardado correctamente en tu carpeta personal 🔒\n\n"
                                           f"¿Qué querés hacer ahora?\n\n"
                                           f"1️⃣ Volver al menú principal\n"
                                           f"2️⃣ Enviar otro documento\n"
                                           f"3️⃣ Terminar conversación"))
                else:
                    conversaciones.pop(numero, None)
                    enviar_mensaje(numero, "❌ Hubo un error al guardar el documento. Intentá de nuevo.")
            else:
                enviar_mensaje(numero, "👤 Para enviar documentación usá la opción *4* del menú primero, así lo guardamos en tu carpeta personal.")
    except Exception as e:
        print(f"Error procesando mensaje: {e}")
    return "OK", 200

import atexit
scheduler = BackgroundScheduler()
scheduler.add_job(verificar_recordatorios, "interval", minutes=5)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
