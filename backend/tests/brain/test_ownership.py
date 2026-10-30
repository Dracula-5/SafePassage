import check_ownership as co
import pytest

RULES = co.load_rules()


@pytest.mark.parametrize(
    "path,owner",
    [
        ("backend/app/main.py", "A"),
        ("backend/app/graph/supervisor.py", "A"),
        ("backend/migrations/101_add_index.sql", "A"),
        ("backend/migrations/201_osm.sql", "B"),
        ("backend/app/api_senses/router.py", "B"),
        ("frontend/src/app/page.tsx", "B"),
        ("docs/gates/A-G1.md", "A"),
        ("docs/gates/B-G1.md", "B"),
        ("contracts/openapi.yaml", "G0"),
        ("contracts/requests/A-new-column.md", "A"),
        ("contracts/requests/B-new-event.md", "B"),
        ("somewhere/else.py", None),
    ],
)
def test_owner_of(path, owner):
    assert co.owner_of(path, RULES) == owner
