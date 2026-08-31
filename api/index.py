import os
import time
import base64
import secrets
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse

app = FastAPI(title="SmartThings DishTV Auto-Source")

# ------------------------------------------------------------
# SmartThings
# ------------------------------------------------------------

ST_API_BASE = "https://api.smartthings.com/v1"

DEVICE_ID = os.getenv(
    "ST_DEVICE_ID",
    "84a47e06-88fa-db59-e9aa-2764d5f5c420",
).strip()

LOCATION_ID = os.getenv(
    "ST_LOCATION_ID",
    "22fde621-3b05-442e-961b-2ca8c5b67574",
).strip()

# 20 seconds is our target.
DELAY_SECONDS = int(
    os.getenv("DISHTV_DELAY_SECONDS", "20")
)

# Existing PAT - keep this for the actual TV command for now.
ST_ACCESS_TOKEN = os.getenv(
    "ST_ACCESS_TOKEN",
    "",
).strip()

# OAuth-In SmartApp credentials.
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


# ------------------------------------------------------------
# OAuth state
# ------------------------------------------------------------

# For this single-user setup we only need one setup state.
# If ST_OAUTH_STATE is not supplied, generate one.
OAUTH_STATE = os.getenv(
    "ST_OAUTH_STATE",
    "",
).strip()

if not OAUTH_STATE:
    OAUTH_STATE = secrets.token_urlsafe(32)


# ------------------------------------------------------------
# Runtime status
# ------------------------------------------------------------

last_event = {
    "status": "idle"
}


# ------------------------------------------------------------
# Authentication helpers
# ------------------------------------------------------------

def pat_headers():
    """
    Headers for the existing Personal Access Token.

    We are intentionally keeping the PAT for the actual device
    command for now, as requested.
    """
    if not ST_ACCESS_TOKEN:
        raise RuntimeError(
            "ST_ACCESS_TOKEN is not configured"
        )

    return {
        "Authorization": f"Bearer {ST_ACCESS_TOKEN}",
        "Accept": "application/json",
    }


# ------------------------------------------------------------
# OAuth
# ------------------------------------------------------------

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

        # Keep the scopes limited to what we need.
        "scope": "r:devices:* x:devices:*",

        # Required for CSRF protection.
        "state": OAUTH_STATE,
    }

    # SmartThings Quick Start currently documents this endpoint
    # without /v1.
    return (
        "https://api.smartthings.com/oauth/authorize?"
        + urlencode(params)
    )


def exchange_code(code: str):
    """
    Exchange authorization code for SmartThings tokens.
    """
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


# ------------------------------------------------------------
# SmartThings subscription
# ------------------------------------------------------------

