"""
Suite de tests automatizados para el chatbot Atlas (Gen Viajero).

Qué hace:
- Prueba responder() directamente para cada paso de cada flujo (menú,
  agendar consulta, documentación, emergencia, cancelar).
- Prueba el endpoint /webhook con un cliente de prueba de Flask
  (verificación de Meta, anti-spam, anti-duplicados, subida de documentos).
- Mockea (reemplaza temporalmente) todas las funciones que llaman a
  Google Calendar / Google Drive y a la API de Meta, para que los tests
  corran rápido, sin internet y sin necesitar credenciales reales.

Cómo correrlos:
    pip install -r requirements-dev.txt
    pytest -v

Si una funcionalidad cambia (un mensaje, una validación, un paso nuevo),
estos tests van a fallar y te van a decir exactamente qué se rompió.
"""

from datetime import datetime, timedelta

import pytest

import chatbot


NUMERO = "5493467000111"


@pytest.fixture(autouse=True)
def limpiar_estado():
    """Resetea todo el estado global del bot antes y después de cada test,
    para que un test no contamine al siguiente."""
    chatbot.conversaciones.clear()
    chatbot.mensajes_recientes.clear()
    chatbot.mensajes_procesados.clear()
    chatbot.eventos_notificados.clear()
    yield
    chatbot.conversaciones.clear()
    chatbot.mensajes_recientes.clear()
    chatbot.mensajes_procesados.clear()
    chatbot.eventos_notificados.clear()


def _proximo_dia_habil():
    dia = datetime.now().date() + timedelta(days=1)
    while dia.weekday() >= 5:
        dia += timedelta(days=1)
    return dia


# ============================================================
# MENÚ PRINCIPAL
# ============================================================

def test_saludo_muestra_menu():
    assert chatbot.responder("hola", NUMERO) == chatbot.MENU


def test_variantes_de_saludo_muestran_menu():
    for saludo in ["buenas", "menu", "menú", "inicio", "start", "buenos dias"]:
        assert chatbot.responder(saludo, NUMERO) == chatbot.MENU


def test_mensaje_no_reconocido_muestra_menu():
    assert chatbot.responder("asdkjaslkdj", NUMERO) == chatbot.MENU


def test_opcion_horarios():
    respuesta = chatbot.responder("2", NUMERO)
    assert "10:00 a 20:00" in respuesta


def test_opcion_emergencia_da_numero_de_guardia():
    respuesta = chatbot.responder("5", NUMERO)
    assert chatbot.NUMERO_GUARDIA in respuesta


# ============================================================
# FLUJO: AGENDAR CONSULTA
# ============================================================

def test_cita_inicia_pidiendo_sucursal():
    respuesta = chatbot.responder("3", NUMERO)
    assert "sucursal" in respuesta.lower()
    assert chatbot.conversaciones[NUMERO]["paso"] == "cita_sucursal"


def test_cita_elige_monte_buey():
    chatbot.responder("3", NUMERO)
    chatbot.responder("1", NUMERO)
    assert chatbot.conversaciones[NUMERO]["sucursal"] == "Monte Buey"
    assert chatbot.conversaciones[NUMERO]["paso"] == "cita_nombre"


def test_cita_elige_justiniano_posse():
    chatbot.responder("3", NUMERO)
    chatbot.responder("2", NUMERO)
    assert chatbot.conversaciones[NUMERO]["sucursal"] == "Justiniano Posse"


def test_cita_sucursal_invalida_resetea_tras_3_intentos():
    chatbot.responder("3", NUMERO)
    chatbot.responder("x", NUMERO)
    chatbot.responder("x", NUMERO)
    respuesta = chatbot.responder("x", NUMERO)
    assert "demasiados intentos" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


def _avanzar_a_telefono(numero=NUMERO):
    chatbot.responder("3", numero)
    chatbot.responder("1", numero)
    chatbot.responder("Juan Perez", numero)


