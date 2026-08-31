import os
import time
import base64
import secrets
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse

app = FastAPI(title="SmartThings DishTV Auto-Source")

# ============================================================
# CONFIG
# ============================================================

ST_API_BASE = "https://api.smartthings.com/v1"

DEVICE_ID = os.getenv(
    "ST_DEVICE_ID",
    "84a47e06-88fa-db59-e9aa-2764d5f5c420",
).strip()

LOCATION_ID = os.getenv(
    "ST_LOCATION_ID",
    "22fde621-3b05-442e-961b-2ca8c5b67574",
).strip()

DELAY_SECONDS = int(
    os.getenv("DISHTV_DELAY_SECONDS", "20")
)

# Existing PAT - keep this for the actual TV command.
ST_ACCESS_TOKEN = os.getenv(
    "ST_ACCESS_TOKEN",
    "",
).strip()

# OAuth-In App
CLIENT_ID = os.getenv(
    "ST_CLIENT_ID",
    "",
).strip()

CLIENT_SECRET = os.getenv(
    "ST_CLIENT_SECRET",
    "",
).strip()

REDIRECT_URI = os.getenv(
    "ST_REDIRECT_URI",
    "",
).strip()

# Persistent value supplied through Vercel.
OAUTH_STATE = os.getenv(
    "ST_OAUTH_STATE",
    "",
).strip()

if not OAUTH_STATE:
    OAUTH_STATE = secrets.token_urlsafe(32)

last_event = {
    "status": "idle"
}


# ============================================================
# PAT AUTH
# ============================================================

def pat_headers():
    if not ST_ACCESS_TOKEN:
        raise RuntimeError(
            "ST_ACCESS_TOKEN is not configured"
        )

    return {
        "Authorization": f"Bearer {ST_ACCESS_TOKEN}",
        "Accept": "application/json",
    }


# ============================================================
# OAUTH
# ============================================================

def oauth_start_url():
    if not CLIENT_ID:
        raise RuntimeError(
            "ST_CLIENT_ID is not configured"
        )

    if not REDIRECT_URI:
        raise RuntimeError(
            "ST_REDIRECT_URI is not configured"
        )

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "r:devices:* x:devices:*",
        "state": OAUTH_STATE,
    }

    return (
        "https://api.smartthings.com/oauth/authorize?"
        + urlencode(params)
    )


