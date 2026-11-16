"""Асинхронный клиент API СДЭК v2."""
import asyncio
import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


class CdekError(Exception):
    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def extract_errors(data: Any) -> str:
    errs: list[dict] = []
    if isinstance(data, dict):
        errs += data.get("errors") or []
        for r in data.get("requests") or []:
            errs += r.get("errors") or []
    return "; ".join(f"{e.get('message')} [{e.get('code')}]" for e in errs if isinstance(e, dict))


class CdekClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=40, transport=transport)
        self._cid, self._secret = client_id, client_secret
        self._token: str | None = None
        self._exp = 0.0
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    # ---------- транспорт ----------
    async def _get_token(self, force: bool = False) -> str:
        async with self._lock:
            if not force and self._token and time.time() < self._exp - 60:
                return self._token
            r = await self._http.post("/oauth/token", data={
                "grant_type": "client_credentials", "client_id": self._cid, "client_secret": self._secret})
            if r.status_code != 200:
                raise CdekError(f"Ошибка авторизации СДЭК: {r.status_code} {r.text[:200]}", r.status_code)
            data = r.json()
            self._token = data["access_token"]
            self._exp = time.time() + int(data.get("expires_in", 3600))
            return self._token

    async def request(self, method: str, path: str, *, raw: bool = False, **kw) -> Any:
        for attempt in (0, 1):
            token = await self._get_token(force=attempt == 1)
            r = await self._http.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)
            if r.status_code == 401 and attempt == 0:
                continue
            break
        if raw and r.status_code < 400:
            return r.content
        try:
            data = r.json() if r.content else None
        except ValueError:
            data = None
        if r.status_code >= 400:
            msg = extract_errors(data) or f"HTTP {r.status_code}: {r.text[:300]}"
            raise CdekError(msg, r.status_code, data)
        return data

    # ---------- справочники ----------
    async def find_cities(self, query: str) -> list[dict]:
        """Поиск населённого пункта. Возвращает [{code, name, label}]."""
        try:
            res = await self.request("GET", "/location/suggest/cities", params={"name": query})
            if res:
                return [{"code": r["code"], "name": r["full_name"].split(",")[0], "label": r["full_name"]}
                        for r in res[:40]]
        except CdekError as e:
            log.info("suggest/cities failed: %s", e)
        res = await self.request("GET", "/location/cities", params={"city": query, "size": 40}) or []
        out = []
        for r in res:
            label = ", ".join(x for x in (r.get("city"), r.get("sub_region"), r.get("region"), r.get("country")) if x)
            out.append({"code": r["code"], "name": r.get("city"), "label": label})
        return out

    async def delivery_points(self, city_code: int, type_: str, *, reception: bool = False,
                              handout: bool = False, weight_max: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"city_code": city_code, "type": type_}
        if reception:
            params["is_reception"] = "true"
        if handout:
            params["is_handout"] = "true"
        if weight_max:
            params["weight_max"] = weight_max
        return await self.request("GET", "/deliverypoints", params=params) or []

    # ---------- калькулятор ----------
    async def tariff_list(self, payload: dict) -> list[dict]:
        res = await self.request("POST", "/calculator/tarifflist", json=payload)
        return (res or {}).get("tariff_codes") or []

    async def calc_tariff(self, payload: dict) -> dict:
        res = await self.request("POST", "/calculator/tariff", json=payload)
        if res and res.get("errors"):
            raise CdekError(extract_errors(res), payload=res)
        return res

    # ---------- заказы ----------
    async def create_order(self, payload: dict) -> dict:
        res = await self.request("POST", "/orders", json=payload)
        for r in res.get("requests") or []:
            if r.get("state") == "INVALID":
                raise CdekError(extract_errors(res) or "Заказ отклонён", payload=res)
        return res

    async def get_order(self, uuid: str) -> dict:
        return await self.request("GET", f"/orders/{uuid}")

    async def find_order(self, cdek_number: str) -> dict:
        return await self.request("GET", "/orders", params={"cdek_number": cdek_number})

    async def wait_order(self, uuid: str, attempts: int = 8, delay: float = 1.5) -> dict:
        """Заказ создаётся асинхронно: ждём присвоения номера СДЭК или ошибки валидации."""
        order: dict = {}
        for _ in range(attempts):
            order = await self.get_order(uuid)
            for r in order.get("requests") or []:
                if r.get("type") == "CREATE" and r.get("state") == "INVALID":
                    raise CdekError(extract_errors(order) or "Заказ не прошёл проверку", payload=order)
            if (order.get("entity") or {}).get("cdek_number"):
                return order
            await asyncio.sleep(delay)
        return order

    async def delete_order(self, uuid: str) -> dict:
        return await self.request("DELETE", f"/orders/{uuid}")

    # ---------- печать ----------
    async def _print(self, kind: str, body: dict) -> bytes:
        res = await self.request("POST", f"/print/{kind}", json=body)
        puuid = res["entity"]["uuid"]
        for _ in range(20):
            await asyncio.sleep(1.5)
            info = (await self.request("GET", f"/print/{kind}/{puuid}"))["entity"]
            codes = [s.get("code") for s in info.get("statuses") or []]
            if "INVALID" in codes:
                raise CdekError("СДЭК не смог сформировать документ")
            if info.get("url") and "READY" in codes:
                return await self.request("GET", info["url"], raw=True)
        raise CdekError("Документ не успел сформироваться, попробуйте позже")

    async def print_waybill(self, order_uuid: str, copies: int = 2) -> bytes:
        return await self._print("orders", {"orders": [{"order_uuid": order_uuid}], "copy_count": copies})

    async def print_barcode(self, order_uuid: str, fmt: str = "A6") -> bytes:
        return await self._print("barcodes", {"orders": [{"order_uuid": order_uuid}], "copy_count": 1, "format": fmt})

    # ---------- вызов курьера ----------
    async def create_intake(self, payload: dict) -> dict:
        res = await self.request("POST", "/intakes", json=payload)
        for r in res.get("requests") or []:
            if r.get("state") == "INVALID":
                raise CdekError(extract_errors(res) or "Заявка отклонена", payload=res)
        return res
