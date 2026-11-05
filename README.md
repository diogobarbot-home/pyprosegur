[![PyPI version](https://badge.fury.io/py/pyprosegur.svg)](https://badge.fury.io/py/pyprosegur)

# PyProsegur
Python library to retrieve information from [Prosegur Residential Alarms](http://www.prosegur.pt)

## Requirements
- backoff
- aiohttp
- aiofile
- Click 

## Cameras in Google Home

Prosegur cameras only provide snapshots, while Google Home only shows cameras that
offer an H.264 stream. `pyprosegur serve` is a small bridge that keeps the latest
snapshot of every camera and re-serves it as MJPEG and (with `--hls`) as an H.264
HLS stream (requires `ffmpeg`).

```
python cli.py USER PASSWORD PT serve CONTRACT --hls --host 0.0.0.0 \
    --http-user bridge --http-password SECRET
```

| Endpoint | Content |
|---|---|
| `/cameras` | JSON list of cameras and their URLs |
| `/camera/<id>/snapshot.jpg` | latest image |
| `/camera/<id>/stream.mjpg` | MJPEG stream |
| `/camera/<id>/hls/index.m3u8` | H.264 HLS stream (`--hls`) |
| `/health` | last refresh / error per camera |

Options: `--refresh` (seconds between images, default 300), `--no-request` (only fetch
the last stored image, never trigger the camera), `--port` (default 8090).

Then, in Home Assistant:

1. *Settings → Devices & services → Add integration → Generic Camera*
   - Still image URL: `http://bridge:SECRET@<bridge-host>:8090/camera/<id>/snapshot.jpg`
   - Stream source: `http://bridge:SECRET@<bridge-host>:8090/camera/<id>/hls/index.m3u8`
2. Expose the new `camera.*` entity to Google Assistant (Home Assistant Cloud /
   Nabu Casa, or the manual `google_assistant` integration).
3. In the Google Home app, sync devices: *"Hey Google, show the living room camera
   on the TV / Nest Hub"*.

Note: each refresh with request enabled asks the Prosegur camera to take a new
picture; keep `--refresh` conservative.

## Changelog

[See CHANGES.md](CHANGES.md)

## NOTES:

This project has no relationship with Prosegur (unofficial library).

The component uses the API provided by the Web Application.

Has been developed/tested in Portugal, but should also work in other countries (please open an Issue and report your success!)

## LICENCE:
Code is under MIT licence.  
