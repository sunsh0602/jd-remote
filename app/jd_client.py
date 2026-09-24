"""JDownloader2 로컬 RemoteAPI(DeprecatedAPIServer, 기본 3128) 클라이언트.

확인된 규약(2026-09-22, JD build 48637):
- 모든 호출은 POST /<namespace>/<method>, body {"params": [...]} (GET ?params= 는 실패).
- 성공: {"data": ...}. 실패: {"src":"DEVICE","type":"API_COMMAND_NOT_FOUND"|"BAD_PARAMETERS"|..., "data": null|str}.
- downloadcontroller/getSpeedInBytes 는 없음 → 패키지 speed 합으로 계산.
"""
from __future__ import annotations

from typing import Any

import httpx


class JDError(Exception):
    """JD가 에러 타입을 돌려준 경우."""

    def __init__(self, kind: str, detail: Any = None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


class JDUnavailable(Exception):
    """JD에 연결 자체가 안 되는 경우(컨테이너 다운, jdnet 미연결 등)."""


PACKAGE_FIELDS = {
    "bytesLoaded": True, "bytesTotal": True, "speed": True, "eta": True,
    "finished": True, "status": True, "running": True, "enabled": True,
    "childCount": True, "hosts": True, "saveTo": True, "comment": True,
}
LINK_FIELDS = {
    "bytesLoaded": True, "bytesTotal": True, "speed": True, "eta": True,
    "finished": True, "status": True, "enabled": True, "host": True,
    "url": True, "packageUUID": True, "running": True, "skipped": True,
}
GRABBER_LINK_FIELDS = {
    "availability": True, "bytesTotal": True, "host": True, "name": True,
    "url": True, "packageUUID": True, "enabled": True, "comment": True, "addedDate": True,
}
GRABBER_PACKAGE_FIELDS = {
    "bytesTotal": True, "childCount": True, "hosts": True, "saveTo": True,
    "enabled": True, "comment": True,
}
ACCOUNT_FIELDS = {
    "userName": True, "validUntil": True, "trafficLeft": True, "trafficMax": True,
    "enabled": True, "valid": True, "error": True, "hostname": True,
}
GENERAL = "org.jdownloader.settings.GeneralSettings"


class JDClient:
    def __init__(self, base_url: str, timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── 저수준 ────────────────────────────────────────────────────────────
    async def call(self, path: str, *params: Any) -> Any:
        try:
            r = await self._http.post(f"/{path.lstrip('/')}", json={"params": list(params)})
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            raise JDUnavailable(str(e)) from e
        try:
            body = r.json()
        except ValueError as e:
            raise JDError("BAD_RESPONSE", r.text[:200]) from e
        if isinstance(body, dict) and body.get("type") and body.get("src") == "DEVICE":
            raise JDError(body["type"], body.get("data"))
        return body.get("data") if isinstance(body, dict) else body

    # ── 컨트롤러 ──────────────────────────────────────────────────────────
    async def state(self) -> str:
        return await self.call("downloadcontroller/getCurrentState")

    async def start(self) -> Any:
        return await self.call("downloadcontroller/start")

    async def stop(self) -> Any:
        return await self.call("downloadcontroller/stop")

    async def pause(self, paused: bool) -> Any:
        return await self.call("downloadcontroller/pause", paused)

    # ── 다운로드 목록 ──────────────────────────────────────────────────────
    async def packages(self, uuids: list[int] | None = None) -> list[dict]:
        q = dict(PACKAGE_FIELDS)
        if uuids:
            q["packageUUIDs"] = uuids
        return await self.call("downloadsV2/queryPackages", q) or []

    async def links(self, package_uuids: list[int] | None = None) -> list[dict]:
        q = dict(LINK_FIELDS)
        if package_uuids:
            q["packageUUIDs"] = package_uuids
        return await self.call("downloadsV2/queryLinks", q) or []

    async def remove(self, link_ids: list[int], package_ids: list[int]) -> Any:
        return await self.call("downloadsV2/removeLinks", link_ids, package_ids)

    async def delete_with_files(self, link_ids: list[int], package_ids: list[int]) -> Any:
        return await self.call("downloadsV2/cleanup", link_ids, package_ids,
                               "DELETE_ALL", "REMOVE_LINKS_AND_DELETE_FILES", "SELECTED")

    async def cleanup_finished(self) -> Any:
        return await self.call("downloadsV2/cleanup", [], [], "DELETE_FINISHED", "REMOVE_LINKS_ONLY", "ALL")

    async def move_packages(self, package_ids: list[int], after_package_id: int | None) -> Any:
        """패키지를 after_package_id 바로 뒤로 옮긴다. None 이면 맨 위(JD 는 -1 을 '맨 위'로 해석).
        JD 는 목록 위에서부터 받으므로 이 순서가 실제 다운로드 순서다."""
        return await self.call("downloadsV2/movePackages", package_ids, -1 if after_package_id is None else after_package_id)

    async def set_enabled(self, enabled: bool, link_ids: list[int], package_ids: list[int]) -> Any:
        return await self.call("downloadsV2/setEnabled", enabled, link_ids, package_ids)

    async def retry(self, link_ids: list[int], package_ids: list[int]) -> Any:
        await self.call("downloadsV2/resetLinks", link_ids, package_ids)
        return await self.call("downloadsV2/forceDownload", link_ids, package_ids)

    # ── 링크그래버 ────────────────────────────────────────────────────────
    async def add_links(self, links: list[str], package_name: str | None = None,
                        dest_folder: str | None = None, autostart: bool = False, assign_job_id: bool = False) -> Any:
        """assign_job_id=True 면 응답에 LinkCollectingJob 의 id 가 온다 → 그 작업의 링크만 jobUUIDs 로 골라낼 수 있다.
        주의: JD 의 autostart=True 는 검증이 끝난 장바구니 '전체'를 확정해 버리므로 즉시 다운로드에는 쓰지 않는다."""
        q: dict[str, Any] = {
            "links": "\n".join(links),
            "autostart": autostart,
            "assignJobID": assign_job_id,
            "autoExtract": False,
            "overwritePackagizerRules": False,
        }
        if package_name:
            q["packageName"] = package_name
        if dest_folder:
            q["destinationFolder"] = dest_folder
        return await self.call("linkgrabberv2/addLinks", q)

    async def grabber_collecting(self) -> bool:
        return bool(await self.call("linkgrabberv2/isCollecting"))

    async def grabber_links(self) -> list[dict]:
        return await self.call("linkgrabberv2/queryLinks", dict(GRABBER_LINK_FIELDS)) or []

    async def grabber_links_by_jobs(self, job_ids: list[int]) -> list[dict]:
        """특정 addLinks 작업(job)으로 들어온 링크만."""
        return await self.call("linkgrabberv2/queryLinks", {**GRABBER_LINK_FIELDS, "jobUUIDs": job_ids}) or []

    async def grabber_packages(self) -> list[dict]:
        return await self.call("linkgrabberv2/queryPackages", dict(GRABBER_PACKAGE_FIELDS)) or []

    async def grabber_move_to_downloads(self, link_ids: list[int], package_ids: list[int]) -> Any:
        return await self.call("linkgrabberv2/moveToDownloadlist", link_ids, package_ids)

    async def grabber_remove(self, link_ids: list[int], package_ids: list[int]) -> Any:
        return await self.call("linkgrabberv2/removeLinks", link_ids, package_ids)

    # ── 호스터 계정 (accountsV2) ──────────────────────────────────────────
    async def accounts(self) -> list[dict]:
        return await self.call("accountsV2/listAccounts", dict(ACCOUNT_FIELDS)) or []

    async def premium_hosters(self) -> list[str]:
        return await self.call("accountsV2/listPremiumHoster") or []

    async def add_account(self, hostname: str, username: str, password: str) -> Any:
        return await self.call("accountsV2/addAccount", hostname, username, password)

    async def update_account(self, account_id: int, username: str, password: str) -> Any:
        # MyJD API 정식 이름. updateAccount 는 존재하지 않음(API_COMMAND_NOT_FOUND)
        return await self.call("accountsV2/setUserNameAndPassword", account_id, username, password)

    async def remove_accounts(self, ids: list[int]) -> Any:
        return await self.call("accountsV2/removeAccounts", ids)

    async def set_accounts_enabled(self, enabled: bool, ids: list[int]) -> Any:
        return await self.call("accountsV2/enableAccounts" if enabled else "accountsV2/disableAccounts", ids)

    async def refresh_accounts(self, ids: list[int]) -> Any:
        return await self.call("accountsV2/refreshAccounts", ids)

    # ── 설정 / 기타 ───────────────────────────────────────────────────────
    async def speed_limit(self) -> dict:
        enabled = await self.call("config/get", GENERAL, None, "DownloadSpeedLimitEnabled")
        limit = await self.call("config/get", GENERAL, None, "DownloadSpeedLimit")
        return {"enabled": bool(enabled), "limit": int(limit or 0)}

    async def set_speed_limit(self, enabled: bool, limit: int | None = None) -> dict:
        if limit is not None:
            await self.call("config/set", GENERAL, None, "DownloadSpeedLimit", int(limit))
        await self.call("config/set", GENERAL, None, "DownloadSpeedLimitEnabled", bool(enabled))
        return await self.speed_limit()

    async def captchas(self) -> list:
        return await self.call("captcha/list") or []

    async def version(self) -> Any:
        return await self.call("jd/version")