def test_cita_nombre_pide_telefono():
    chatbot.responder("3", NUMERO)
    chatbot.responder("1", NUMERO)
    respuesta = chatbot.responder("Juan Perez", NUMERO)
    assert "whatsapp" in respuesta.lower()
    assert chatbot.conversaciones[NUMERO]["paso"] == "cita_telefono"


def test_cita_telefono_valido_formatea_y_pide_fecha():
    _avanzar_a_telefono()
    respuesta = chatbot.responder("3467123456", NUMERO)
    assert chatbot.conversaciones[NUMERO]["paso"] == "cita_fecha"
    assert chatbot.conversaciones[NUMERO]["telefono"] == "+54 9 3467 12-3456"
    assert "fecha" in respuesta.lower()


def test_cita_telefono_invalido_resetea_tras_3_intentos():
    _avanzar_a_telefono()
    chatbot.responder("123", NUMERO)
    chatbot.responder("abc", NUMERO)
    respuesta = chatbot.responder("12", NUMERO)
    assert "menú" in respuesta.lower() or "demasiados intentos" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


def _avanzar_a_fecha(numero=NUMERO):
    _avanzar_a_telefono(numero)
    chatbot.responder("3467123456", numero)


def test_cita_fecha_pasada_rechazada():
    _avanzar_a_fecha()
    ayer = datetime.now().date() - timedelta(days=1)
    respuesta = chatbot.responder(ayer.strftime("%d/%m/%Y"), NUMERO)
    assert "pasó" in respuesta.lower()


def test_cita_fecha_muy_lejana_rechazada():
    _avanzar_a_fecha()
    lejos = datetime.now().date() + timedelta(days=30)
    respuesta = chatbot.responder(lejos.strftime("%d/%m/%Y"), NUMERO)
    assert "15 días" in respuesta or "15 dias" in respuesta


def test_cita_fecha_fin_de_semana_rechazada():
    _avanzar_a_fecha()
    hoy = datetime.now().date()
    dias_hasta_sabado = (5 - hoy.weekday()) % 7
    sabado = hoy + timedelta(days=dias_hasta_sabado or 7)
    respuesta = chatbot.responder(sabado.strftime("%d/%m/%Y"), NUMERO)
    assert "fin de semana" in respuesta.lower()


def test_cita_fecha_formato_invalido_rechazada():
    _avanzar_a_fecha()
    respuesta = chatbot.responder("32/13/2026", NUMERO)
    assert "no existe" in respuesta.lower()


def test_cita_fecha_valida_pide_hora():
    _avanzar_a_fecha()
    dia = _proximo_dia_habil()
    respuesta = chatbot.responder(dia.strftime("%d/%m/%Y"), NUMERO)
    assert chatbot.conversaciones[NUMERO]["paso"] == "cita_hora"
    assert "hora" in respuesta.lower()


def _avanzar_a_hora(numero=NUMERO):
    _avanzar_a_fecha(numero)
    dia = _proximo_dia_habil()
    chatbot.responder(dia.strftime("%d/%m/%Y"), numero)


def test_cita_hora_fuera_de_rango_rechazada():
    _avanzar_a_hora()
    respuesta = chatbot.responder("22:00", NUMERO)
    assert "10:00" in respuesta and "20:00" in respuesta


def test_cita_hora_minuto_invalido_rechazada():
    _avanzar_a_hora()
    respuesta = chatbot.responder("14:07", NUMERO)
    assert "terminar en 0 o 5" in respuesta


def test_cita_hora_formato_invalido_rechazada():
    _avanzar_a_hora()
    respuesta = chatbot.responder("no es una hora", NUMERO)
    assert "formato de hora incorrecto" in respuesta.lower()


def test_cita_hora_resetea_tras_5_intentos():
    _avanzar_a_hora()
    for _ in range(4):
        chatbot.responder("x", NUMERO)
    respuesta = chatbot.responder("x", NUMERO)
    assert "demasiados intentos" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


