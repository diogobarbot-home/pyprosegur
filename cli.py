"""Command Line Interface."""
import pprint
import logging
import aiohttp
import asyncclick as click

from pyprosegur.auth import Auth, COUNTRY
from pyprosegur.installation import Installation
from pyprosegur.exceptions import ProsegurException
from pyprosegur.bridge import CameraBridge

logging.basicConfig(level=logging.DEBUG)


@click.group()
@click.argument("username")
@click.argument("password")
@click.argument("country", type=click.Choice(COUNTRY.keys(), case_sensitive=True))
@click.pass_context
async def prosegur(ctx, username, password, country):
    """Set common arguments."""
    ctx.ensure_object(dict)

    ctx.obj["username"] = username
    ctx.obj["password"] = password
    ctx.obj["country"] = country


@click.command()
@click.pass_context
async def list_install(ctx):
    """Get List of installations."""
    username = ctx.obj["username"]
    password = ctx.obj["password"]
    country = ctx.obj["country"]

    async with aiohttp.ClientSession() as session:
        auth = Auth(session, username, password, country)

        installations = await Installation.list(auth)

        pprint.pprint(installations)


@click.command()
@click.argument("contract")
@click.pass_context
async def installation(ctx, contract):
    """Get installation status."""
    username = ctx.obj["username"]
    password = ctx.obj["password"]
    country = ctx.obj["country"]

    async with aiohttp.ClientSession() as session:
        auth = Auth(session, username, password, country)

        installation = await Installation.retrieve(auth, contract)

        pprint.pprint(installation.data)
        pprint.pprint(installation.status)


@click.command()
@click.argument("contract")
@click.pass_context
async def status(ctx, contract):
    """Get alarm status."""
    username = ctx.obj["username"]
    password = ctx.obj["password"]
    country = ctx.obj["country"]

    async with aiohttp.ClientSession() as session:
        auth = Auth(session, username, password, country)

        installation = await Installation.retrieve(auth, contract)
        r = await installation.panel_status(auth)

        print(installation.status)


@click.command()
@click.argument("contract")
@click.pass_context
async def arm(ctx, contract):
    """Arm the Alarm Panel."""
    username = ctx.obj["username"]
    password = ctx.obj["password"]
    country = ctx.obj["country"]

    async with aiohttp.ClientSession() as session:
        auth = Auth(session, username, password, country)

        installation = await Installation.retrieve(auth, contract)

        r = await installation.arm(auth)

        print(r)


@click.command()
@click.argument("contract")
@click.pass_context
async def disarm(ctx, contract):
    """Disarm the Alarm Panel."""
    username = ctx.obj["username"]
    password = ctx.obj["password"]
    country = ctx.obj["country"]

    async with aiohttp.ClientSession() as session:
        auth = Auth(session, username, password, country)

        installation = await Installation.retrieve(auth, contract)

        r = await installation.disarm(auth)

        print(r)


@click.command()
@click.argument("contract")
@click.pass_context
async def activity(ctx, contract):
    """Get Alarm Panel Activity."""
    username = ctx.obj["username"]
    password = ctx.obj["password"]
    country = ctx.obj["country"]

    async with aiohttp.ClientSession() as session:
        auth = Auth(session, username, password, country)

        installation = await Installation.retrieve(auth, contract)

        r = await installation.activity(auth)

        pprint.pprint(r)


@click.command()
@click.argument("contract")
@click.pass_context
async def panel_status(ctx, contract):
    """Get Alarm Panel Panel Status."""
    username = ctx.obj["username"]
    password = ctx.obj["password"]
    country = ctx.obj["country"]

    async with aiohttp.ClientSession() as session:
        auth = Auth(session, username, password, country)

        installation = await Installation.retrieve(auth, contract)

        r = await installation.panel_status(auth)

        pprint.pprint(r)


@click.command()
@click.argument("contract")
@click.pass_context
async def last_event(ctx, contract):
    """Get the last event."""
    username = ctx.obj["username"]
    password = ctx.obj["password"]
    country = ctx.obj["country"]

    async with aiohttp.ClientSession() as session:
        auth = Auth(session, username, password, country)

        installation = await Installation.retrieve(auth, contract)

        r = await installation.last_event(auth)

        pprint.pprint(r)


@click.command()
@click.pass_context
@click.argument("contract")
@click.argument("camera")
async def get_image(ctx, contract, camera):
    """Get CAMERA image.

    CAMERA is the Camera ID to get the image from.
    """
    username = ctx.obj["username"]
    password = ctx.obj["password"]
    country = ctx.obj["country"]

    async with aiohttp.ClientSession() as session:
        try:
            auth = Auth(session, username, password, country)

            installation = await Installation.retrieve(auth, contract)

            r = await installation.get_image(auth, camera, save_to_disk=True)

        except ProsegurException as err:
            logging.error("Image doesn't exist: %s", err)


@click.command()
@click.pass_context
@click.argument("contract")
@click.argument("camera")
async def request_image(ctx, contract, camera):
    """Request new CAMERA image.

    CAMERA is the Camera ID to request a new image from.
    """
    username = ctx.obj["username"]
    password = ctx.obj["password"]
    country = ctx.obj["country"]

    async with aiohttp.ClientSession() as session:
        auth = Auth(session, username, password, country)

        installation = await Installation.retrieve(auth, contract)

        r = await installation.request_image(auth, camera)

        pprint.pprint(r)


@click.command()
@click.pass_context
@click.argument("contract")
@click.option("--host", default="127.0.0.1", show_default=True, help="Address to bind.")
@click.option("--port", default=8090, show_default=True, help="Port to bind.")
@click.option("--refresh", default=300, show_default=True, help="Seconds between image refreshes.")
@click.option("--no-request", is_flag=True, help="Only fetch the last stored image, never ask the camera for a new one.")
@click.option("--http-user", default=None, help="Protect the bridge with HTTP Basic auth (user).")
@click.option("--http-password", default=None, help="Protect the bridge with HTTP Basic auth (password).")
@click.option("--hls", is_flag=True, help="Also serve an H.264 HLS stream (requires ffmpeg); needed for Google Home via Home Assistant.")
async def serve(ctx, contract, host, port, refresh, no_request, http_user, http_password, hls):
    """Serve CONTRACT cameras as snapshot/MJPEG HTTP endpoints (for Home Assistant / Google Home)."""
    async with aiohttp.ClientSession() as session:
        auth = Auth(session, ctx.obj["username"], ctx.obj["password"], ctx.obj["country"])
        bridge = CameraBridge(
            auth,
            contract,
            refresh_interval=refresh,
            request_new=not no_request,
            username=http_user,
            password=http_password,
            hls=hls,
        )
        await bridge.run(host, port)


prosegur.add_command(serve)
prosegur.add_command(list_install)
prosegur.add_command(installation)
prosegur.add_command(arm)
prosegur.add_command(disarm)
prosegur.add_command(activity)
prosegur.add_command(last_event)
prosegur.add_command(get_image)
prosegur.add_command(request_image)
prosegur.add_command(panel_status)
prosegur.add_command(status)

if __name__ == "__main__":
    try:
        prosegur(obj={})
    except Exception as err:
        print(err)
