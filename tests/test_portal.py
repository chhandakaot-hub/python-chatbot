"""Guards on the portal connection.

These do not need the portal database to be reachable -- they pin the two
properties that keep a live production schema safe from this app.
"""

from app.core.database import Base
from app.core.portal_database import PortalBase, engine, get_portal_db


def test_portal_tables_are_not_registered_on_the_app_base():
    """`init_db()` runs create_all() on Base. Portal tables must never be in it."""
    app_tables = set(Base.metadata.tables)
    portal_tables = set(PortalBase.metadata.tables)
    assert app_tables.isdisjoint(portal_tables)
    assert "students" not in app_tables


def test_portal_engine_is_a_separate_engine():
    from app.core.database import engine as app_engine

    assert engine is not app_engine
    assert engine.url != app_engine.url


def test_portal_sql_is_never_echoed():
    """Portal rows carry student data; they must not land in the app log."""
    assert engine.echo is False


def test_get_portal_db_is_a_generator_dependency():
    assert callable(get_portal_db)


# --------------------------------------------------------------------------- #
# The students allow-list
# --------------------------------------------------------------------------- #
SECRET_COLUMNS = {
    "password",
    "forum_pass",
    "forum_access_token",
    "edmingle_api_key",
    "remember_token",
    "otp",
    "verification_otp",
    "tmp_verification_token",
    "tmp_verification_token_expire_at",
    "otp_expire_at",
    "forum_token_time",
}

PRIVATE_COLUMNS = {
    "date_of_birth",
    "father_name",
    "address",
    "pin_code",
    "phone",
    "gender",
    "id_image",
    "image",
    "linked_in_link",
}


def test_student_model_maps_no_credential_columns():
    from app.models.portal.student import Student

    mapped = set(Student.__table__.columns.keys())
    assert mapped.isdisjoint(SECRET_COLUMNS), mapped & SECRET_COLUMNS


def test_student_model_maps_no_unnecessary_personal_columns():
    from app.models.portal.student import Student

    mapped = set(Student.__table__.columns.keys())
    assert mapped.isdisjoint(PRIVATE_COLUMNS), mapped & PRIVATE_COLUMNS


def test_student_schema_cannot_leak_unmapped_fields():
    from app.models.portal.student import Student
    from app.schemas.student import StudentOut

    assert set(StudentOut.model_fields) <= set(Student.__table__.columns.keys())


def test_student_repr_carries_no_personal_data():
    from app.models.portal.student import Student

    student = Student(id=1, reg_code="RC1", full_name="A Name", email="a@b.com")
    rendered = repr(student)
    assert "A Name" not in rendered and "a@b.com" not in rendered


def test_search_results_are_capped():
    from app.services import portal_service

    assert portal_service.MAX_RESULTS <= 25


# --------------------------------------------------------------------------- #
# Tool dispatch
# --------------------------------------------------------------------------- #
def test_unknown_tool_is_refused_without_touching_the_database():
    from app.services import ai_tools

    result = ai_tools.dispatch("drop_everything", {"x": 1})
    assert "error" in result and "Unknown tool" in result["error"]


def test_bad_arguments_are_returned_as_data_not_raised():
    from app.services import ai_tools

    result = ai_tools.dispatch("count_students", {"unexpected": True})
    assert "error" in result


def test_declared_tools_all_have_handlers():
    from app.services import ai_tools

    declared = {f.name for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    assert declared == set(ai_tools._HANDLERS)


def test_tool_rounds_are_bounded():
    from app.services import gemini_service

    # Bounded, but high enough for a four-to-five step student lookup.
    assert 1 <= gemini_service.MAX_TOOL_ROUNDS <= 10


def test_invented_parameter_names_are_rejected_not_ignored():
    """A dropped filter would make an unfiltered count look like the answer."""
    from app.services import ai_tools

    result = ai_tools.dispatch("count_students", {"town": "Delhi"})
    assert "error" in result and "town" in result["error"]


def test_every_declared_parameter_is_accepted():
    from app.services import ai_tools

    for declaration in ai_tools.TOOL_DECLARATIONS.function_declarations:
        declared = set((declaration.parameters.properties or {}).keys())
        assert declared == ai_tools._DECLARED_PARAMS[declaration.name]


def test_filters_are_shared_between_search_and_count():
    """Drift here would make a count disagree with the list it summarises."""
    import inspect

    from app.services import portal_service

    params = set(inspect.signature(portal_service.build_filters).parameters)
    assert {"city", "state", "country", "status", "never_logged_in"} <= params


def test_bad_iso_date_is_ignored_rather_than_raising():
    from app.services.ai_tools import _parse_date

    assert _parse_date("not-a-date") is None
    assert _parse_date("2026-01-15") is not None