def test_cita_hora_no_disponible_sugiere_otros_horarios(monkeypatch):
    _avanzar_a_hora()
    monkeypatch.setattr(
        chatbot, "verificar_disponibilidad",
        lambda f, h: (False, ["15:00", "15:30", "16:00"])
    )
    respuesta = chatbot.responder("14:00", NUMERO)
    assert "ya está reservado" in respuesta
    assert "15:00" in respuesta


def test_cita_hora_disponible_pide_email(monkeypatch):
    _avanzar_a_hora()
    monkeypatch.setattr(chatbot, "verificar_disponibilidad", lambda f, h: (True, []))
    respuesta = chatbot.responder("14:00", NUMERO)
    assert chatbot.conversaciones[NUMERO]["paso"] == "cita_email"
    assert "email" in respuesta.lower()


def _avanzar_a_email(numero=NUMERO, monkeypatch=None):
    _avanzar_a_hora(numero)
    monkeypatch.setattr(chatbot, "verificar_disponibilidad", lambda f, h: (True, []))
    chatbot.responder("14:00", numero)


def test_cita_email_invalido_resetea_tras_3_intentos(monkeypatch):
    _avanzar_a_email(monkeypatch=monkeypatch)
    chatbot.responder("noesemail", NUMERO)
    chatbot.responder("tampoco", NUMERO)
    respuesta = chatbot.responder("nada", NUMERO)
    assert "demasiados intentos" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


def test_cita_email_valido_pide_motivo(monkeypatch):
    _avanzar_a_email(monkeypatch=monkeypatch)
    respuesta = chatbot.responder("juan@gmail.com", NUMERO)
    assert chatbot.conversaciones[NUMERO]["paso"] == "cita_motivo"
    assert "consultar" in respuesta.lower()


def _avanzar_a_motivo(numero=NUMERO, monkeypatch=None):
    _avanzar_a_email(numero, monkeypatch)
    chatbot.responder("juan@gmail.com", numero)


def test_cita_motivo_muestra_resumen_y_pide_confirmacion(monkeypatch):
    _avanzar_a_motivo(monkeypatch=monkeypatch)
    respuesta = chatbot.responder("Viaje a Europa", NUMERO)
    assert chatbot.conversaciones[NUMERO]["paso"] == "cita_confirmar"
    assert "Viaje a Europa" in respuesta
    assert "Confirmás" in respuesta


def _avanzar_a_confirmar(numero=NUMERO, monkeypatch=None):
    _avanzar_a_motivo(numero, monkeypatch)
    chatbot.responder("Viaje a Europa", numero)


def test_cita_confirmar_si_agenda_la_cita(monkeypatch):
    _avanzar_a_confirmar(monkeypatch=monkeypatch)
    monkeypatch.setattr(chatbot, "agendar_cita", lambda *a, **k: True)
    respuesta = chatbot.responder("SI", NUMERO)
    assert "quedó agendada" in respuesta
    assert NUMERO not in chatbot.conversaciones


def test_cita_confirmar_si_pero_falla_el_calendario(monkeypatch):
    _avanzar_a_confirmar(monkeypatch=monkeypatch)
    monkeypatch.setattr(chatbot, "agendar_cita", lambda *a, **k: False)
    respuesta = chatbot.responder("SI", NUMERO)
    assert "error al agendar" in respuesta.lower()


def test_cita_confirmar_no_cancela(monkeypatch):
    _avanzar_a_confirmar(monkeypatch=monkeypatch)
    respuesta = chatbot.responder("NO", NUMERO)
    assert "cancelada" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


# ============================================================
# COMANDO GLOBAL CANCELAR / SALIR
# ============================================================

def test_cancelar_funciona_a_mitad_del_flujo_de_cita():
    chatbot.responder("3", NUMERO)
    chatbot.responder("1", NUMERO)
    respuesta = chatbot.responder("cancelar", NUMERO)
    assert "cancelada" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


