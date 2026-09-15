-- ============================================================================
-- AMC.Py.Agent.EdiAuto — Procedimientos de resolución de incidencias EDI
-- Script: 003_seed_procedures.sql
--
-- Procedimientos extraídos de la documentación técnica del equipo EDI AMC.
-- Nombres de cliente y de partner anonimizados para el repositorio público
-- (NORDCART = cliente ficticio, PTX = consola/partner ficticio).
-- PROC 2: Corrección precio unitario facturas PTX → NORDCART   (INVOIC / wrong_data)
-- PROC 3: Corrección error "Not serial number available"   (DESADV / system_error)
-- PROC 4: Resolución "No existe punto operacional origen"  (any   / system_error)
-- ============================================================================

-- ── PROC 2: Corrección precio unitario facturas PTX → NORDCART ───────────────
INSERT INTO edi_resolution_procedures (
    procedure_name, doc_type, problem_type, description, steps
) VALUES (
    'Corrección precio unitario facturas PTX → NORDCART',
    'INVOIC',
    'wrong_data',
    'NORDCART notifica que el precio total (MOA+203) del INVOIC recibido no coincide con el resultado de multiplicar '
    'el precio unitario indicado (PRI+AAA) por la cantidad solicitada indicada (QTY+47). Se localiza el fichero '
    'de factura en el sistema de ficheros, se calcula el precio unitario correcto (con los decimales necesarios) '
    'y se corrige el contenido del fichero con dicho precio unitario para reprocesarlo. '
    'IMPORTANTE: el informe de rechazo de NORDCART (Compliance Check Report) incluye siempre el texto estándar de '
    'plantilla ''ATTENTION! THE COMPLETE FILE HAS BEEN REJECTED! ... All invoices in this interchange file have '
    'been rejected'', incluso cuando el informe solo lista un único ''Document no.'' con un único segmento de '
    'error. Esa frase es una advertencia genérica sobre el intercambio EDI en su conjunto, no una indicación de '
    'que haya más de una factura afectada: no asumir que hay varias facturas salvo que el informe liste '
    'explícitamente más de un ''Document no.''.',
    '[
        {
            "order": 1,
            "action": "Extraer del documento adjunto del correo el número de documento y la fecha/hora de referencia junto a ''from''",
            "notes": "Datos necesarios para localizar el fichero correcto en los pasos siguientes. No requiere ninguna herramienta, es información ya presente en el correo/adjunto de la incidencia."
        },
        {
            "order": 2,
            "action": "Listar los ficheros IDoc de facturas de salida de PTX ya procesados por Edicom, filtrando por fecha de modificación en un rango de +/-30 minutos respecto a la fecha/hora de referencia obtenida",
            "tool": "filesystem.list_files",
            "notes": "Ruta: \\\\fileserver.example\\EDI$\\PROD\\EDI\\PTX\\Edicom\\INVOICE\\PRO. Admite filtrar por fecha de modificación mediante los parámetros modifiedAfter/modifiedBefore (ver filesystem.list_files, ampliado junto con ListarFicherosResponse.LastWriteTime en SysFileHub)."
        },
        {
            "order": 3,
            "action": "Leer el contenido de los ficheros candidatos y localizar el que contiene el número de documento indicado en el correo",
            "tool": "filesystem.read_file",
            "notes": "Comparar el número de documento del correo contra el contenido de cada fichero candidato hasta encontrar la coincidencia."
        },
        {
            "order": 4,
            "action": "Calcular el precio unitario correcto y corregir el fichero INVOIC con dicho valor",
            "tool": "filesystem.write_file",
            "notes": "El precio unitario correcto se calcula dividiendo el precio total (segmento E2EDP26, con QUALF 004) entre la cantidad solicitada (segmento E2EDP01008, campo MENGE). Sustituir ese valor en el segmento E2EDP26 con calificador 001, con los decimales necesarios. Escribir en la misma ruta/fichero."
        },
        {
            "order": 5,
            "action": "Mover el fichero corregido de la carpeta de procesados a la carpeta de pendientes para que Edicom lo reprocese",
            "tool": "filesystem.move_file",
            "notes": "Ruta origen: \\\\fileserver.example\\EDI$\\PROD\\EDI\\PTX\\Edicom\\INVOICE\\PRO. Ruta destino: \\\\fileserver.example\\EDI$\\PROD\\EDI\\PTX\\Edicom\\INVOICE\\PDT."
        },
        {
            "order": 6,
            "action": "Pasados 15-30 minutos, en la consola Edicom de PTX ir a Salida -> No enviados, filtrar por el número de documento y usar la opción ''Tratar duplicados'' para forzar el reenvío del mensaje corregido al cliente",
            "tool": "edicom.resend_message",
            "notes": "Requiere interactuar con la consola web de Edicom (no hay API). El flujo real no es un reenvío genérico: hay que localizar el mensaje en ''No enviados'' y usar específicamente ''Tratar duplicados'', no un botón de reenvío directo."
        }
    ]'::jsonb
) ON CONFLICT DO NOTHING;

