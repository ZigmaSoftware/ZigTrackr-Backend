"""Company is an access boundary; role grants never widen it."""

from rest_framework.exceptions import PermissionDenied


def is_super_admin(user):
    return bool(user and user.is_authenticated and user.is_superuser)


def entity_company_id(entity):
    if entity is None:
        return None
    if getattr(getattr(entity, "_meta", None), "label_lower", "") == "masters.companymaster":
        return entity.pk
    if hasattr(entity, "company_id"):
        return entity.company_id
    if hasattr(entity, "project"):
        return entity_company_id(entity.project)
    if hasattr(entity, "module"):
        return entity_company_id(entity.module)
    if hasattr(entity, "ticket"):
        return entity_company_id(entity.ticket)
    if hasattr(entity, "bug"):
        return entity_company_id(entity.bug)
    if hasattr(entity, "mail"):
        return entity_company_id(entity.mail)
    return None


def scope_company_queryset(queryset, user, field="company_id"):
    if not user or not user.is_authenticated:
        return queryset.none()
    if is_super_admin(user):
        return queryset
    if not user.company_id or not user.company.is_active or user.company.is_deleted:
        return queryset.none()
    return queryset.filter(**{field: user.company_id})


def can_access_company(user, entity):
    if is_super_admin(user):
        return True
    return bool(user and user.is_authenticated and user.company_id
                and user.company.is_active and not user.company.is_deleted
                and entity_company_id(entity) == user.company_id)


def require_company_access(user, entity):
    if not can_access_company(user, entity):
        raise PermissionDenied("You can access only your own company's data.")


def require_super_admin(user):
    if not is_super_admin(user):
        raise PermissionDenied("Only Super Admin can manage this setting.")


def validate_company_relations(company_id, values):
    from rest_framework.exceptions import ValidationError
    errors = {}
    for field, value in values.items():
        if value is None or not hasattr(value, "_meta"):
            continue
        if entity_company_id(value) != company_id:
            errors[field] = ["Select a record from the same company."]
    if errors:
        raise ValidationError(errors)
