-- ============================================================================
-- AMC.Py.Agent.EdiAuto — Migración de esquema
-- Script: 004_resolution_time_as_interval.sql
--
-- Sustituye resolution_time_minutes (INTEGER, minutos) por resolution_time
-- (INTERVAL) — Postgres lo muestra en formato HH:MM:SS de forma nativa para
-- duraciones menores a 24h (para duraciones mayores antepone "N days").
-- ============================================================================

ALTER TABLE edi_historical_cases
    ADD COLUMN IF NOT EXISTS resolution_time INTERVAL;

UPDATE edi_historical_cases
SET resolution_time = resolved_at - created_at
WHERE resolved_at IS NOT NULL AND resolution_time IS NULL;

ALTER TABLE edi_historical_cases
    DROP COLUMN IF EXISTS resolution_time_minutes;