def test_salir_funciona_a_mitad_del_flujo_de_documentacion():
    chatbot.responder("4", NUMERO)
    respuesta = chatbot.responder("salir", NUMERO)
    assert "cancelada" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


# ============================================================
# FLUJO: ENVIAR DOCUMENTACIÓN
# ============================================================

def test_doc_inicia_menu():
    respuesta = chatbot.responder("4", NUMERO)
    assert "Subir documentación" in respuesta
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_menu"


def test_doc_subir_con_carpeta_existente_salta_pedido_de_nombre(monkeypatch):
    chatbot.responder("4", NUMERO)
    monkeypatch.setattr(chatbot, "buscar_carpeta_existente", lambda n: "ID_CARPETA_FALSA")
    respuesta = chatbot.responder("1", NUMERO)
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_tipo"
    assert "Pasaporte" in respuesta


def test_doc_subir_sin_carpeta_pide_nombre(monkeypatch):
    chatbot.responder("4", NUMERO)
    monkeypatch.setattr(chatbot, "buscar_carpeta_existente", lambda n: None)
    respuesta = chatbot.responder("1", NUMERO)
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_nombre"
    assert "nombre" in respuesta.lower()


def test_doc_ver_documentos_con_archivos(monkeypatch):
    chatbot.responder("4", NUMERO)
    monkeypatch.setattr(chatbot, "listar_documentos", lambda n: {"Pasaporte": 1, "DNI": 3})
    respuesta = chatbot.responder("2", NUMERO)
    assert "Pasaporte: 1" in respuesta
    assert "DNI: 3" in respuesta


def test_doc_ver_documentos_vacio_ofrece_subir(monkeypatch):
    chatbot.responder("4", NUMERO)
    monkeypatch.setattr(chatbot, "listar_documentos", lambda n: {})
    respuesta = chatbot.responder("2", NUMERO)
    assert "no tenés documentos" in respuesta.lower()
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_menu_vacio"


def test_doc_menu_invalido_resetea_tras_3_intentos():
    chatbot.responder("4", NUMERO)
    chatbot.responder("x", NUMERO)
    chatbot.responder("x", NUMERO)
    respuesta = chatbot.responder("x", NUMERO)
    assert "demasiados intentos" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


def test_doc_menu_vacio_opcion_1_pide_nombre():
    chatbot.conversaciones[NUMERO] = {"paso": "doc_menu_vacio"}
    chatbot.responder("1", NUMERO)
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_nombre"


def test_doc_menu_vacio_opcion_2_vuelve_al_menu():
    chatbot.conversaciones[NUMERO] = {"paso": "doc_menu_vacio"}
    respuesta = chatbot.responder("2", NUMERO)
    assert respuesta == chatbot.MENU
    assert NUMERO not in chatbot.conversaciones


def test_doc_nombre_pide_tipo_de_documento():
    chatbot.conversaciones[NUMERO] = {"paso": "doc_nombre"}
    chatbot.responder("Juan Perez", NUMERO)
    assert chatbot.conversaciones[NUMERO]["nombre_cliente"] == "Juan Perez"
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_tipo"


def test_doc_tipo_valido_pasa_a_esperar_archivo():
    chatbot.conversaciones[NUMERO] = {"paso": "doc_tipo", "nombre_cliente": "Juan"}
    respuesta = chatbot.responder("1", NUMERO)
    assert chatbot.conversaciones[NUMERO]["tipo_doc"] == "Pasaporte"
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_esperar"


def test_doc_tipo_invalido_resetea_tras_3_intentos():
    chatbot.conversaciones[NUMERO] = {"paso": "doc_tipo", "nombre_cliente": "Juan"}
    chatbot.responder("x", NUMERO)
    chatbot.responder("x", NUMERO)
    respuesta = chatbot.responder("x", NUMERO)
    assert "demasiados intentos" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


