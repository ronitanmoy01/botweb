from tests.test_auth import _extract_csrf, admin_user, client  # noqa: F401  reuse fixtures


def _login_admin(client) -> None:
    csrf = _extract_csrf(client.get("/login/").text)
    client.post(
        "/login/",
        data={"email": "admin@example.com", "password": "password1234", "csrf_token": csrf},
    )


def test_admin_sees_admin_panel_button(client, admin_user):
    _login_admin(client)

    html = client.get("/").text

    assert 'href="/admin-panel/"' in html
    assert "Admin panel" in html
    assert 'href="/login/"' not in html


def test_regular_user_does_not_see_admin_panel_button(client):
    csrf = _extract_csrf(client.get("/register/").text)
    client.post(
        "/register/",
        data={
            "email": "reader@example.com",
            "password": "securepassword123",
            "display_name": "Reader",
            "csrf_token": csrf,
        },
    )

    html = client.get("/").text

    assert 'href="/admin-panel/"' not in html
    assert 'href="/login/"' not in html


def test_anonymous_visitor_sees_login_not_admin_panel(client):
    html = client.get("/").text

    assert 'href="/login/"' in html
    assert 'href="/admin-panel/"' not in html
