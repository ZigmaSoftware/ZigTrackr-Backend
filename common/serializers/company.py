from rest_framework import serializers

from common.permissions.company import scope_company_queryset


class CompanyRelatedFieldsMixin:
    """Resolve submitted UUIDs inside the caller's company before validation."""

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if not request:
            return fields
        for field in fields.values():
            if not isinstance(field, serializers.RelatedField) or field.queryset is None:
                continue
            model = field.queryset.model
            label = model._meta.label_lower
            if label == "masters.companymaster":
                parent = "pk"
            elif label == "masters.modulemaster":
                parent = "project__company_id"
            elif label == "masters.submodulemaster":
                parent = "module__project__company_id"
            elif hasattr(model, "company"):
                parent = "company_id"
            else:
                continue
            field.queryset = scope_company_queryset(field.queryset, request.user, parent)
        return fields
