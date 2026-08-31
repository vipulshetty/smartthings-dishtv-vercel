import os
import time
import base64
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse

app = FastAPI(title="SmartThings DishTV Auto-Source")

ST_BASE = "https://api.smartthings.com/v1"
DEVICE_ID = os.getenv("ST_DEVICE_ID", "84a47e06-88fa-db59-e9aa-2764d5f5c420").strip()
LOCATION_ID = os.getenv("ST_LOCATION_ID", "22fde621-3b05-442e-961b-2ca8c5b67574").strip()
DELAY_SECONDS = int(os.getenv("DISHTV_DELAY_SECONDS", "20"))

# Keep the already-working PAT for device commands for now.
ST_ACCESS_TOKEN = os.getenv("ST_ACCESS_TOKEN", "").strip()

# OAuth-In SmartApp credentials. Used for authorization + subscription setup.
CLIENT_ID = os.getenv("ST_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("ST_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("ST_REDIRECT_URI", "").strip()

last_event = {"status": "idle"}


def pat_headers():
    if not ST_ACCESS_TOKEN:
        raise RuntimeError("ST_ACCESS_TOKEN is not configured")
    return {
        "Authorization": f"Bearer {ST_ACCESS_TOKEN}",
        "Accept": "application/json",
    }


def oauth_start_url():
    if not CLIENT_ID or not REDIRECT_URI:
        raise RuntimeError("ST_CLIENT_ID and ST_REDIRECT_URI must be configured")
    params = {
        "client_id": CLIENT_ID,
        "scope": "r:devices:* x:devices:*",
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
    }
    return "https://api.smartthings.com/v1/oauth/authorize?" + urlencode(params)


def exchange_code(code: str):
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        raise RuntimeError("OAuth environment variables are incomplete")
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        f"{ST_BASE}/oauth/token",
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def create_device_subscription(access_token: str, installed_app_id: str):
    body = {
        "sourceType": "DEVICE",
        "device": {"deviceId": DEVICE_ID},
        "subscriptionName": "dishtvBootHandler",
    }
    r = requests.post(
        f"{ST_BASE}/installedapps/{installed_app_id}/subscriptions",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15,
    )
    if r.status_code == 409:
        return {"already_exists": True, "detail": r.text}
    r.raise_for_status()
    return r.json()


def set_dishtv_source():
    body = {
        "commands": [{
            "component": "main",
            "capability": "samsungvd.mediaInputSource",
            "command": "setInputSource",
            "arguments": ["HDMI2"],
        }]
    }
    r = requests.post(
        f"{ST_BASE}/devices/{DEVICE_ID}/commands",
        headers={**pat_headers(), "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    r.raise_for_status()
    return r.json() if r.content else {"ok": True}


def get_source():
    r = requests.get(
        f"{ST_BASE}/devices/{DEVICE_ID}/status",
        headers=pat_headers(),
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return (
        data.get("components", {})
        .get("main", {})
        .get("samsungvd.mediaInputSource", {})
        .get("inputSource", {})
        .get("value")
    )


@app.get("/")
def home():
    return HTMLResponse(
        "<h2>SmartThings → DishTV</h2>"
        "<p>Service is running.</p>"
        "<p><a href='/health'>Health</a></p>"
        "<p><a href='/oauth/start'>Connect SmartThings</a></p>"
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "device_id": DEVICE_ID,
        "delay_seconds": DELAY_SECONDS,
        "oauth_configured": bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI),
        "last_event": last_event,
    }


@app.get("/test-source")
def test_source():
    try:
        result = set_dishtv_source()
        return {"ok": True, "source_command": result}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get("/oauth/start")
def oauth_start():
    try:
        return RedirectResponse(oauth_start_url(), status_code=302)
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/oauth/callback")
def oauth_callback(code: str | None = None, error: str | None = None):
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    if not code:
        return JSONResponse({"ok": False, "error": "missing authorization code"}, status_code=400)
    try:
        tokens = exchange_code(code)
        installed_app_id = tokens.get("installed_app_id")
        if not installed_app_id:
            raise RuntimeError("SmartThings did not return installed_app_id")
        subscription = create_device_subscription(tokens["access_token"], installed_app_id)
        return JSONResponse({
            "ok": True,
            "installed_app_id": installed_app_id,
            "subscription": subscription,
            "message": "SmartThings is connected. You can close this page."
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.post("/")
async def webhook(request: Request):
    payload = await request.json()

    # SmartThings target URL confirmation handshake.
    if payload.get("messageType") == "CONFIRMATION":
        confirmation_url = payload.get("confirmationData", {}).get("confirmationUrl")
        if confirmation_url:
            try:
                requests.get(confirmation_url, timeout=10)
            except requests.RequestException:
                pass
        return JSONResponse({"ok": True, "confirmed": bool(confirmation_url)})

    if payload.get("messageType") != "EVENT":
        return JSONResponse({"ok": True})

    events = payload.get("eventData", {}).get("events", [])
    matched = False
    for event in events:
        if event.get("eventType") != "DEVICE_EVENT":
            continue
        de = event.get("deviceEvent", {})
        if (
            de.get("deviceId") == DEVICE_ID
            and de.get("component") == "main"
            and de.get("capability") == "switch"
            and de.get("attribute") == "switch"
            and de.get("value") == "on"
        ):
            matched = True
            last_event.clear()
            last_event.update({
                "status": "received_on",
                "event_id": de.get("eventId"),
                "event_time": de.get("eventTime"),
            })

    if not matched:
        return JSONResponse({"ok": True, "matched": False})

    time.sleep(DELAY_SECONDS)
    try:
        before = get_source()
        result = set_dishtv_source()
        after = get_source()
        last_event.clear()
        last_event.update({
            "status": "source_set",
            "before": before,
            "after": after,
            "delay_seconds": DELAY_SECONDS,
        })
        return JSONResponse({"ok": True, "matched": True, "before": before, "after": after, "result": result})
    except Exception as exc:
        last_event.clear()
        last_event.update({"status": "command_failed", "error": str(exc)})
        return JSONResponse({"ok": False, "matched": True, "error": str(exc)}, status_code=502)
