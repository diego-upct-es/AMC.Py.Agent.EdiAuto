-- ============================================================================
-- AMC.Py.Agent.EdiAuto — Índices para rendimiento
-- Script: 002_indexes.sql
-- Ejecutar después de 001_create_tables.sql
-- ============================================================================

-- ── edi_historical_cases ──────────────────────────────────────────────────────

-- Búsqueda por estado (listado de incidencias en API REST)
CREATE INDEX IF NOT EXISTS idx_historical_status
    ON edi_historical_cases(status);

-- Búsqueda de casos similares por tipo de problema (el más frecuente en el agente)
CREATE INDEX IF NOT EXISTS idx_historical_problem_type
    ON edi_historical_cases(problem_type);

-- Búsqueda combinada (la query más usada por el agente IA)
CREATE INDEX IF NOT EXISTS idx_historical_problem_doc
    ON edi_historical_cases(problem_type, doc_type, status);

-- Búsqueda por cliente
CREATE INDEX IF NOT EXISTS idx_historical_client
    ON edi_historical_cases(client_code);

-- Ordenación temporal (listados descendentes)
CREATE INDEX IF NOT EXISTS idx_historical_created_at
    ON edi_historical_cases(created_at DESC);

-- ── edi_processed_emails ──────────────────────────────────────────────────────

-- Lookup por case_id (para el endpoint reprocess)
CREATE INDEX IF NOT EXISTS idx_processed_emails_case_id
    ON edi_processed_emails(case_id);

-- Lookup por estado (filtrar los marcados para reprocesar)
CREATE INDEX IF NOT EXISTS idx_processed_emails_status
    ON edi_processed_emails(status);

-- ── edi_resolution_procedures ─────────────────────────────────────────────────

-- Búsqueda por tipo de problema (query del knowledge base MCP)
CREATE INDEX IF NOT EXISTS idx_procedures_problem_type
    ON edi_resolution_procedures(problem_type, doc_type)
    WHERE active = true;

-- ── edi_flow_configurations ───────────────────────────────────────────────────

-- Búsqueda por cliente (query de get_client_config)
CREATE INDEX IF NOT EXISTS idx_flow_config_client
    ON edi_flow_configurations(client_code, console)
    WHERE active = true;
