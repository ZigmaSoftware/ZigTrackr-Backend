from django.db import migrations


VIEWS = {
    "tracker_ticket_overview": """
        SELECT t.id AS ticket_row_id, t.unique_id AS ticket_uuid,
               t.company_id, c.code AS company_code, c.name AS company_name,
               t.ref_no, t.ticket_no, t.ticket_type, t.source, t.title,
               COALESCE(b.status, t.status) AS effective_status,
               t.status AS intake_status, b.bug_no,
               t.owner_id, u.username AS developer_username, u.full_name AS developer_name,
               p.name AS project_name, m.name AS module_name, sm.name AS submodule_name,
               pr.name AS priority_name, se.name AS severity_name,
               COALESCE(b.expected_closure_date, t.expected_closure_date) AS expected_closure_date,
               t.environment, t.needs_review, t.is_deleted, t.created_at, t.updated_at
        FROM support_ticket t
        LEFT JOIN company_master c ON c.id = t.company_id
        LEFT JOIN bug_tracker b ON b.id = t.bug_id
        LEFT JOIN users u ON u.id = t.owner_id
        LEFT JOIN project_master p ON p.id = COALESCE(t.project_id, b.project_id)
        LEFT JOIN module_master m ON m.id = COALESCE(t.module_id, b.module_id)
        LEFT JOIN submodule_master sm ON sm.id = COALESCE(t.submodule_id, b.submodule_id)
        LEFT JOIN priority_master pr ON pr.id = COALESCE(t.priority_id, b.priority_id)
        LEFT JOIN severity_master se ON se.id = COALESCE(t.severity_id, b.severity_id)
    """,
    "tracker_company_summary": """
        SELECT c.id AS company_id, c.code AS company_code, c.name AS company_name, c.is_active,
               (SELECT COUNT(*) FROM users u WHERE u.company_id=c.id AND u.is_deleted=0) AS user_count,
               (SELECT COUNT(*) FROM project_master p WHERE p.company_id=c.id AND p.is_deleted=0) AS project_count,
               (SELECT COUNT(*) FROM support_ticket t WHERE t.company_id=c.id AND t.is_deleted=0) AS ticket_count,
               (SELECT COUNT(*) FROM support_ticket t WHERE t.company_id=c.id AND t.is_deleted=0 AND t.owner_id IS NULL) AS unassigned_count
        FROM company_master c WHERE c.is_deleted=0
    """,
}


def create_views(apps, schema_editor):
    # TEMPTABLE prevents accidental writes through inspection views on MariaDB.
    algorithm = "ALGORITHM=TEMPTABLE " if schema_editor.connection.vendor == "mysql" else ""
    for name, sql in VIEWS.items():
        schema_editor.execute(f"CREATE {algorithm}VIEW {name} AS {sql}")


def drop_views(apps, schema_editor):
    for name in VIEWS:
        schema_editor.execute(f"DROP VIEW IF EXISTS {name}")


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("tickets", "0017_supportticket_idx_ticket_company_state")]
    operations = [migrations.RunPython(create_views, drop_views)]