def exchange_code(code: str):
    if not CLIENT_ID:
        raise RuntimeError(
            "ST_CLIENT_ID is not configured"
        )

    if not CLIENT_SECRET:
        raise RuntimeError(
            "ST_CLIENT_SECRET is not configured"
        )

    if not REDIRECT_URI:
        raise RuntimeError(
            "ST_REDIRECT_URI is not configured"
        )

    basic_auth = base64.b64encode(
        f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")
    ).decode("ascii")

    response = requests.post(
        "https://api.smartthings.com/oauth/token",
        headers={
            "Authorization": f"Basic {basic_auth}",
            "Accept": "application/json",
            "Content-Type": (
                "application/x-www-form-urlencoded"
            ),
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=15,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# SUBSCRIPTIONS
# ============================================================

def create_device_subscription(
    access_token: str,
    installed_app_id: str,
):
    """
    Receive device capability events for this TV.
    """

    body = {
        "sourceType": "DEVICE",
        "device": {
            "deviceId": DEVICE_ID,
        },
        "subscriptionName": "dishtvDeviceEvents",
    }

    response = requests.post(
        (
            f"{ST_API_BASE}/installedapps/"
            f"{installed_app_id}/subscriptions"
        ),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15,
    )

    if response.status_code == 409:
        return {
            "already_exists": True,
            "detail": response.text,
        }

    response.raise_for_status()

    return response.json()


def create_health_subscription(
    access_token: str,
    installed_app_id: str,
):
    """
    Receive ONLINE/OFFLINE health events for this TV.

    DEVICE_HEALTH subscriptions are officially supported by
    SmartThings.
    """

    body = {
        "sourceType": "DEVICE_HEALTH",
        "deviceHealth": {
            "deviceIds": [
                DEVICE_ID
            ],
            "locationId": LOCATION_ID,
            "subscriptionName": "dishtvHealthEvents",
        },
    }

    response = requests.post(
        (
            f"{ST_API_BASE}/installedapps/"
            f"{installed_app_id}/subscriptions"
        ),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15,
    )

    if response.status_code == 409:
        return {
            "already_exists": True,
            "detail": response.text,
        }

    response.raise_for_status()

    return response.json()


# ============================================================
# TV COMMAND
# ============================================================

def set_dishtv_source():
    """
    Switch Samsung TV to HDMI2.
    HDMI2 = DishTV.
    """

    body = {
        "commands": [
            {
                "component": "main",
                "capability": (
                    "samsungvd.mediaInputSource"
                ),
                "command": "setInputSource",
                "arguments": ["HDMI2"],
            }
        ]
    }

    response = requests.post(
        (
            f"{ST_API_BASE}/devices/"
            f"{DEVICE_ID}/commands"
        ),
        headers={
            **pat_headers(),
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15,
    )

    response.raise_for_status()

    return (
        response.json()
        if response.content
        else {"ok": True}
    )


def get_source():
    response = requests.get(
        (
            f"{ST_API_BASE}/devices/"
            f"{DEVICE_ID}/status"
        ),
        headers=pat_headers(),
        timeout=15,
    )

    response.raise_for_status()

    data = response.json()

    return (
        data
        .get("components", {})
        .get("main", {})
        .get("samsungvd.mediaInputSource", {})
        .get("inputSource", {})
        .get("value")
    )


def get_health():
    response = requests.get(
        (
            f"{ST_API_BASE}/devices/"
            f"{DEVICE_ID}/health"
        ),
        headers=pat_headers(),
        timeout=15,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# WEB
# ============================================================

@app.get("/")
def home():
    return HTMLResponse(
        """
        <h2>SmartThings → DishTV</h2>
        <p>Service is running.</p>
        <p><a href="/health">Health</a></p>
        <p><a href="/oauth/start">Connect SmartThings</a></p>
        <p><a href="/test-source">Test HDMI2</a></p>
        """
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "device_id": DEVICE_ID,
        "delay_seconds": DELAY_SECONDS,
        "oauth_configured": bool(
            CLIENT_ID
            and CLIENT_SECRET
            and REDIRECT_URI
        ),
        "last_event": last_event,
    }


# ============================================================
# MANUAL COMMAND TEST
# ============================================================

@app.get("/test-source")
def test_source():
    try:
        result = set_dishtv_source()

        return {
            "ok": True,
            "source_command": result,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        )


# ============================================================
# OAUTH
# ============================================================

@app.get("/oauth/start")
def oauth_start():
    try:
        return RedirectResponse(
            oauth_start_url(),
            status_code=302,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.get("/oauth/callback")
def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        return JSONResponse(
            {
                "ok": False,
                "error": error,
            },
            status_code=400,
        )

    if state != OAUTH_STATE:
        return JSONResponse(
            {
                "ok": False,
                "error": "invalid OAuth state",
            },
            status_code=400,
        )

    if not code:
        return JSONResponse(
            {
                "ok": False,
                "error": "missing authorization code",
            },
            status_code=400,
        )

    try:
        tokens = exchange_code(code)

        installed_app_id = tokens.get(
            "installed_app_id"
        )

        if not installed_app_id:
            raise RuntimeError(
                "SmartThings did not return installed_app_id"
            )

        # Create normal device subscription.
        device_subscription = (
            create_device_subscription(
                tokens["access_token"],
                installed_app_id,
            )
        )

        # Create health subscription.
        health_subscription = (
            create_health_subscription(
                tokens["access_token"],
                installed_app_id,
            )
        )

        return JSONResponse(
            {
                "ok": True,
                "installed_app_id": installed_app_id,
                "device_subscription":
                    device_subscription,
                "health_subscription":
                    health_subscription,
                "message": (
                    "SmartThings connected. "
                    "Device + health subscriptions "
                    "are configured."
                ),
            }
        )

    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "error": str(exc),
            },
            status_code=502,
        )


# ============================================================
# SMARTTHINGS WEBHOOK
# ============================================================

@app.post("/")
async def webhook(request: Request):
    payload = await request.json()

    # --------------------------------------------------------
    # Target URL confirmation
    # --------------------------------------------------------

    if payload.get("messageType") == "CONFIRMATION":

        confirmation_url = (
            payload
            .get("confirmationData", {})
            .get("confirmationUrl")
        )

        if confirmation_url:
            try:
                requests.get(
                    confirmation_url,
                    timeout=10,
                )
            except requests.RequestException:
                pass

        return JSONResponse(
            {
                "ok": True,
                "confirmed": bool(
                    confirmation_url
                ),
            }
        )

    if payload.get("messageType") != "EVENT":
        return JSONResponse(
            {"ok": True}
        )

    events = (
        payload
        .get("eventData", {})
        .get("events", [])
    )

    # ========================================================
    # FIND THE RELEVANT EVENT
    # ========================================================

    device_on_event = False
    health_online_event = False

    for event in events:

        event_type = event.get(
            "eventType"
        )

        # ----------------------------------------------------
        # Normal switch event
        # ----------------------------------------------------

        if event_type == "DEVICE_EVENT":

            de = event.get(
                "deviceEvent",
                {}
            )

            component_id = de.get(
                "componentId",
                de.get("component")
            )

            if (
                de.get("deviceId")
                == DEVICE_ID
                and component_id == "main"
                and de.get("capability")
                == "switch"
                and de.get("attribute")
                == "switch"
                and de.get("value")
                == "on"
                and de.get("stateChange", False)
            ):
                device_on_event = True

        # ----------------------------------------------------
        # Device health ONLINE event
        # ----------------------------------------------------

        elif event_type == "DEVICE_HEALTH_EVENT":

            he = event.get(
                "deviceHealthEvent",
                {}
            )

            if (
                he.get("deviceId")
                == DEVICE_ID
                and he.get("status")
                == "ONLINE"
            ):
                health_online_event = True

    # ========================================================
    # TRIGGER ON EITHER EVENT
    #
    # Normal ON event:
    #     switch -> on
    #
    # Wall power:
    #     health -> ONLINE
    #
    # Both are accepted.
    # ========================================================

    if not device_on_event and not health_online_event:
        return JSONResponse(
            {
                "ok": True,
                "matched": False,
            }
        )

    trigger_type = []

    if device_on_event:
        trigger_type.append("switch_on")

    if health_online_event:
        trigger_type.append("health_online")

    last_event.clear()

    last_event.update(
        {
            "status": "trigger_received",
            "trigger": trigger_type,
        }
    )

    # ========================================================
    # WAIT FOR TV BOOT
    # ========================================================

    time.sleep(DELAY_SECONDS)

    try:

        # ====================================================
        # CHECK TV HEALTH
        # ====================================================

        health = get_health()

        # Give SmartThings another 5 seconds if the TV
        # is not yet reachable.
        if health.get("state") != "ONLINE":

            time.sleep(5)

            health = get_health()

        # ====================================================
        # READ CURRENT SOURCE
        # ====================================================

        before = get_source()

        # ====================================================
        # SWITCH TO DISHTV
        # ====================================================

        result = set_dishtv_source()

        time.sleep(2)

        # ====================================================
        # VERIFY
        # ====================================================

        after = get_source()

        # ====================================================
        # RETRY IF NECESSARY
        # ====================================================

        if after != "HDMI2":

            time.sleep(3)

            retry_result = (
                set_dishtv_source()
            )

            time.sleep(2)

            after = get_source()

            result = {
                "first_attempt": result,
                "retry": retry_result,
            }

        # ====================================================
        # SAVE RESULT
        # ====================================================

        last_event.clear()

        last_event.update(
            {
                "status": (
                    "source_set"
                    if after == "HDMI2"
                    else "source_not_changed"
                ),
                "trigger": trigger_type,
                "before": before,
                "after": after,
                "health": health.get(
                    "state"
                ),
                "delay_seconds":
                    DELAY_SECONDS,
            }
        )

        return JSONResponse(
            {
                "ok": after == "HDMI2",
                "matched": True,
                "trigger": trigger_type,
                "before": before,
                "after": after,
                "health": health.get(
                    "state"
                ),
                "result": result,
            }
        )

    except Exception as exc:

        last_event.clear()

        last_event.update(
            {
                "status":
                    "command_failed",
                "trigger": trigger_type,
                "error": str(exc),
            }
        )

        return JSONResponse(
            {
                "ok": False,
                "matched": True,
                "trigger": trigger_type,
                "error": str(exc),
            },
            status_code=502,
        )
