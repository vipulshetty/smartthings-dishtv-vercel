# SmartThings → DishTV on Vercel

Personal serverless webhook for a Samsung TV.

Flow: SmartThings `switch=on` event → wait 20 seconds → direct `setInputSource("HDMI2")` → verify source.

## Environment variables

- `ST_ACCESS_TOKEN` — SmartThings token
- `ST_DEVICE_ID` — Samsung TV device ID
- `ST_LOCATION_ID` — SmartThings location ID
- `DISHTV_DELAY_SECONDS` — default `20`

## Deploy

Deploy this folder to Vercel. Then set the environment variables in Vercel Project Settings.

Webhook target URL:
`https://YOUR-APP.vercel.app/`

## Test

Open `/health` and `/test-source` after deployment.

Important: SmartThings webhook events must be configured for the API Access App and Target URL. The app must be confirmed by SmartThings before events are delivered.
