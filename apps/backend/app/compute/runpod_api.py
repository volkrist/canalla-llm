"""Official RunPod REST v2 only. No MCP, OAuth extraction, or automatic write retries."""

import asyncio
import base64
import json
import re
import shlex
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from ..config import Settings
from .schemas import ComputePreferences, GpuOption, PodInfo, VolumeInfo

ERROR_MESSAGES = {
    "not_configured": "RunPod не настроен. Добавьте API key только в backend .env.",
    "runpod_unavailable": "RunPod сейчас недоступен. Повторите позже.",
    "runpod_timeout": "RunPod не ответил вовремя. Проверяем состояние операции.",
    "runpod_auth": "Backend не смог авторизоваться в RunPod.",
    "runpod_balance": "На аккаунте RunPod недостаточно средств.",
    "runpod_rate_limit": "RunPod ограничил частоту запросов. Повторите позже.",
    "runpod_invalid_request": "RunPod отклонил параметры запуска. Проверьте конфигурацию backend.",
    "malformed_response": "RunPod вернул неподдерживаемый ответ. Операция остановлена.",
    "placement_rejected": "GPU исчезла из доступных или размещение отклонено.",
    "volume_mismatch": "Network Volume несовместим с выбранным дата-центром.",
    "no_compatible_gpu": "Подходящих GPU сейчас нет.",
    "gpu_unavailable": "Подходящих GPU сейчас нет в наличии. Поиск можно повторить.",
    "price_limit": "Подходящий GPU существует, но превышает ваш лимит.",
    "price_changed": "Цена или доступность изменилась. Выполните поиск и подтвердите запуск заново.",
    "create_unknown": "Результат создания пока неизвестен. Повторное создание заблокировано; выполняется сверка с RunPod.",
    "startup_failed": "Не удалось запустить AI. Compute освобождается; данные Volume сохраняются.",
    "startup_timeout": "Превышено время запуска AI. Compute освобождается.",
    "external_compute": "Обнаружен существующий compute. Дополнительный Pod не создаётся.",
    "multiple_compute": "Обнаружено несколько Pods для этого Volume. Требуется проверка администратора.",
    "session_budget": "Достигнут бюджет compute-сессии.",
    "COMPUTE_BUDGET_REACHED": "Достигнут бюджет compute-сессии.",
    "price_violation": "RunPod подтвердил цену выше лимита. Созданный compute освобождается.",
    "not_found": "Ресурс больше не существует в RunPod.",
    "llm_key_missing": "Для защищённого подключения настройте LLM_API_KEY на backend (не менее 32 символов).",
    "connection_auth_failed": "Не удалось авторизовать защищённое подключение AI.",
    "connection_failed": "Не удалось подключиться к AI. Backend продолжает проверку.",
    "model_mismatch": "Запущенная модель не совпадает с настроенным alias.",
    # Canalla Cloud (Central RunPod Gateway). RunPod becomes infrastructure of Canalla: in shared
    # mode a user never holds the provider credential, so these are the messages that
    # replace the provider ones.
    "gateway_not_connected": "Canalla Cloud не подключён. Подключите его в настройках Canalla LLM.",
    "gateway_unavailable": "Canalla Cloud сейчас недоступен. Повторите позже.",
    "gateway_auth_failed": "Canalla Cloud отклонил эту установку. Подключите её заново.",
    "gateway_protocol_mismatch": "Версия Canalla Cloud несовместима с этой установкой Canalla.",
    "installation_revoked": "Эта установка отключена от Canalla Cloud. Нужен новый код активации.",
    "gateway_busy": "AI занят другим запросом. Повторите позже.",
    "gateway_queue_full": "Очередь запросов AI заполнена. Повторите позже.",
    "gateway_budget_denied": "Запрос превышает установленные лимиты расходов.",
    "gateway_request_in_flight": "Этот запрос уже выполняется.",
    "gateway_request_completed": "Этот запрос уже завершён.",
    "gateway_managed_compute": (
        "Compute управляется Canalla Cloud. Запуск и остановка доступны через Canalla Cloud."
    ),
}


class RunPodError(Exception):
    def __init__(self, code: str, status: int = 502):
        self.code = code
        self.status = status
        super().__init__(ERROR_MESSAGES.get(code, ERROR_MESSAGES["runpod_unavailable"]))


