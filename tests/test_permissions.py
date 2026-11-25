import pytest

from slothbot.permissions import is_authorised

MODERATOR_ROLE = 7
OTHER_ROLE = 8


@pytest.mark.parametrize(
    ("is_administrator", "member_roles", "moderator_roles", "expected"),
    [
        (True, (), (), True),
        (False, (MODERATOR_ROLE, OTHER_ROLE), (MODERATOR_ROLE,), True),
        (False, (OTHER_ROLE,), (MODERATOR_ROLE,), False),
        (False, (OTHER_ROLE,), (), False),
    ],
    ids=["administrator", "moderator-role", "unrelated-role", "no-moderator-roles-registered"],
)
def test_is_authorised_allows_administrators_and_moderator_role_holders(
    is_administrator, member_roles, moderator_roles, expected
):
    result = is_authorised(
        is_administrator=is_administrator,
        member_role_ids=member_roles,
        moderator_role_ids=moderator_roles,
    )

    assert result is expected
