-- ============================================================================
-- AMC.Py.Agent.EdiAuto — Migración de esquema
-- Script: 005_add_pending_manual_step_status.sql
--
-- Añade 'pending_manual_step' a los valores permitidos de
-- edi_historical_cases.status: el plan se ejecutó hasta el final salvo por
-- un paso cuya herramienta todavía no está implementada en SysEdi (p. ej.
-- edicom.resend_message). Se distingue de 'escalated' porque aquí el agente
-- SÍ ha completado y aplicado el resto de acciones (incluidas las de efecto
-- secundario) — no es un caso en el que la IA dudara del diagnóstico.
-- ============================================================================

ALTER TABLE edi_historical_cases
    DROP CONSTRAINT IF EXISTS edi_historical_cases_status_check;

ALTER TABLE edi_historical_cases
    ADD CONSTRAINT edi_historical_cases_status_check
    CHECK (status IN (
        'open', 'processing', 'resolved',
        'escalated', 'closed', 'waiting', 'pending_manual_step'
    ));