def startup_command(port: int, timeout: int) -> str:
    # Only trusted backend constants enter this command. Existing volume data is never modified.
    script = f"""set -eu
echo ALEX_LLM_PHASE=mounting_storage
test -f /workspace/start-llm.sh || {{ echo ALEX_LLM_PHASE=error; exit 1; }}
test -f /workspace/check-llm.sh || {{ echo ALEX_LLM_PHASE=error; exit 1; }}
echo ALEX_LLM_PHASE=starting_llm
bash /workspace/start-llm.sh >/tmp/alex-llm-start.log 2>&1 &
echo ALEX_LLM_PHASE=loading_model
deadline=$((SECONDS+{timeout}))
until curl --silent --fail --max-time 3 http://127.0.0.1:{port}/health >/dev/null; do
  if [ "$SECONDS" -ge "$deadline" ]; then echo ALEX_LLM_PHASE=error; exit 1; fi
  sleep 3
done
bash /workspace/check-llm.sh >/tmp/alex-llm-check.log 2>&1 || {{ echo ALEX_LLM_PHASE=error; exit 1; }}
while true; do
  if curl --silent --fail --max-time 3 http://127.0.0.1:{port}/health >/dev/null; then
    echo ALEX_LLM_PHASE=ready
  else
    echo ALEX_LLM_PHASE=error
    exit 1
  fi
  sleep 10
done"""
    return "bash -lc " + shlex.quote(script)


ACCOUNT_QUERY = """query AlexAccount { myself { id clientBalance currentSpendPerHr } }"""


