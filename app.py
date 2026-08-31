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
# CONFIGURATION
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

# Target delay: 20 seconds
DELAY_SECONDS = int(
    os.getenv("DISHTV_DELAY_SECONDS", "20")
)

# Existing Personal Access Token.
# We are keeping this for the actual TV command for now.
ST_ACCESS_TOKEN = os.getenv(
    "ST_ACCESS_TOKEN",
    "",
).strip()

# OAuth-In SmartApp
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

# OAuth state.
# For this single-user setup, one persistent value is sufficient.
OAUTH_STATE = os.getenv(
    "ST_OAUTH_STATE",
    "",
).strip()

if not OAUTH_STATE:
    OAUTH_STATE = secrets.token_urlsafe(32)

# Runtime information shown by /health
last_event = {
    "status": "idle"
}


# ============================================================
# AUTHENTICATION
# ============================================================

def pat_headers():
    """
    Headers for the existing SmartThings Personal Access Token.
    """
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
    """
    Build the SmartThings OAuth authorization URL.
    """

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
    """
    Exchange SmartThings OAuth authorization code
    for access/refresh tokens.
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


# ============================================================
# SMARTTHINGS SUBSCRIPTION
# ============================================================

def create_device_subscription(
    access_token: str,
    installed_app_id: str,
):
    """
    Subscribe the installed SmartThings app to events
    from our specific Samsung TV.

    We filter the events ourselves in the webhook handler.
    """

    body = {
        "sourceType": "DEVICE",
        "device": {
            "deviceId": DEVICE_ID
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

    # Subscription already exists
    if response.status_code == 409:
        return {
            "already_exists": True,
            "detail": response.text,
        }

    response.raise_for_status()

    return response.json()


# ============================================================
# TV CONTROL
# ============================================================

def set_dishtv_source():
    """
    Switch the Samsung TV to HDMI2.

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
    Get the TV's current input source.
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
    Get SmartThings connectivity state for the TV.
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


# ============================================================
# WEB PAGES
# ============================================================

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


# ============================================================
# MANUAL TEST
# ============================================================

@app.get("/test-source")
def test_source():
    """
    Manual test of the direct HDMI2 command.
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


# ============================================================
# OAUTH START
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


# ============================================================
# OAUTH CALLBACK
# ============================================================

@app.get("/oauth/callback")
def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """
    SmartThings returns:
      code
      state

    Validate state before exchanging code.
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
                    "SmartThings is connected and "
                    "the TV subscription was created."
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
    """
    Receives SmartThings webhook events.

    We react only to:

      our TV
      main component
      switch capability
      switch attribute
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
    # Ignore anything that isn't a device event
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

    # --------------------------------------------------------
    # Find TV switch ON event
    # --------------------------------------------------------

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
    # Not our event
    # --------------------------------------------------------

    if not matched:
        return JSONResponse(
            {
                "ok": True,
                "matched": False,
            }
        )

    # --------------------------------------------------------
    # Wait 20 seconds for TV boot
    # --------------------------------------------------------

    time.sleep(DELAY_SECONDS)

    try:

        # ----------------------------------------------------
        # Check connectivity
        # ----------------------------------------------------

        health = get_health()

        # Give the TV another 5 seconds if SmartThings
        # still reports it as offline.
        if health.get("state") != "ONLINE":

            time.sleep(5)

            health = get_health()

        # ----------------------------------------------------
        # Read source before command
        # ----------------------------------------------------

        before = get_source()

        # ----------------------------------------------------
        # Send HDMI2 command
        # ----------------------------------------------------

        result = set_dishtv_source()

        # Allow TV to process command
        time.sleep(2)

        # ----------------------------------------------------
        # Verify
        # ----------------------------------------------------

        after = get_source()

        # ----------------------------------------------------
        # Retry once if needed
        # ----------------------------------------------------

        if after != "HDMI2":

            time.sleep(3)

            retry_result = set_dishtv_source()

            time.sleep(2)

            after = get_source()

            result = {
                "first_attempt": result,
                "retry": retry_result,
            }

        # ----------------------------------------------------
        # Save status
        # ----------------------------------------------------

        last_event.clear()

        last_event.update(
            {
                "status": (
                    "source_set"
                    if after == "HDMI2"
                    else "source_not_changed"
                ),
                "before": before,
                "after": after,
                "health": health.get("state"),
                "delay_seconds": DELAY_SECONDS,
            }
        )

        return JSONResponse(
            {
                "ok": after == "HDMI2",
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
