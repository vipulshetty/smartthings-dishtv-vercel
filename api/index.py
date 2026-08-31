@app.post("/")
async def webhook(request: Request):
    payload = await request.json()

    # SmartThings target URL confirmation
    if payload.get("messageType") == "CONFIRMATION":
        confirmation_url = (
            payload.get("confirmationData", {})
                   .get("confirmationUrl")
        )

        if confirmation_url:
            try:
                requests.get(confirmation_url, timeout=10)
            except requests.RequestException:
                pass

        return JSONResponse({
            "ok": True,
            "confirmed": bool(confirmation_url)
        })

    if payload.get("messageType") != "EVENT":
        return JSONResponse({"ok": True})

    events = (
        payload.get("eventData", {})
               .get("events", [])
    )

    matched = False

    for event in events:
        if event.get("eventType") != "DEVICE_EVENT":
            continue

        de = event.get("deviceEvent", {})
        component = de.get(
            "componentId",
            de.get("component")
        )

        if (
            de.get("deviceId") == DEVICE_ID
            and component == "main"
            and de.get("capability") == "switch"
            and de.get("attribute") == "switch"
            and de.get("value") == "on"
            and de.get("stateChange", False)
        ):
            matched = True

            last_event.clear()
            last_event.update({
                "status": "received_on",
                "event_id": de.get("eventId"),
                "event_time": de.get("eventTime"),
            })

    if not matched:
        return JSONResponse({
            "ok": True,
            "matched": False
        })

    # Give the TV time to finish booting.
    time.sleep(DELAY_SECONDS)

    try:
        # First check connectivity.
        health = get_health()

        # If SmartThings still sees the TV as offline,
        # give it another 5 seconds.
        if health.get("state") != "ONLINE":
            time.sleep(5)
            health = get_health()

        before = get_source()

        # First attempt.
        result = set_dishtv_source()

        # Give the TV a moment to process the command.
        time.sleep(2)

        after = get_source()

        # One retry if the source didn't change.
        if after != "HDMI2":
            time.sleep(3)

            retry_result = set_dishtv_source()

            time.sleep(2)

            after = get_source()

            result = {
                "first_attempt": result,
                "retry": retry_result
            }

        last_event.clear()
        last_event.update({
            "status": (
                "source_set"
                if after == "HDMI2"
                else "source_not_changed"
            ),
            "before": before,
            "after": after,
            "health": health.get("state"),
            "delay_seconds": DELAY_SECONDS,
        })

        return JSONResponse({
            "ok": after == "HDMI2",
            "matched": True,
            "before": before,
            "after": after,
            "health": health.get("state"),
            "result": result,
        })

    except Exception as exc:

        last_event.clear()
        last_event.update({
            "status": "command_failed",
            "error": str(exc)
        })

        return JSONResponse(
            {
                "ok": False,
                "matched": True,
                "error": str(exc),
            },
            status_code=502,
        )
