"""Test helper: create real DRAGON accounts through the API and sign browser sessions in."""
import json, random, string, urllib.request

PASSWORD = "correct-horse-battery"

def make_account(base, name, password=PASSWORD):
    """Sign up a fresh account on the server at `base`; returns {token, user, email, password}."""
    tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    email = f"{name.lower().replace(' ', '.')}.{tag}@test.dragon"
    req = urllib.request.Request(base.rstrip("/") + "/api/signup", method="POST",
                                 data=json.dumps({"name": name, "email": email, "password": password}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    data["email"], data["password"] = email, password
    return data

def login_script(account):
    """Init script that stores the account's session in the browser (as "Keep me logged in" would)."""
    auth = json.dumps(json.dumps({"token": account["token"], "user": account["user"], "remember": True}))
    return ("if (window === window.top) { try { if (!localStorage.getItem('dragonAuth')) "
            f"localStorage.setItem('dragonAuth', {auth}); }} catch (e) {{}} }}")

def api(base, path, method="GET", token=None, body=None):
    """Call the account API; returns (status, json)."""
    headers = {"Content-Type": "application/json"}
    if token: headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base.rstrip("/") + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)
