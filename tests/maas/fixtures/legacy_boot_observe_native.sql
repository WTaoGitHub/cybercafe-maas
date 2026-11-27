-- Reproduce the former observer objects in an isolated PostgreSQL test.
-- The sequence orders successful registrations. The worker handoff row lock
-- serializes registrations for one worker; duplicate physical owners are excluded.
BEGIN;
LOCK TABLE maas_worker_handoffs IN SHARE ROW EXCLUSIVE MODE;
CREATE TABLE IF NOT EXISTS maas_boot_registrations (
    seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    worker_id bigint NOT NULL, session_id uuid NOT NULL UNIQUE,
    cluster_id bigint NOT NULL, gpu_uuid text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE OR REPLACE FUNCTION maas_observe_active_registration() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
    INSERT INTO public.maas_boot_registrations(worker_id,session_id,cluster_id,gpu_uuid)
    SELECT w.id, NEW.session_id::uuid, w.cluster_id, w.external_id FROM public.workers w
    WHERE w.id=NEW.worker_id AND w.deleted_at IS NULL AND w.name=w.external_id
    ON CONFLICT(session_id) DO NOTHING;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS maas_observe_registration ON maas_worker_handoffs;
CREATE TRIGGER maas_observe_registration AFTER UPDATE OF phase ON maas_worker_handoffs
    FOR EACH ROW WHEN (NEW.phase='active' AND OLD.phase='registering')
    EXECUTE FUNCTION maas_observe_active_registration();
INSERT INTO maas_boot_registrations(worker_id,session_id,cluster_id,gpu_uuid)
SELECT w.id,h.session_id::uuid,w.cluster_id,w.external_id FROM maas_worker_handoffs h
JOIN workers w ON w.id=h.worker_id
WHERE h.phase='active' AND w.deleted_at IS NULL AND w.name=w.external_id
ON CONFLICT(session_id) DO NOTHING;
CREATE OR REPLACE VIEW maas_current_boot_registrations AS
SELECT r.* FROM maas_boot_registrations r JOIN maas_worker_handoffs h
  ON h.worker_id=r.worker_id AND h.session_id=r.session_id::text AND h.phase='active'
JOIN workers w ON w.id=h.worker_id AND w.deleted_at IS NULL AND w.cluster_id=r.cluster_id
  AND w.external_id=r.gpu_uuid AND w.name=w.external_id
WHERE NOT EXISTS (SELECT 1 FROM workers other WHERE other.cluster_id=w.cluster_id
  AND other.deleted_at IS NULL AND other.external_id=w.external_id AND other.id<>w.id);
COMMIT;
