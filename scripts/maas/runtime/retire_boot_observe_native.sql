-- Retire the MaaS boot observer in the GPUStack PostgreSQL database.
-- Keep its historical rows and leave the registration and handoff functions in place.
BEGIN;
SET LOCAL lock_timeout = '2s';
DO $$
DECLARE
    observer record;
BEGIN
    SELECT t.tgenabled, p.proname, n.nspname, t.tgtype, t.tgattr::text AS attrs,
           a.attnum::text AS phase_attr
    INTO observer
    FROM pg_trigger t
    JOIN pg_proc p ON p.oid = t.tgfoid
    JOIN pg_namespace n ON n.oid = p.pronamespace
    JOIN pg_attribute a ON a.attrelid = t.tgrelid AND a.attname = 'phase' AND NOT a.attisdropped
    WHERE t.tgrelid = to_regclass('public.maas_worker_handoffs')
      AND t.tgname = 'maas_observe_registration' AND NOT t.tgisinternal;

    IF NOT FOUND THEN
        RETURN;
    END IF;
    IF observer.proname <> 'maas_observe_active_registration' OR observer.nspname <> 'public'
       OR observer.tgtype <> 17 OR observer.attrs <> observer.phase_attr THEN
        RAISE EXCEPTION 'Unexpected definition of the MaaS boot observer trigger';
    END IF;
    IF observer.tgenabled <> 'D' THEN
        ALTER TABLE public.maas_worker_handoffs DISABLE TRIGGER maas_observe_registration;
    END IF;
END;
$$;
COMMIT;
