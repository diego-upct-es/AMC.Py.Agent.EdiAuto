-- ============================================================================
-- AMC.Py.Agent.EdiAuto — Schema inicial PostgreSQL
-- Motor: PostgreSQL | BD: edi_auto
-- Usuario gestión (DDL): edi_admin
-- Usuario aplicación (CRUD): edi_app
-- ============================================================================

-- ── edi_flow_configurations ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS edi_flow_configurations (
    id           SERIAL PRIMARY KEY,
    console      VARCHAR(20)  NOT NULL
                 CHECK (console IN ('AMC', 'PTX')),
    client_code  VARCHAR(50)  NOT NULL,
    message_type VARCHAR(10)  NOT NULL,
    direction    VARCHAR(10)  NOT NULL
                 CHECK (direction IN ('INBOUND', 'OUTBOUND')),
    flow_steps   JSONB,
    sla_minutes  INTEGER      DEFAULT 120,
    active       BOOLEAN      DEFAULT true,
    notes        TEXT,
    created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (console, client_code, message_type, direction)
);

-- ── edi_resolution_procedures ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS edi_resolution_procedures (
    id             SERIAL PRIMARY KEY,
    procedure_name VARCHAR(100) NOT NULL,
    doc_type       VARCHAR(10),
    problem_type   VARCHAR(50)  NOT NULL,
    description    TEXT,
    steps          JSONB        NOT NULL,
    success_rate   FLOAT        DEFAULT 0.0,
    times_used     INTEGER      DEFAULT 0,
    active         BOOLEAN      DEFAULT true,
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── edi_historical_cases ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS edi_historical_cases (
    id                       SERIAL PRIMARY KEY,
    case_id                  VARCHAR(50) UNIQUE NOT NULL,
    console                  VARCHAR(20),
    client_code              VARCHAR(50),
    doc_type                 VARCHAR(10),
    doc_number               VARCHAR(100),
    email_subject            TEXT,
    email_from               VARCHAR(255),
    problem_description      TEXT NOT NULL,
    problem_type             VARCHAR(50),
    urgency                  VARCHAR(20),
    extracted_info           JSONB,
    action_plan              JSONB,
    actions_executed         JSONB,
    resolved_automatically   BOOLEAN,
    resolution_summary       TEXT,
    resolution_time_minutes  INTEGER,
    requires_human           BOOLEAN,
    severity                 VARCHAR(20),
    status                   VARCHAR(30) DEFAULT 'open'
                             CHECK (status IN (
                                 'open', 'processing', 'resolved',
                                 'escalated', 'closed', 'waiting'
                             )),
    created_at               TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at              TIMESTAMP WITH TIME ZONE,
    updated_at               TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ── edi_processed_emails ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS edi_processed_emails (
    id           SERIAL PRIMARY KEY,
    message_id   VARCHAR(500) UNIQUE NOT NULL,
    mailbox      VARCHAR(255) NOT NULL,
    subject      TEXT,
    from_email   VARCHAR(255),
    received_at  TIMESTAMP WITH TIME ZONE,
    processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    case_id      VARCHAR(50) REFERENCES edi_historical_cases(case_id),
    status       VARCHAR(30) DEFAULT 'processed'
                 CHECK (status IN ('processing', 'processed', 'error', 'reprocess'))
);

-- ── agent_memory ──────────────────────────────────────────────────────────────
-- Tabla para gestión de memoria del agente (patrón OCIPgMemoryService).
-- Usada si en el futuro se adopta Google ADK con memoria persistente.
-- Por ahora el contexto entre pasos viaja en el mensaje de Azure Service Bus.
CREATE TABLE IF NOT EXISTS agent_memory (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_name   TEXT NOT NULL,
    user_id    TEXT NOT NULL,         -- incident_id actúa como user_id
    session_id TEXT NOT NULL,
    content    JSONB NOT NULL,
    source     TEXT DEFAULT 'local',  -- 'local' | 'oci'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_user
    ON agent_memory (app_name, user_id);
