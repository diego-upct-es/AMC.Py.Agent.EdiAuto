# AMC.Py.Agent.EdiAuto

Agente de Inteligencia Artificial que automatiza la gestión de incidencias EDI (*Electronic Data Interchange*) en AMC Global. Es el componente práctico de un Trabajo Fin de Grado (TFG) del Grado en Ciencia e Ingeniería de Datos, desarrollado en colaboración con el departamento de sistemas de AMC Global.

Cuando un intercambio EDI con un cliente o proveedor falla, el agente recibe la incidencia desde el buzón de soporte, extrae la información relevante del caso mediante un modelo de lenguaje (incluido, cuando es necesario, el contenido de documentos adjuntos), recupera de una base de conocimiento el procedimiento de resolución correspondiente, genera un plan de actuación y lo ejecuta paso a paso sobre los sistemas corporativos, escalando el caso a una persona cuando no existe un procedimiento conocido o cuando la acción a realizar se considera de riesgo.

## Arquitectura

El proyecto sigue una arquitectura hexagonal (puertos y adaptadores):

- **Núcleo** (`app/core`): el agente (`EDIAgent`), el `Worker` que ejecuta el plan de actuación paso a paso, el `ActionExecutor` y el `IncidentProcessor`. No depende de ningún proveedor externo concreto, solo de los puertos que declara.
- **Puertos** (`app/core/ports`): interfaces que definen los contratos con el exterior (`AIPort`, `MailboxPort`, `ToolExecutorPort`, `MessageQueuePort`, `IncidentRepositoryPort`).
- **Adaptadores** (`app/integrations`): implementaciones concretas de esos puertos: cliente de Azure OpenAI / Oracle AI, cliente HTTP de `AMC.Core.SysEdi` (que expone el catálogo de herramientas sobre SAP, Edicom, el sistema de gestión de almacén y logística, el sistema de ficheros y PCAE), cliente de `AMC.Core.Sys.Mailing`, repositorio PostgreSQL y colas de Azure Service Bus.

Esta separación permitió, por ejemplo, sustituir el proveedor de IA inicialmente previsto (Oracle AI) por Azure OpenAI sin modificar el resto del código.

Los microservicios corporativos a los que llama el agente (`AMC.Core.SysEdi`, `AMC.Core.Sys.Mailing`) son sistemas internos de AMC Global y no forman parte de este repositorio.

## Stack tecnológico

FastAPI, httpx, pypdf, asyncpg (PostgreSQL), Azure Service Bus, Azure Key Vault, structlog. El detalle de cada dependencia está comentado en [requirements.txt](requirements.txt).

## Puesta en marcha

1. Instalar las dependencias: `pip install -r requirements.txt`
2. Copiar `.env.example` a `.env` y rellenar las variables necesarias (proveedor de IA, URLs de los microservicios internos, etc.)
3. Proporcionar la cadena de conexión a PostgreSQL. En AMC Global se resuelve automáticamente desde Azure Key Vault; fuera de ese entorno, se fija mediante la variable de entorno `CONNECTIONSTRINGS` en formato JSON, por ejemplo: `CONNECTIONSTRINGS={"EdiAuto": "postgresql://usuario:contraseña@host:5432/edi_auto"}`. El mismo mecanismo admite una clave `AzureServiceBus` para que el `Worker` procese los pasos posteriores al primero; sin ella, el servicio arranca igualmente, pero cada incidencia se escala directamente tras generarse el primer plan, sin pasar por la cola.
4. Aplicar los scripts de `sql/` en orden sobre esa base de datos, por ejemplo: `psql -d edi_auto -f sql/001_create_tables.sql` (y así sucesivamente con el resto de scripts, en orden).
5. Arrancar el servicio: `uvicorn app.main:app --reload`

También hay un `Dockerfile` y un `docker-compose.yml` para levantar el servicio junto con una base de datos PostgreSQL en contenedor (ya con la variable `CONNECTIONSTRINGS` anterior resuelta), y manifiestos de Kubernetes de ejemplo en `k8s/`.

El agente delega toda la ejecución de herramientas en `AMC.Core.SysEdi`, y la lectura y el envío de correo en `AMC.Core.Sys.Mailing`; ambos son sistemas internos de AMC Global y no forman parte de este repositorio. Por ello, una puesta en marcha fuera de la infraestructura de la empresa permite arrancar el servicio y ejercitar su lógica de orquestación, pero no completar el flujo de extremo a extremo sin sustituir esos dos adaptadores, por ejemplo por dobles de prueba que implementen `ToolExecutorPort` y `MailboxPort`.

## Estado del proyecto

El sistema se ha validado de forma manual, con un lote de incidencias sintéticas ejecutado en un entorno de pruebas aislado del buzón y los datos de producción (detalle completo en la memoria del TFG). Los directorios `tests/unit` y `tests/integration` están preparados, con `pytest`, `pytest-asyncio` y `pytest-httpx` ya incluidos entre las dependencias, pero todavía sin una batería de pruebas automatizada.
