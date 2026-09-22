import json

import httpx
import pytest

from app.jd_client import JDClient, JDError, JDUnavailable


def make_client(handler):
    return JDClient("http://jd:3128", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_call_posts_params_and_unwraps_data():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": "IDLE"})

    jd = make_client(handler)
    assert await jd.state() == "IDLE"
    assert seen["path"] == "/downloadcontroller/getCurrentState"
    assert seen["body"] == {"params": []}


@pytest.mark.asyncio
async def test_add_links_body():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": {"id": 1}})

    jd = make_client(handler)
    await jd.add_links(["http://a/1", "http://a/2"], "pkg", "/output/게임", autostart=True)
    p = seen["body"]["params"][0]
    assert p["links"] == "http://a/1\nhttp://a/2"
    assert p["packageName"] == "pkg"
    assert p["destinationFolder"] == "/output/게임"
    assert p["autostart"] is True and p["autoExtract"] is False


@pytest.mark.asyncio
async def test_cleanup_finished_args():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": None})

    await make_client(handler).cleanup_finished()
    assert seen["body"]["params"] == [[], [], "DELETE_FINISHED", "REMOVE_LINKS_ONLY", "ALL"]


@pytest.mark.asyncio
async def test_device_error_raises_jderror():
    def handler(req):
        return httpx.Response(200, json={"src": "DEVICE", "type": "API_COMMAND_NOT_FOUND", "data": None})

    with pytest.raises(JDError) as e:
        await make_client(handler).call("downloadcontroller/getSpeedInBytes")
    assert e.value.kind == "API_COMMAND_NOT_FOUND"


@pytest.mark.asyncio
async def test_connect_error_raises_unavailable():
    def handler(req):
        raise httpx.ConnectError("boom", request=req)

    with pytest.raises(JDUnavailable):
        await make_client(handler).state()


@pytest.mark.asyncio
async def test_speed_limit_set_order():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.url.path, json.loads(req.content)["params"]))
        return httpx.Response(200, json={"data": True if "Enabled" in str(calls[-1]) else 1048576})

    jd = make_client(handler)
    await jd.set_speed_limit(True, 1048576)
    assert calls[0][0] == "/config/set" and calls[0][1][2:] == ["DownloadSpeedLimit", 1048576]
    assert calls[1][0] == "/config/set" and calls[1][1][2:] == ["DownloadSpeedLimitEnabled", True]


@pytest.mark.asyncio
async def test_account_calls():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.url.path, json.loads(req.content)["params"]))
        return httpx.Response(200, json={"data": []})

    jd = make_client(handler)
    await jd.add_account("terabox.com", "me@x.io", "s3cret")
    await jd.update_account(42, "me@x.io", "n3w")
    await jd.set_accounts_enabled(False, [42])
    await jd.remove_accounts([42])
    assert calls[0] == ("/accountsV2/addAccount", ["terabox.com", "me@x.io", "s3cret"])
    assert calls[1] == ("/accountsV2/updateAccount", [42, "me@x.io", "n3w"])
    assert calls[2] == ("/accountsV2/disableAccounts", [[42]])
    assert calls[3] == ("/accountsV2/removeAccounts", [[42]])
