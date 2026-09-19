-- Guarantees the DATABASE keeps, checked by the database. Runs inside one
-- transaction that always rolls back, so it is safe on any environment.
-- Fixture ids are negative: Telegram never issues negative ids, so this is a
-- namespace, not a prefix. Every check prints PASS or raises FAIL.
BEGIN;

DO $$
DECLARE
  n int;
BEGIN
  -- 1. audit_logs rejects UPDATE
  INSERT INTO audit_logs (actor_role, action, entity_type, entity_id) VALUES ('SYSTEM', '__inv', '__inv', '-1');
  BEGIN
    UPDATE audit_logs SET action = 'edited' WHERE entity_id = '-1';
    RAISE EXCEPTION 'FAIL audit_logs accepted an UPDATE';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'FAIL%' THEN RAISE; END IF;
    RAISE NOTICE 'PASS audit_logs rejects UPDATE';
  END;

  -- 2. audit_logs rejects DELETE
  BEGIN
    DELETE FROM audit_logs WHERE entity_id = '-1';
    RAISE EXCEPTION 'FAIL audit_logs accepted a DELETE';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'FAIL%' THEN RAISE; END IF;
    RAISE NOTICE 'PASS audit_logs rejects DELETE';
  END;

  -- 3. one outbox row per dedupe key, and a SENT row still holds its key
  INSERT INTO bot_notifications (dedupe_key, chat_id, payload, status) VALUES ('__inv:1', -1, '{}'::jsonb, 'SENT');
  BEGIN
    INSERT INTO bot_notifications (dedupe_key, chat_id, payload) VALUES ('__inv:1', -1, '{}'::jsonb);
    RAISE EXCEPTION 'FAIL bot_notifications accepted a duplicate dedupe_key';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS bot_notifications dedupe_key is unique even after SENT';
  END;

  -- 4. outbox status is a closed set
  BEGIN
    INSERT INTO bot_notifications (dedupe_key, chat_id, payload, status) VALUES ('__inv:2', -1, '{}'::jsonb, 'MAYBE');
    RAISE EXCEPTION 'FAIL bot_notifications accepted an unknown status';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS bot_notifications status is a closed set';
  END;

  -- 5. operator roles are a closed set
  BEGIN
    INSERT INTO operators (email, display_name, password_hash, role) VALUES ('__inv@invalid', 'x', 'x', 'GOD');
    RAISE EXCEPTION 'FAIL operators accepted an unknown role';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'PASS operators role is a closed set';
  END;

  -- 6. operator email is unique
  INSERT INTO operators (email, display_name, password_hash, role) VALUES ('__inv@invalid', 'x', 'x', 'ADMIN');
  BEGIN
    INSERT INTO operators (email, display_name, password_hash, role) VALUES ('__inv@invalid', 'y', 'y', 'ADMIN');
    RAISE EXCEPTION 'FAIL operators accepted a duplicate email';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS operators email is unique';
  END;

  -- 7. settings are keyed by (scope, key)
  INSERT INTO settings (scope, key, value) VALUES ('__inv', 'k', '1'::jsonb);
  BEGIN
    INSERT INTO settings (scope, key, value) VALUES ('__inv', 'k', '2'::jsonb);
    RAISE EXCEPTION 'FAIL settings accepted a duplicate (scope, key)';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'PASS settings (scope, key) is the primary key';
  END;
END $$;

ROLLBACK;