def create_device_subscription(
    access_token: str,
    installed_app_id: str,
):
    """
    Subscribe this installed SmartThings app to all events
    from the TV device.

    We filter for:
      deviceId == our TV
      component == main
      capability == switch
      attribute == switch
      value == on

    inside the webhook handler.

    SmartThings documents DEVICE subscriptions as:
      sourceType = DEVICE
      device.deviceId = <device>
    """

    body = {
        "sourceType": "DEVICE",
        "device": {
            "deviceId": DEVICE_ID,
        },
        "subscriptionName": "dishtvBootHandler",
    }

    response = requests.post(
        (
            f"{ST_API_BASE}/installedapps/"
            f"{installed_app_id}/subscriptions"
        ),
        headers={
            "Authorization": (
                f"Bearer {access_token}"
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15,
    )

    # If we already created the subscription,
    # don't treat that as fatal.
    if response.status_code == 409:
        return {
            "already_exists": True,
            "detail": response.text,
        }

    response.raise_for_status()

    return response.json()


# ------------------------------------------------------------
# Device control
# ------------------------------------------------------------

def set_dishtv_source():
    """
    Directly switch the Samsung TV to HDMI2.

    HDMI2 is your DishTV input.
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

    if response.content:
        return response.json()

    return {
        "ok": True
    }


def get_source():
    """
    Read the current TV input source.
    """

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
    """
    Check whether SmartThings currently considers
    the TV ONLINE or OFFLINE.
    """

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


# ------------------------------------------------------------
# Web pages / health
# ------------------------------------------------------------

@app.get("/")
def home():
    return HTMLResponse(
        """
        <h2>SmartThings → DishTV</h2>

        <p>Service is running.</p>

        <p>
            <a href="/health">Health</a>
        </p>

        <p>
            <a href="/oauth/start">
                Connect SmartThings
            </a>
        </p>

        <p>
            <a href="/test-source">
                Test HDMI2
            </a>
        </p>
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


# ------------------------------------------------------------
# Manual source test
# ------------------------------------------------------------

@app.get("/test-source")
def test_source():
    """
    Manual test endpoint.

    This uses the SAME direct SmartThings command
    that worked from your PowerShell terminal.
    """

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


# ------------------------------------------------------------
# OAuth start
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# OAuth callback
# ------------------------------------------------------------

@app.get("/oauth/callback")
def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """
    SmartThings returns:
      ?code=...
      ?state=...

    We validate state before exchanging the code.
    """

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
                "error": (
                    "missing authorization code"
                ),
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

        # Create the event subscription.
        subscription = create_device_subscription(
            tokens["access_token"],
            installed_app_id,
        )

        return JSONResponse(
            {
                "ok": True,
                "installed_app_id": installed_app_id,
                "subscription": subscription,
                "message": (
                    "SmartThings is connected. "
                    "The TV event subscription "
                    "has been created."
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


# ------------------------------------------------------------
# SmartThings webhook
# ------------------------------------------------------------

@app.post("/")
async def webhook(request: Request):
    """
    Receives SmartThings webhook events.

    We specifically react to:
        device = our TV
        component = main
        capability = switch
        attribute = switch
        value = on
        stateChange = true
    """

    payload = await request.json()

    # --------------------------------------------------------
    # SmartThings target URL confirmation
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
                # The webhook should still return 200.
                pass

        return JSONResponse(
            {
                "ok": True,
                "confirmed": bool(
                    confirmation_url
                ),
            }
        )

    # --------------------------------------------------------
    # Ignore non-event messages
    # --------------------------------------------------------

    if payload.get("messageType") != "EVENT":
        return JSONResponse(
            {
                "ok": True
            }
        )

    events = (
        payload
        .get("eventData", {})
        .get("events", [])
    )

    matched = False

    for event in events:

        if event.get("eventType") != "DEVICE_EVENT":
            continue

        device_event = event.get(
            "deviceEvent",
            {}
        )

        component_id = device_event.get(
            "componentId",
            device_event.get("component"),
        )

        state_change = device_event.get(
            "stateChange",
            False,
        )

        # ----------------------------------------------------
        # The exact TV ON event we want.
        # ----------------------------------------------------

        if (
            device_event.get("deviceId")
            == DEVICE_ID
            and component_id == "main"
            and device_event.get("capability")
            == "switch"
            and device_event.get("attribute")
            == "switch"
            and device_event.get("value")
            == "on"
            and state_change
        ):

            matched = True

            last_event.clear()

            last_event.update(
                {
                    "status": "received_on",
                    "event_id": device_event.get(
                        "eventId"
                    ),
                    "event_time": device_event.get(
                        "eventTime"
                    ),
                }
            )

    # --------------------------------------------------------
    # Not our event.
    # --------------------------------------------------------

    if not matched:
        return JSONResponse(
            {
                "ok": True,
                "matched": False,
            }
        )

    # --------------------------------------------------------
    # Wait 20 seconds so the TV finishes booting.
    # --------------------------------------------------------

    time.sleep(DELAY_SECONDS)

    try:
        # Check health before trying the command.
        health = get_health()

        # If SmartThings still reports OFFLINE,
        # give it another 5 seconds.
        if health.get("state") != "ONLINE":
            time.sleep(5)

            health = get_health()

        # Read source before switching.
        before = get_source()

        # Send HDMI2.
        result = set_dishtv_source()

        # Read source after switching.
        after = get_source()

        last_event.clear()

        last_event.update(
            {
                "status": "source_set",
                "before": before,
                "after": after,
                "health": health.get("state"),
                "delay_seconds": DELAY_SECONDS,
            }
        )

        return JSONResponse(
            {
                "ok": True,
                "matched": True,
                "before": before,
                "after": after,
                "health": health.get("state"),
                "result": result,
            }
        )

    except Exception as exc:

        last_event.clear()

        last_event.update(
            {
                "status": "command_failed",
                "error": str(exc),
            }
        )

        return JSONResponse(
            {
                "ok": False,
                "matched": True,
                "error": str(exc),
            },
            status_code=502,
        )
