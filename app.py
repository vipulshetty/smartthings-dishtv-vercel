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

# Desired delay after the TV ON/ONLINE event.
DELAY_SECONDS = int(
    os.getenv("DISHTV_DELAY_SECONDS", "20")
)

# Existing Personal Access Token.
# We keep using this for the actual HDMI2 command for now.
ST_ACCESS_TOKEN = os.getenv(
    "ST_ACCESS_TOKEN",
    "",
).strip()

# OAuth-In App credentials.
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


# ============================================================
# RUNTIME STATUS
# ============================================================

last_event = {
    "status": "idle"
}


# ============================================================
# PAT AUTHENTICATION
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

def build_oauth_url(state: str):
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
        "state": state,
    }

    return (
        "https://api.smartthings.com/v1/oauth/authorize?"
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
        "https://api.smartthings.com/v1/oauth/token",
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

    if not response.ok:
        raise RuntimeError(
            f"SmartThings OAuth token exchange failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

    return response.json()


# ============================================================
# DEVICE SUBSCRIPTION
# ============================================================

def create_device_subscription(
    access_token: str,
    installed_app_id: str,
):
    body = {
        "sourceType": "DEVICE",
        "device": {
            "deviceId": DEVICE_ID
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

    # Already exists = okay.
    if response.status_code == 409:
        return {
            "already_exists": True,
            "detail": response.text,
        }

    if not response.ok:
        raise RuntimeError(
            f"Device subscription failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

    return response.json()


# ============================================================
# HEALTH SUBSCRIPTION
# ============================================================

def create_health_subscription(
    access_token: str,
    installed_app_id: str,
):
    """
    Subscribe to health events for the location.

    We intentionally use locationId here rather than
    mixing locationId and deviceIds.
    """

    body = {
        "sourceType": "DEVICE_HEALTH",
        "deviceHealth": {
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

    # Already exists = okay.
    if response.status_code == 409:
        return {
            "already_exists": True,
            "detail": response.text,
        }

    if not response.ok:
        raise RuntimeError(
            f"Health subscription failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

    return response.json()


# ============================================================
# TV COMMAND
# ============================================================

def set_dishtv_source():
    """
    Switch the Samsung TV to HDMI2.
    HDMI2 is the DishTV input.
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

    if not response.ok:
        raise RuntimeError(
            f"TV command failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

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

    if not response.ok:
        raise RuntimeError(
            f"TV status failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

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

    if not response.ok:
        raise RuntimeError(
            f"TV health check failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

    return response.json()


# ============================================================
# HOME / HEALTH
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
# MANUAL SOURCE TEST
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
# OAUTH START
# ============================================================

@app.get("/oauth/start")
def oauth_start():
    try:
        # Generate state for THIS browser session.
        state = secrets.token_urlsafe(32)

        url = build_oauth_url(state)

        response = RedirectResponse(
            url,
            status_code=302,
        )

        # Store state in the browser rather than a server
        # process variable. This works correctly with Vercel's
        # serverless execution model.
        response.set_cookie(
            key="st_oauth_state",
            value=state,
            max_age=600,
            httponly=True,
            secure=True,
            samesite="lax",
        )

        return response

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
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    # SmartThings returned an OAuth error.
    if error:
        return JSONResponse(
            {
                "ok": False,
                "error": error,
            },
            status_code=400,
        )

    # Get the original state from the browser cookie.
    stored_state = request.cookies.get(
        "st_oauth_state"
    )

    if not state:
        return JSONResponse(
            {
                "ok": False,
                "error": "missing OAuth state",
            },
            status_code=400,
        )

    if not stored_state:
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    "OAuth state cookie is missing. "
                    "Start again from /oauth/start."
                ),
            },
            status_code=400,
        )

    if state != stored_state:
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
        # Exchange authorization code for OAuth tokens.
        tokens = exchange_code(code)

        installed_app_id = tokens.get(
            "installed_app_id"
        )

        access_token = tokens.get(
            "access_token"
        )

        if not installed_app_id:
            raise RuntimeError(
                "SmartThings did not return installed_app_id"
            )

        if not access_token:
            raise RuntimeError(
                "SmartThings did not return access_token"
            )

        # Create normal device subscription.
        device_subscription = (
            create_device_subscription(
                access_token,
                installed_app_id,
            )
        )

        # Create ONLINE/OFFLINE health subscription.
        health_subscription = (
            create_health_subscription(
                access_token,
                installed_app_id,
            )
        )

        response = JSONResponse(
            {
                "ok": True,
                "installed_app_id": installed_app_id,
                "device_subscription":
                    device_subscription,
                "health_subscription":
                    health_subscription,
                "message": (
                    "SmartThings connected. "
                    "Device and health subscriptions "
                    "are configured."
                ),
            }
        )

        # State is no longer needed.
        response.delete_cookie(
            "st_oauth_state"
        )

        return response

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
    # SmartThings target URL confirmation handshake
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

    switch_on = False
    health_online = False

    # --------------------------------------------------------
    # Inspect all events in the webhook payload.
    # --------------------------------------------------------

    for event in events:

        event_type = event.get(
            "eventType"
        )

        # ====================================================
        # DEVICE_EVENT
        # ====================================================

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
                switch_on = True

        # ====================================================
        # DEVICE_HEALTH_EVENT
        # ====================================================

        elif event_type == "DEVICE_HEALTH_EVENT":

            health_event = event.get(
                "deviceHealthEvent",
                {}
            )

            if (
                health_event.get("deviceId")
                == DEVICE_ID

                and health_event.get("status")
                == "ONLINE"
            ):
                health_online = True

    # --------------------------------------------------------
    # Nothing relevant.
    # --------------------------------------------------------

    if not switch_on and not health_online:

        return JSONResponse(
            {
                "ok": True,
                "matched": False,
            }
        )

    # --------------------------------------------------------
    # Record what triggered us.
    # --------------------------------------------------------

    trigger = []

    if switch_on:
        trigger.append("switch_on")

    if health_online:
        trigger.append("health_online")

    last_event.clear()

    last_event.update(
        {
            "status": "trigger_received",
            "trigger": trigger,
        }
    )

    # --------------------------------------------------------
    # Wait 20 seconds.
    # --------------------------------------------------------

    time.sleep(DELAY_SECONDS)

    try:

        # ----------------------------------------------------
        # Make sure the TV is reachable.
        # ----------------------------------------------------

        health = get_health()

        # If still offline, give it another 5 seconds.
        if health.get("state") != "ONLINE":

            time.sleep(5)

            health = get_health()

        # ----------------------------------------------------
        # Read source before command.
        # ----------------------------------------------------

        before = get_source()

        # ----------------------------------------------------
        # Send HDMI2.
        # ----------------------------------------------------

        first_result = set_dishtv_source()

        # Give the TV a moment to process it.
        time.sleep(2)

        # ----------------------------------------------------
        # Verify.
        # ----------------------------------------------------

        after = get_source()

        # ----------------------------------------------------
        # Retry once if HDMI2 did not stick.
        # ----------------------------------------------------

        result = first_result

        if after != "HDMI2":

            time.sleep(3)

            retry_result = set_dishtv_source()

            time.sleep(2)

            after = get_source()

            result = {
                "first_attempt": first_result,
                "retry": retry_result,
            }

        # ----------------------------------------------------
        # Store final state.
        # ----------------------------------------------------

        if after == "HDMI2":

            final_status = "source_set"

        else:

            final_status = "source_not_changed"

        last_event.clear()

        last_event.update(
            {
                "status": final_status,
                "trigger": trigger,
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
                "trigger": trigger,
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
                "trigger": trigger,
                "error": str(exc),
            }
        )

        return JSONResponse(
            {
                "ok": False,
                "matched": True,
                "trigger": trigger,
                "error": str(exc),
            },
            status_code=502,
        )
