from django.db import migrations


def backfill(apps, schema_editor):
    db = schema_editor.connection.alias
    Company = apps.get_model("masters", "CompanyMaster")
    company, _ = Company.objects.using(db).get_or_create(
        code="ZIGMA", defaults={"name": "Zigma Global Environ Solutions"},
    )
    for name in ("ProjectMaster", "DepartmentMaster", "TeamMaster", "SiteMaster",
                 "PriorityMaster", "SeverityMaster", "RootCauseTypeMaster"):
        apps.get_model("masters", name).objects.using(db).filter(company__isnull=True).update(company_id=company.pk)
    User = apps.get_model("accounts", "User")
    User.objects.using(db).filter(company__isnull=True, is_superuser=False).update(company_id=company.pk)
    Bug = apps.get_model("bugs", "Bug")
    for bug in Bug.objects.using(db).filter(company__isnull=True).select_related("project").iterator():
        Bug.objects.using(db).filter(pk=bug.pk).update(company_id=bug.project.company_id if bug.project_id else company.pk)
    Ticket = apps.get_model("tickets", "SupportTicket")
    for ticket in Ticket.objects.using(db).filter(company__isnull=True).select_related("bug", "project").iterator():
        owner = ticket.bug.company_id if ticket.bug_id else ticket.project.company_id if ticket.project_id else company.pk
        updates = {"company_id": owner}
        if ticket.bug_id:
            for field in ("severity", "department", "site"):
                updates[f"{field}_id"] = getattr(ticket.bug, f"{field}_id")
            updates["environment"] = ticket.bug.environment or ""
        Ticket.objects.using(db).filter(pk=ticket.pk).update(**updates)
    Mail = apps.get_model("mail_intake", "MailIntake")
    for mail in Mail.objects.using(db).filter(company__isnull=True).select_related("linked_ticket").iterator():
        Mail.objects.using(db).filter(pk=mail.pk).update(company_id=mail.linked_ticket.company_id if mail.linked_ticket_id else company.pk)
    Audit = apps.get_model("audit", "AuditLog")
    entities = {"Bug": Bug, "SupportTicket": Ticket, "MailIntake": Mail, "User": User,
                **{name: apps.get_model("masters", name) for name in (
                    "ProjectMaster", "DepartmentMaster", "TeamMaster", "SiteMaster",
                    "PriorityMaster", "SeverityMaster", "RootCauseTypeMaster")}}
    for name, model in entities.items():
        for pk, tenant in model.objects.using(db).values_list("pk", "company_id").iterator():
            Audit.objects.using(db).filter(entity_type=name, entity_id=pk, company__isnull=True).update(company_id=tenant)


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0015_supportticket_company_supportticket_department_and_more"),
        ("masters", "0003_remove_prioritymaster_uq_priority_master_code_and_more"),
        ("accounts", "0004_user_company"),
        ("bugs", "0004_bug_company"),
        ("mail_intake", "0004_mailintake_company"),
        ("audit", "0006_auditlog_company_alter_auditlog_action"),
    ]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