def test_doc_esperar_con_texto_recuerda_enviar_archivo():
    chatbot.conversaciones[NUMERO] = {"paso": "doc_esperar", "tipo_doc": "Pasaporte"}
    respuesta = chatbot.responder("hola", NUMERO)
    assert "esperando" in respuesta.lower()
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_esperar"


def test_doc_post_opcion_1_vuelve_al_menu():
    chatbot.conversaciones[NUMERO] = {"paso": "doc_post"}
    respuesta = chatbot.responder("1", NUMERO)
    assert respuesta == chatbot.MENU
    assert NUMERO not in chatbot.conversaciones


def test_doc_post_opcion_2_con_carpeta_pide_tipo(monkeypatch):
    chatbot.conversaciones[NUMERO] = {"paso": "doc_post"}
    monkeypatch.setattr(chatbot, "buscar_carpeta_existente", lambda n: "ID")
    respuesta = chatbot.responder("2", NUMERO)
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_tipo"
    assert "Pasaporte" in respuesta


def test_doc_post_opcion_2_sin_carpeta_pide_nombre(monkeypatch):
    chatbot.conversaciones[NUMERO] = {"paso": "doc_post"}
    monkeypatch.setattr(chatbot, "buscar_carpeta_existente", lambda n: None)
    respuesta = chatbot.responder("2", NUMERO)
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_nombre"
    assert "nombre" in respuesta.lower()


def test_doc_post_opcion_3_termina_conversacion():
    chatbot.conversaciones[NUMERO] = {"paso": "doc_post"}
    respuesta = chatbot.responder("3", NUMERO)
    assert "placer ayudarte" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


def test_doc_post_invalido_resetea_tras_3_intentos():
    chatbot.conversaciones[NUMERO] = {"paso": "doc_post"}
    chatbot.responder("x", NUMERO)
    chatbot.responder("x", NUMERO)
    respuesta = chatbot.responder("x", NUMERO)
    assert "demasiados intentos" in respuesta.lower()
    assert NUMERO not in chatbot.conversaciones


# ============================================================
# ANTI-SPAM Y ANTI-DUPLICADOS (funciones usadas por el webhook)
# ============================================================

def test_es_spam_permite_mensajes_normales():
    for _ in range(5):
        assert chatbot.es_spam(NUMERO) is False


def test_es_spam_detecta_exceso_de_mensajes():
    resultados = [chatbot.es_spam(NUMERO) for _ in range(chatbot.LIMITE_MENSAJES + 4)]
    assert resultados[-1] is True
    assert resultados[0] is False


def test_ya_procesado_detecta_duplicados():
    assert chatbot.ya_procesado("abc123") is False
    assert chatbot.ya_procesado("abc123") is True


def test_ya_procesado_ids_distintos_no_son_duplicados():
    assert chatbot.ya_procesado("id1") is False
    assert chatbot.ya_procesado("id2") is False


# ============================================================
# WEBHOOK (endpoint completo de Flask)
# ============================================================

def _payload_texto(numero, texto, msg_id="wamid.TEST1"):
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": numero,
                        "id": msg_id,
                        "type": "text",
                        "text": {"body": texto},
                    }]
                }
            }]
        }]
    }


def _payload_documento(numero, msg_id="wamid.DOC1"):
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": numero,
                        "id": msg_id,
                        "type": "document",
                        "document": {"id": "media123", "mime_type": "application/pdf"},
                    }]
                }
            }]
        }]
    }


def test_webhook_verificacion_con_token_correcto():
    client = chatbot.app.test_client()
    resp = client.get("/webhook", query_string={
        "hub.mode": "subscribe",
        "hub.verify_token": chatbot.VERIFY_TOKEN,
        "hub.challenge": "12345",
    })
    assert resp.status_code == 200
    assert resp.data.decode() == "12345"


