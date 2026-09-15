"""
Funciones de dependencia compartidas entre routers.

Cada función construye el adaptador concreto que implementa el puerto
correspondiente. Los adaptadores sin estado propio (delegan en recursos
ya gestionados como singleton, ej. el pool de app.integrations.database)
se pueden instanciar libremente en cada request sin coste real.
"""

from fastapi import Depends, Request

from app.core.ports.incident_repository_port import IncidentRepositoryPort
from app.core.ports.mailbox_port import MailboxPort
from app.integrations.postgres_incident_repository import PostgresIncidentRepository


def get_incident_repository() -> IncidentRepositoryPort:
    return PostgresIncidentRepository()


def get_mailbox_port(request: Request) -> MailboxPort:
    """Reutiliza la única instancia de MailingClient creada en el lifespan (app.state)."""
    return request.app.state.mailing
