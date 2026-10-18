"""Database-level guards for Entry: immutability and the zero-sum rule."""

from django.db import migrations

FORWARD = """
CREATE FUNCTION ledger_entry_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entry is immutable: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER ledger_entry_no_update_delete
    BEFORE UPDATE OR DELETE ON ledger_entry
    FOR EACH ROW EXECUTE FUNCTION ledger_entry_immutable();

CREATE FUNCTION ledger_entry_sum_is_zero() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    total numeric;
BEGIN
    SELECT COALESCE(SUM(amount), 0) INTO total
    FROM ledger_entry WHERE transaction_id = NEW.transaction_id;
    IF total <> 0 THEN
        RAISE EXCEPTION 'entries of transaction % sum to %, expected 0', NEW.transaction_id, total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER ledger_entry_zero_sum
    AFTER INSERT ON ledger_entry
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_entry_sum_is_zero();
"""

REVERSE = """
DROP TRIGGER ledger_entry_zero_sum ON ledger_entry;
DROP FUNCTION ledger_entry_sum_is_zero();
DROP TRIGGER ledger_entry_no_update_delete ON ledger_entry;
DROP FUNCTION ledger_entry_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
