from apps.masters.models import PriorityMaster, RootCauseTypeMaster, SeverityMaster


def seed_company_classifications(company):
    from apps.masters.management.commands.seed_masters import PRIORITIES, ROOT_CAUSE_TYPES, SEVERITIES

    for code, name, rank, color, sla in PRIORITIES:
        PriorityMaster.objects.get_or_create(company=company, code=code, defaults={
            "name": name, "rank": rank, "color": color, "sla_days": sla, "is_system": True,
        })
    for code, name, rank, color in SEVERITIES:
        SeverityMaster.objects.get_or_create(company=company, code=code, defaults={
            "name": name, "rank": rank, "color": color, "is_system": True,
        })
    for index, (code, name) in enumerate(ROOT_CAUSE_TYPES, start=1):
        RootCauseTypeMaster.objects.get_or_create(company=company, code=code, defaults={
            "name": name, "rank": index * 10, "is_system": True,
        })