-- ── PROC 3: Corrección error "Not serial number available" en PCAE ────────────
INSERT INTO edi_resolution_procedures (
    procedure_name, doc_type, problem_type, description, steps
) VALUES (
    'Corrección error Not serial number available en PCAE',
    'DESADV',
    'system_error',
    'El sistema PCAE devuelve el error "Not serial number available" al procesar un DESADV, '
    'lo que indica que no encuentra los datos de seriación del palet/caja en su base de datos. '
    'Se localiza el fichero de log de error, se extraen los datos y se crea la entrada en PCAE mediante su API.',
    '[
        {
            "order": 1,
            "action": "Localizar el fichero de log de error de PCAE que corresponde al número de pedido o entrega reportado",
            "tool": "filesystem.list_files",
            "notes": "Buscar en la carpeta de logs de PCAE el fichero con el número de pedido indicado en el correo. Puede estar en una ruta como //fileserver.example/.../PCAE/LOGS o similar."
        },
        {
            "order": 2,
            "action": "Leer el contenido del fichero de log para extraer los datos de la petición que hay que enviar a PCAE",
            "tool": "filesystem.read_file",
            "notes": "El fichero de log contiene el body JSON que se debe enviar a la API de PCAE (endpoint ReadDESADV). Extraer el contenido íntegro."
        },
        {
            "order": 3,
            "action": "Crear la entrada de seriación en PCAE enviando el contenido del log a la API ReadDESADV",
            "tool": "pcae.create_entry",
            "notes": "SysEdi inyecta automáticamente la contraseña en la petición. Enviar el contenido JSON leído del log como cuerpo de la llamada."
        },
        {
            "order": 4,
            "action": "Verificar que la respuesta de PCAE indica éxito (HTTP 200) y que el número de serie ha quedado registrado correctamente",
            "notes": "Si la API devuelve error, revisar el contenido del log enviado: puede que el formato sea incorrecto o falten campos. Escalar si persiste."
        },
        {
            "order": 5,
            "action": "Notificar al equipo de logística (logistics-team@example.com) la resolución del error indicando el número de pedido o entrega afectado"
        }
    ]'::jsonb
) ON CONFLICT DO NOTHING;

-- ── PROC 4: Resolución error Edicom "No existe punto operacional origen" ──────
INSERT INTO edi_resolution_procedures (
    procedure_name, doc_type, problem_type, description, steps
) VALUES (
    'Resolución error Edicom No existe punto operacional origen',
    NULL,
    'system_error',
    'Edicom notifica el error "No existe punto operacional origen" (o "Unknown sender operational point") '
    'al recibir un mensaje EDI de entrada de un GLN emisor que no está dado de alta en la consola Edicom. '
    'Se identifica el GLN, se busca el partner en SAP, se confirma con logística si el mensaje era esperado '
    'y se coordina el alta del punto operacional si procede.',
    '[
        {
            "order": 1,
            "action": "Obtener los detalles del mensaje en error en Edicom para extraer el GLN origen (punto operacional emisor desconocido)",
            "tool": "edicom.get_message_details",
            "notes": "El GLN origen es el identificador de 13 dígitos del emisor que Edicom no reconoce. Suele aparecer en el campo UNB o NAD+BY del mensaje EDIFACT."
        },
        {
            "order": 2,
            "action": "Buscar en SAP el interlocutor comercial que tenga asignado ese GLN para identificar si el emisor es un cliente o proveedor conocido",
            "tool": "erp.search_partner_by_gln",
            "notes": "Si se encuentra en SAP, el partner existe en el sistema pero el punto operacional no está dado de alta en Edicom. Si no se encuentra, puede ser un emisor desconocido."
        },
        {
            "order": 3,
            "action": "Verificar el estado del mensaje en Edicom para identificar el tipo de documento (ORDERS, DESADV, etc.) y el flujo afectado",
            "tool": "edicom.check_message_status",
            "notes": "Determinar el tipo de documento ayuda a identificar el equipo responsable del flujo y si el mensaje era esperado."
        },
        {
            "order": 4,
            "action": "Consultar con el equipo de logística (logistics-team@example.com) si se esperaba recibir mensajes EDI de ese GLN",
            "notes": "IMPRESCINDIBLE confirmar antes de dar de alta el punto operacional: podría ser un partner nuevo no comunicado, un error de configuración del emisor, o un mensaje no esperado. Indicar en el correo: GLN, nombre del partner (si se encontró en SAP), tipo de documento."
        },
        {
            "order": 5,
            "action": "Si el equipo confirma que el mensaje ES esperado: escalar al equipo EDI (edi-support@example.com) para coordinar con Edicom el alta del punto operacional origen en la consola EDI",
            "notes": "Proporcionar: GLN origen, nombre del partner, tipo de documento, consola Edicom afectada (AMC o PTX), y confirmación del equipo de logística."
        },
        {
            "order": 6,
            "action": "Si el mensaje NO es esperado: descartar o ignorar el mensaje en Edicom y notificar al equipo EDI para registrar el incidente",
            "notes": "Documentar el GLN para detectar si vuelve a aparecer. Si se repite, investigar si el emisor tiene un error de configuración en su plataforma EDI."
        }
    ]'::jsonb
) ON CONFLICT DO NOTHING;
