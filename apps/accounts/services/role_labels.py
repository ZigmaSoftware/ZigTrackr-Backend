"""Current active role labels for staff names shown in ticket conversations."""

from apps.accounts.models import UserRole


def role_labels_for_users(user_ids, *, unique=False):
    """Return role names for user PKs or public UUIDs in one query."""
    ids = set(user_ids)
    if not ids:
        return {}
    field = "user__unique_id" if unique else "user_id"
    rows = (
        UserRole.objects.filter(
            **{f"{field}__in": ids},
            is_active=True,
            role__is_active=True,
            role__is_deleted=False,
        )
        .order_by("role__rank", "role__name")
        .values_list(field, "role__name")
    )
    labels = {}
    for user_id, name in rows:
        labels.setdefault(str(user_id) if unique else user_id, []).append(name)
    return {user_id: ", ".join(names) for user_id, names in labels.items()}
