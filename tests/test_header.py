from tests.test_auth import _csrf, _join, admin_user, client  # noqa: F401  reuse fixtures


def _login_admin(client, admin_user) -> None:
    client.post(
        "/login/",
        data={"email": admin_user.email, "password": "password1234", "csrf_token": _csrf(client)},
    )


def test_admin_sees_admin_panel_button(client, admin_user):
    _login_admin(client, admin_user)

    html = client.get("/").text

    assert 'href="/admin-panel/"' in html
    assert "Admin panel" in html
    assert 'href="/login/"' not in html


def test_regular_user_does_not_see_admin_panel_button(client):
    _join(client, "reader-2026@example.com", "securepassword123", "Reader")

    html = client.get("/").text

    # Registration no longer signs the user in, so the header shows the public
    # login link — what matters is that no admin panel entry appears.
    assert 'href="/admin-panel/"' not in html
    assert "Admin panel" not in html


def test_anonymous_visitor_sees_login_not_admin_panel(client):
    html = client.get("/").text

    assert 'href="/login/"' in html
    assert 'href="/admin-panel/"' not in html