def test_webhook_verificacion_con_token_incorrecto():
    client = chatbot.app.test_client()
    resp = client.get("/webhook", query_string={
        "hub.mode": "subscribe",
        "hub.verify_token": "token_falso",
        "hub.challenge": "12345",
    })
    assert resp.status_code == 403


def test_webhook_mensaje_de_texto_responde_por_whatsapp(monkeypatch):
    enviados = []
    monkeypatch.setattr(chatbot, "enviar_mensaje", lambda n, t: enviados.append((n, t)))
    client = chatbot.app.test_client()
    resp = client.post("/webhook", json=_payload_texto(NUMERO, "hola"))
    assert resp.status_code == 200
    assert len(enviados) == 1
    assert enviados[0] == (NUMERO, chatbot.MENU)


def test_webhook_ignora_mensaje_duplicado(monkeypatch):
    enviados = []
    monkeypatch.setattr(chatbot, "enviar_mensaje", lambda n, t: enviados.append((n, t)))
    client = chatbot.app.test_client()
    payload = _payload_texto(NUMERO, "hola", msg_id="wamid.DUP")
    client.post("/webhook", json=payload)
    client.post("/webhook", json=payload)
    assert len(enviados) == 1


def test_webhook_ignora_spam(monkeypatch):
    enviados = []
    monkeypatch.setattr(chatbot, "enviar_mensaje", lambda n, t: enviados.append((n, t)))
    client = chatbot.app.test_client()
    for i in range(chatbot.LIMITE_MENSAJES + 4):
        payload = _payload_texto(NUMERO, "hola", msg_id=f"wamid.SPAM{i}")
        client.post("/webhook", json=payload)
    assert len(enviados) == chatbot.LIMITE_MENSAJES


def test_webhook_documento_sin_haber_pasado_por_opcion_4_avisa(monkeypatch):
    enviados = []
    monkeypatch.setattr(chatbot, "enviar_mensaje", lambda n, t: enviados.append((n, t)))
    client = chatbot.app.test_client()
    resp = client.post("/webhook", json=_payload_documento(NUMERO))
    assert resp.status_code == 200
    assert "opción *4*" in enviados[0][1]


def test_webhook_documento_con_estado_doc_esperar_lo_sube(monkeypatch):
    enviados = []
    monkeypatch.setattr(chatbot, "enviar_mensaje", lambda n, t: enviados.append((n, t)))
    monkeypatch.setattr(chatbot, "descargar_media_meta", lambda mid: (b"contenido falso", "application/pdf"))
    monkeypatch.setattr(chatbot, "subir_a_drive", lambda *a, **k: (True, "Pasaporte"))
    chatbot.conversaciones[NUMERO] = {"paso": "doc_esperar", "tipo_doc": "Pasaporte", "nombre_cliente": "Juan"}
    client = chatbot.app.test_client()
    resp = client.post("/webhook", json=_payload_documento(NUMERO))
    assert resp.status_code == 200
    assert "guardado correctamente" in enviados[0][1]
    assert chatbot.conversaciones[NUMERO]["paso"] == "doc_post"


def test_webhook_documento_falla_al_subir(monkeypatch):
    enviados = []
    monkeypatch.setattr(chatbot, "enviar_mensaje", lambda n, t: enviados.append((n, t)))
    monkeypatch.setattr(chatbot, "descargar_media_meta", lambda mid: (b"contenido falso", "application/pdf"))
    monkeypatch.setattr(chatbot, "subir_a_drive", lambda *a, **k: (False, "Pasaporte"))
    chatbot.conversaciones[NUMERO] = {"paso": "doc_esperar", "tipo_doc": "Pasaporte", "nombre_cliente": "Juan"}
    client = chatbot.app.test_client()
    client.post("/webhook", json=_payload_documento(NUMERO))
    assert "error al guardar" in enviados[0][1].lower()
    assert NUMERO not in chatbot.conversaciones
