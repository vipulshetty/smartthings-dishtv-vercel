import os
import time
import hmac
import hashlib
import base64
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse

app = FastAPI(title="SmartThings DishTV Auto-Source")

ST_BASE = "https://api.smartthings.com/v1"
DEVICE_ID = os.getenv("ST_DEVICE_ID", "84a47e06-88fa-db59-e9aa-2764d5f5c420")
LOCATION_ID = os.getenv("ST_LOCATION_ID", "22fde621-3b05-442e-961b-2ca8c5b67574")
DELAY_SECONDS = int(os.getenv("DISHTV_DELAY_SECONDS", "20"))

# Simplest free/personal deployment: use the existing SmartThings PAT.
# If the PAT expires, replace it in Vercel Environment Variables.
ST_ACCESS_TOKEN = os.getenv("ST_ACCESS_TOKEN", "")

# Optional OAuth/API Access App variables. Not required for the first test.
CLIENT_ID = os.getenv("ST_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("ST_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("ST_REDIRECT_URI", "")

last_event = {"status": "idle"}


def token():
    if not ST_ACCESS_TOKEN:
        raise RuntimeError("ST_ACCESS_TOKEN is not configured")
    return ST_ACCESS_TOKEN


def st_headers():
    return {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/json",
    }


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
        headers={**st_headers(), "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    r.raise_for_status()
    return r.json() if r.content else {"ok": True}


def get_source():
    r = requests.get(
        f"{ST_BASE}/devices/{DEVICE_ID}/status",
        headers=st_headers(),
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
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "device_id": DEVICE_ID,
        "delay_seconds": DELAY_SECONDS,
        "last_event": last_event,
    }


@app.get("/test-source")
def test_source():
    try:
        result = set_dishtv_source()
        return {"ok": True, "source_command": result}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post("/")
async def webhook(request: Request):
    payload = await request.json()

    # SmartThings Target URL confirmation.
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

    # Keep the webhook request alive for the short boot delay.
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
