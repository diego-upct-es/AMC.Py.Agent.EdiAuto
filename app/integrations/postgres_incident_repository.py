"""
Adaptador Postgres del puerto IncidentRepositoryPort.

Encapsula todo el SQL de incidencias EDI (edi_historical_cases, edi_processed_emails,
edi_resolution_procedures). Usa app.integrations.database como utilidad de bajo nivel
para el pool de conexiones — no expone asyncpg directamente al resto de la app.
"""

import json
from typing import Any

from app.core.ports.incident_repository_port import IncidentRepositoryPort
from app.integrations.database import execute, fetch, fetchrow, fetchval
from app.models.incident import IncidentResponse

_INCIDENT_FIELDS = """
    case_id, status, client_code, doc_type, problem_description,
    urgency, resolved_automatically, resolution_summary,
    created_at, resolved_at, requires_human
"""


class PostgresIncidentRepository(IncidentRepositoryPort):

    # ── Consulta de incidencias ──────────────────────────────────────────────

    async def list_incidents(
        self, status: str | None, limit: int, offset: int
    ) -> list[IncidentResponse]:
        rows = await fetch(
            f"""
            SELECT {_INCIDENT_FIELDS}
            FROM edi_historical_cases
            WHERE ($1::varchar IS NULL OR status = $1)
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            status,
            limit,
            offset,
        )
        return [IncidentResponse(**dict(r)) for r in rows]

    async def get_incident(self, case_id: str) -> IncidentResponse | None:
        row = await fetchrow(
            f"""
            SELECT {_INCIDENT_FIELDS}
            FROM edi_historical_cases
            WHERE case_id = $1
            """,
            case_id,
        )
        return IncidentResponse(**dict(row)) if row else None

    async def has_processed_email(self, case_id: str) -> bool:
        row = await fetchrow(
            "SELECT id FROM edi_processed_emails WHERE case_id = $1",
            case_id,
        )
        return row is not None

    async def mark_incident_for_reprocess(self, case_id: str) -> None:
        await execute(
            "UPDATE edi_processed_emails SET status = 'reprocess' WHERE case_id = $1",
            case_id,
        )
        await execute(
            "UPDATE edi_historical_cases SET status = 'open', updated_at = NOW() WHERE case_id = $1",
            case_id,
        )

    # ── Escritura del flujo (IncidentProcessor / Worker) ────────────────────

    async def is_email_already_processed(self, message_id: str) -> bool:
        row = await fetchrow(
            "SELECT id FROM edi_processed_emails WHERE message_id = $1 AND status != 'reprocess'",
            message_id,
        )
        return row is not None

    async def register_new_case(
        self,
        message_id: str,
        mailbox: str,
        case_id: str,
        subject: str,
        from_email: str,
        received_at: Any,
    ) -> None:
        # edi_historical_cases primero: edi_processed_emails.case_id la referencia
        # por clave foránea, así que tiene que existir antes de poder insertarse.
        await execute(
            """
            INSERT INTO edi_historical_cases
                (case_id, email_subject, email_from, problem_description, status)
            VALUES ($1, $2, $3, $4, 'processing')
            ON CONFLICT (case_id) DO NOTHING
            """,
            case_id,
            subject,
            from_email,
            f"Correo recibido: {subject}",
        )
        await execute(
            """
            INSERT INTO edi_processed_emails
                (message_id, mailbox, subject, from_email, received_at, case_id, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'processing')
            ON CONFLICT (message_id) DO UPDATE
                SET status = 'processing', case_id = EXCLUDED.case_id
            """,
            message_id,
            mailbox,
            subject,
            from_email,
            received_at,
            case_id,
        )

    async def save_extraction_and_plan(
        self,
        case_id: str,
        client_code: str | None,
        doc_type: str | None,
        doc_number: str | None,
        problem_type: str | None,
        urgency: str | None,
        console: str | None,
        severity: str | None,
        extracted_info: dict,
        action_plan: dict,
    ) -> None:
        await execute(
            """
            UPDATE edi_historical_cases SET
                client_code   = $2,
                doc_type      = $3,
                doc_number    = $4,
                problem_type  = $5,
                urgency       = $6,
                console       = $7,
                severity      = $8,
                extracted_info = $9::jsonb,
                action_plan   = $10::jsonb,
                updated_at    = NOW()
            WHERE case_id = $1
            """,
            case_id,
            client_code,
            doc_type,
            doc_number,
            problem_type,
            urgency,
            console,
            severity,
            json.dumps(extracted_info, ensure_ascii=False),
            json.dumps(action_plan, ensure_ascii=False),
        )

    async def escalate_case(
        self, case_id: str, resolution_summary: str, mark_email_error: bool = False
    ) -> None:
        await execute(
            """
            UPDATE edi_historical_cases SET
                status = 'escalated', resolution_summary = $2,
                requires_human = true, updated_at = NOW(), resolved_at = NOW(),
                resolution_time = NOW() - created_at
            WHERE case_id = $1
            """,
            case_id,
            resolution_summary,
        )
        email_status = "error" if mark_email_error else "processed"
        await execute(
            "UPDATE edi_processed_emails SET status = $2 WHERE case_id = $1",
            case_id,
            email_status,
        )

    async def save_step_result(self, case_id: str, steps_results: list) -> None:
        await execute(
            """
            UPDATE edi_historical_cases
            SET actions_executed = $2::jsonb, updated_at = NOW()
            WHERE case_id = $1
            """,
            case_id,
            json.dumps(steps_results, default=str),
        )

    async def mark_case_waiting(self, case_id: str) -> None:
        await execute(
            """
            UPDATE edi_historical_cases
            SET status = 'waiting', updated_at = NOW()
            WHERE case_id = $1
            """,
            case_id,
        )

    async def finalize_case(
        self,
        case_id: str,
        resolved_automatically: bool,
        resolution_summary: str,
        requires_human: bool,
        status: str,
    ) -> None:
        await execute(
            """
            UPDATE edi_historical_cases SET
                resolved_automatically = $2,
                resolution_summary     = $3,
                requires_human         = $4,
                status                 = $5,
                resolved_at            = NOW(),
                updated_at             = NOW(),
                resolution_time        = NOW() - created_at
            WHERE case_id = $1
            """,
            case_id,
            resolved_automatically,
            resolution_summary,
            requires_human,
            status,
        )
        await execute(
            "UPDATE edi_processed_emails SET status = 'processed' WHERE case_id = $1",
            case_id,
        )

    async def fetch_similar_cases(
        self,
        problem_type: str,
        doc_type: str | None,
        client_code: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        rows = await fetch(
            """
            SELECT case_id, client_code, doc_type, problem_description,
                   resolution_summary, resolved_automatically, resolution_time
            FROM edi_historical_cases
            WHERE problem_type = $1
              AND status IN ('resolved', 'closed')
              AND ($2::varchar IS NULL OR doc_type = $2)
              AND ($3::varchar IS NULL OR client_code = $3)
            ORDER BY created_at DESC
            LIMIT $4
            """,
            problem_type,
            doc_type,
            client_code,
            limit,
        )
        return [dict(r) for r in rows]

    async def fetch_procedures(self, problem_type: str, doc_type: str | None) -> list[dict]:
        rows = await fetch(
            """
            SELECT id, procedure_name, doc_type, problem_type,
                   description, steps, success_rate, times_used
            FROM edi_resolution_procedures
            WHERE active = true
              AND problem_type = $1
              AND ($2::varchar IS NULL OR doc_type = $2 OR doc_type IS NULL)
            ORDER BY success_rate DESC
            LIMIT 5
            """,
            problem_type,
            doc_type,
        )
        return [dict(r) for r in rows]

    async def get_client_config(
        self, client_code: str, console: str | None = None
    ) -> list[dict]:
        rows = await fetch(
            """
            SELECT console, client_code, message_type, direction,
                   flow_steps, sla_minutes, notes
            FROM edi_flow_configurations
            WHERE client_code = $1
              AND active = true
              AND ($2::varchar IS NULL OR console = $2)
            ORDER BY message_type, direction
            """,
            client_code,
            console,
        )
        return [dict(r) for r in rows]

    async def increment_procedure_usage(self, procedure_id: int, success: bool) -> None:
        await execute(
            """
            UPDATE edi_resolution_procedures
            SET times_used  = times_used + 1,
                success_rate = (success_rate * times_used + $2::float) / (times_used + 1),
                updated_at  = NOW()
            WHERE id = $1
            """,
            procedure_id,
            1.0 if success else 0.0,
        )

    async def check_database_alive(self) -> None:
        await fetchval("SELECT 1")
