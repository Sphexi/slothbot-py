"""Authorisation rule for command invokers."""

from collections.abc import Collection


def is_authorised(
    *,
    is_administrator: bool,
    member_role_ids: Collection[int],
    moderator_role_ids: Collection[int],
) -> bool:
    """Administrators pass; otherwise the member must hold a registered moderator role."""
    if is_administrator:
        return True
    return not set(moderator_role_ids).isdisjoint(member_role_ids)