class RunPodAPI:
    BASE = "https://api.runpod.io/v2"
    GRAPHQL_TIMEOUT = 6

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    @property
    def configured(self):
        return bool(self.settings.runpod_api_key.get_secret_value())

    def client(self, timeout=15):
        if not self.configured:
            raise RunPodError("not_configured", 503)
        return httpx.AsyncClient(
            base_url=self.BASE,
            timeout=timeout,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + self.settings.runpod_api_key.get_secret_value()},
            transport=self.transport,
        )

    async def request(self, method: str, path: str, *, timeout: float | None = None, **kwargs):
        try:
            async with self.client(timeout or 15) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise RunPodError("runpod_timeout", 504) from None
        except httpx.HTTPError:
            raise RunPodError("runpod_unavailable") from None
        if response.status_code >= 400:
            codes = {
                400: "placement_rejected",
                401: "runpod_auth",
                402: "runpod_balance",
                403: "runpod_auth",
                404: "not_found",
                409: "placement_rejected",
                422: "runpod_invalid_request",
                429: "runpod_rate_limit",
            }
            raise RunPodError(codes.get(response.status_code, "runpod_unavailable"), response.status_code)
        if response.status_code == 204:
            return None
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, TypeError):
            raise RunPodError("malformed_response") from None

    async def graphql(self, query: str):
        """Read-only GraphQL. The account balance is not exposed by REST v2 or v1."""
        if not self.configured:
            raise RunPodError("not_configured", 503)
        try:
            async with httpx.AsyncClient(
                timeout=self.GRAPHQL_TIMEOUT,
                follow_redirects=False,
                headers={"Authorization": "Bearer " + self.settings.runpod_api_key.get_secret_value()},
                transport=self.transport,
            ) as client:
                response = await client.post(self.settings.runpod_graphql_url, json={"query": query})
        except httpx.TimeoutException:
            raise RunPodError("runpod_timeout", 504) from None
        except httpx.HTTPError:
            raise RunPodError("runpod_unavailable") from None
        if response.status_code >= 400:
            codes = {
                401: "runpod_auth",
                402: "runpod_balance",
                403: "runpod_auth",
                404: "not_found",
                422: "runpod_invalid_request",
                429: "runpod_rate_limit",
            }
            raise RunPodError(codes.get(response.status_code, "runpod_unavailable"), response.status_code)
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise RunPodError("malformed_response") from None
        if not isinstance(data, dict):
            raise RunPodError("malformed_response")
        if data.get("errors") and not data.get("data"):
            raise RunPodError("malformed_response")
        return data

    def parse_balance(self, payload):
        try:
            data = payload["data"]
            myself = data["myself"] if isinstance(data, dict) else None
            if not isinstance(myself, dict):
                raise ValueError()
            balance = Decimal(str(myself["clientBalance"]))
            if not balance.is_finite():
                raise ValueError()
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise RunPodError("malformed_response") from None
        spend = myself.get("currentSpendPerHr")
        try:
            spend = None if spend is None else Decimal(str(spend))
            if spend is not None and not spend.is_finite():
                spend = None
        except (TypeError, ValueError, InvalidOperation):
            spend = None
        return {"balance": balance, "current_spend_per_hr": spend}

    async def account_balance(self):
        """READ-ONLY account balance. Never creates, resumes or stops compute."""
        return self.parse_balance(await self.graphql(ACCOUNT_QUERY))

    async def volume(self):
        data = await self.request(
            "GET", "/network-volumes/" + quote(self.settings.runpod_network_volume_id, safe="")
        )
        try:
            volume = VolumeInfo.model_validate(data)
        except ValidationError:
            raise RunPodError("malformed_response") from None
        dc = await self.request(
            "GET", "/catalog/datacenters/" + quote(self.settings.runpod_datacenter, safe="")
        )
        if dc is None:  # the datacenter catalog did not return the JSON object we need
            raise RunPodError("malformed_response")
        if volume.dataCenter != self.settings.runpod_datacenter or volume.type not in dc.get(
            "networkVolumeTypes", []
        ):
            raise RunPodError("volume_mismatch", 409)
        return volume

    @staticmethod
    def parse_price(value, *, strict: bool) -> Decimal | None:
        """A catalogue price. ``strict`` keeps the historic Secure-Cloud rule: a Secure row whose
        price is missing, non-finite or non-positive is a malformed catalogue, not a cheap GPU.
        An optional tier (Community) that is absent is simply not bookable.
        """
        if value is None:
            if strict:
                raise ValueError()
            return None
        try:
            price = Decimal(str(value))
        except (ArithmeticError, TypeError, ValueError):
            if strict:
                raise ValueError() from None
            return None
        if not price.is_finite() or price <= 0:
            if strict:
                raise ValueError()
            return None
        return price

    async def gpu_offers(self):
        """READ-ONLY catalogue slice: every cloud tier and datacenter the provider reports.

        The allocator must never be pinned to one physical placement, so this read asks the
        catalogue without the Secure-Cloud filter and keeps the per-tier price plus the
        per-datacenter availability. Rows are plain, non-secret dicts so the shared candidate
        policy (``app.compute.candidates``) can be the one place that decides what is bookable.
        """
        data = await self.request(
            "GET",
            "/catalog/gpus",
            params={
                "include": "AVAILABILITY",
                "product": "POD",
                "count": 1,
                "minCudaVersion": self.settings.runpod_min_cuda_version,
            },
        )
        try:
            if data is None or not isinstance(data.get("gpus"), list):
                raise ValueError()
            offers = []
            for gpu in data["gpus"]:
                if gpu["manufacturer"] != "NVIDIA":
                    continue
                memory = int(gpu["memory"])
                secure = bool(gpu["secure"])
                prices = gpu["price"]
                offers.append(
                    {
                        "id": str(gpu["id"]),
                        "name": str(gpu.get("name") or gpu["id"]),
                        "vram_gb": memory,
                        "secure": secure,
                        "community": bool(gpu.get("community", False)),
                        "price": {
                            "secure": self.parse_price(prices["secure"], strict=secure),
                            "community": self.parse_price(prices.get("community"), strict=False),
                        },
                        "data_centers": {
                            str(center["id"]): str(center.get("availability") or "NONE")
                            for center in gpu.get("dataCenters", [])
                            if isinstance(center, dict) and center.get("id")
                        },
                    }
                )
            return offers
        except (KeyError, TypeError, ValueError, InvalidOperation, ValidationError):
            raise RunPodError("malformed_response") from None

    def project_options(self, rows, preferences: ComputePreferences):
        """The single-placement projection: Secure Cloud, the configured datacenter.

        This is the shape the quote and the GPU table have always used, and it stays the
        user-facing catalogue for a manual choice. The allocator itself walks the wider set from
        ``gpu_offers`` through the shared candidate policy. Projecting in memory (instead of a
        second provider read) keeps one catalogue read per decision.
        """
        try:
            options = []
            for row in rows:
                if not row["secure"]:
                    continue
                price = row["price"]["secure"]
                stock = row["data_centers"].get(self.settings.runpod_datacenter, "NONE")
                memory = int(row["vram_gb"])
                compatible = memory >= preferences.min_vram_gb
                reason = (
                    "gpu_not_selected"
                    if preferences.gpu_id and row["id"] != preferences.gpu_id
                    else "insufficient_vram"
                    if not compatible
                    else "price_limit"
                    if price > preferences.max_hourly_price
                    else "unavailable"
                    if stock not in {"LOW", "MEDIUM", "HIGH"}
                    else None
                )
                options.append(
                    GpuOption(
                        id=row["id"],
                        name=row["name"],
                        vram_gb=memory,
                        hourly_rate=price,
                        availability=stock,
                        compatible=compatible,
                        selectable=reason is None,
                        reason=reason,
                    )
                )
            return sorted(options, key=lambda gpu: (gpu.hourly_rate, gpu.id))
        except (KeyError, TypeError, ValueError, InvalidOperation, ValidationError):
            raise RunPodError("malformed_response") from None

    async def gpu_options(self, preferences: ComputePreferences):
        """One catalogue read, projected to the single-placement quote shape."""
        return self.project_options(await self.gpu_offers(), preferences)

    def parse_pod(self, data):
        try:
            return PodInfo.model_validate(data)
        except ValidationError:
            raise RunPodError("malformed_response") from None

    async def list_pods(self):
        data = await self.request("GET", "/pods")
        if data is None or not isinstance(data.get("pods"), list):
            raise RunPodError("malformed_response")
        return [self.parse_pod(pod) for pod in data["pods"]]

    async def get_pod(self, pod_id):
        return self.parse_pod(await self.request("GET", "/pods/" + quote(pod_id, safe="")))

    async def create_pod(
        self,
        name: str,
        gpu: GpuOption,
        *,
        cloud: str | None = None,
        datacenter: str | None = None,
        volume_id: str | None = None,
        timeout: float | None = None,
    ):
        """Create one Pod for one *candidate* placement.

        A model-required allocation walks an ordered candidate set, so the tier, the datacenter
        and the Volume it mounts are the candidate's, not a process-wide constant. Defaults keep
        the historic direct-mode call shape (Secure Cloud, the configured datacenter, the
        configured Volume).
        """
        real = self.settings.llm_provider == "llamacpp"
        if real and len(self.settings.llm_api_key) < 32:
            raise RunPodError("llm_key_missing", 422)
        args = startup_command(self.settings.runpod_llm_port, self.settings.runpod_startup_timeout)
        environment = {}
        if real:
            from ..packaging import package_file

            runtime = base64.b64encode(
                package_file("app", "compute", "remote_runtime.py").read_bytes()
            ).decode("ascii")
            args = "python3 -u -c " + shlex.quote(
                "import base64; exec(compile(base64.b64decode('" + runtime + "'), 'alex-runtime', 'exec'))"
            )
            environment = {
                "ALEX_GATEWAY_KEY": self.settings.llm_api_key,
                "ALEX_LLM_PORT": str(self.settings.runpod_llm_port),
                "ALEX_GATEWAY_PORT": str(self.settings.runpod_gateway_port),
                "ALEX_LLM_MODEL": self.settings.llm_model,
                "ALEX_STARTUP_TIMEOUT": str(self.settings.runpod_startup_timeout),
            }
        data = await self.request(
            "POST",
            "/pods",
            timeout=timeout,
            json={
                "name": name,
                "image": self.settings.runpod_image,
                "cloud": cloud or "SECURE",
                "gpu": {"id": gpu.id, "count": 1, "minCudaVersion": self.settings.runpod_min_cuda_version},
                "dataCenterIds": [datacenter or self.settings.runpod_datacenter],
                "disk": 10,
                "mounts": {
                    "network": [
                        {
                            "volumeId": volume_id or self.settings.runpod_network_volume_id,
                            "path": "/workspace",
                        }
                    ]
                },
                "ports": [f"{self.settings.runpod_gateway_port}/http"] if real else [],
                "env": environment,
                "startJupyter": False,
                "startSsh": False,
                "args": args,
            },
        )
        return self.parse_pod(data)

    async def terminate_pod(self, pod_id):
        # Only the controller can call this, for the exact DB-tracked pod. Volume is untouched.
        try:
            await self.request(
                "POST", "/pods/" + quote(pod_id, safe="") + "/action", json={"action": "terminate"}
            )
        except RunPodError as error:
            if error.code != "not_found":
                raise

    async def phases(self, pod_id):
        phases = []
        try:
            async with asyncio.timeout(2):
                async with self.client(timeout=2) as client:
                    async with client.stream(
                        "GET",
                        "/pods/" + quote(pod_id, safe="") + "/logs",
                        params={"source": "container", "tail": 50},
                    ) as response:
                        if response.status_code != 200:
                            return []
                        async for line in response.aiter_lines():
                            if line.startswith("data:"):
                                data = json.loads(line[5:])
                                timestamp = datetime.fromisoformat(
                                    str(data.get("ts", "")).replace("Z", "+00:00")
                                )
                                if (
                                    timestamp.tzinfo is None
                                    or (datetime.now(timezone.utc) - timestamp).total_seconds() > 45
                                ):
                                    continue
                                match = re.fullmatch(
                                    r"ALEX_LLM_PHASE=(mounting_storage|starting_llm|loading_model|ready|error)",
                                    str(data.get("line", "")).strip(),
                                )
                                if match:
                                    phases.append(match[1])
                                if len(phases) >= 20:
                                    break
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError):
            pass
        return phases

    async def actual_cost(self, pod_id, start, end):
        data = await self.request(
            "GET",
            "/billing/pods",
            params={
                "podId": pod_id,
                "startTime": start.isoformat(),
                "endTime": end.isoformat(),
                "bucketSize": "day",
            },
        )
        try:
            records = data["records"] if data else None
            if not isinstance(records, list):
                raise ValueError()
            amounts = [Decimal(str(row["gpuAmount"])) for row in records if row["podId"] == pod_id]
            if not amounts:
                return None
            if any(not amount.is_finite() or amount < 0 for amount in amounts):
                raise ValueError()
            return sum(amounts, Decimal(0))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise RunPodError("malformed_response") from None
