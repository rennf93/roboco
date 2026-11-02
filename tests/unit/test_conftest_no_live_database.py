"""No test reaches the app's configured database.

``settings.database_url`` is what production code opens (``get_engine()``,
``get_db_context()``, the cloud-auth login router). Left at its default it
is ``localhost:5432/roboco`` (a developer's own database; on the NAS the live
one sits next to it on 15432): ``test_login_route_parses_oauth2_form_not_query_params``
built an engine from it and, depending on the host, failed on its auth or
wrote a ``users`` table into whatever answered. The suite now points the
app's database at a port that refuses instantly (``_no_live_database``,
autouse, as ``_no_live_redis`` does for Redis), and a test that needs the
real app machinery asks for ``app_settings_on_test_db``, which points it at
the throwaway test database.
"""

from __future__ import annotations

from roboco.config import settings
from sqlalchemy.engine import make_url

_REFUSING_PORT = 1


def test_the_app_database_points_nowhere_by_default() -> None:
    assert settings.database_host == "127.0.0.1"
    assert settings.database_port == _REFUSING_PORT


def test_app_settings_on_test_db_points_at_the_throwaway_database(
    app_settings_on_test_db: str,
) -> None:
    app, test = make_url(settings.database_url), make_url(app_settings_on_test_db)
    assert (app.host, app.port, app.database) == (test.host, test.port, test.database)
    assert settings.database_name.startswith("roboco_test_")
