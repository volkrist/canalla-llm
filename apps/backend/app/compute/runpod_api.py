"""Official RunPod REST v2 only. No MCP, OAuth extraction, or automatic write retries."""

import asyncio
import base64
import json
import re
import shlex
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
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
    "price_limit": "Подходящий GPU существует, но превышает ваш лимит.",
    "price_changed": "Цена или доступность изменилась. Выполните поиск и подтвердите запуск заново.",
    "create_unknown": "Результат создания пока неизвестен. Повторное создание заблокировано; выполняется сверка с RunPod.",
    "startup_failed": "Не удалось запустить AI. Compute освобождается; данные Volume сохраняются.",
    "startup_timeout": "Превышено время запуска AI. Compute освобождается.",
    "external_compute": "Обнаружен существующий compute. Дополнительный Pod не создаётся.",
    "multiple_compute": "Обнаружено несколько Pods для этого Volume. Требуется проверка администратора.",
    "session_budget": "Достигнут бюджет compute-сессии.",
    "price_violation": "RunPod подтвердил цену выше лимита. Созданный compute освобождается.",
    "not_found": "Ресурс больше не существует в RunPod.",
    "llm_key_missing": "Для защищённого подключения настройте LLM_API_KEY на backend (не менее 32 символов).",
    "connection_auth_failed": "Не удалось авторизовать защищённое подключение AI.",
    "connection_failed": "Не удалось подключиться к AI. Backend продолжает проверку.",
    "model_mismatch": "Запущенная модель не совпадает с настроенным alias.",
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


class RunPodAPI:
    BASE = "https://api.runpod.io/v2"

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

    async def request(self, method: str, path: str, **kwargs):
        try:
            async with self.client() as client:
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
        if volume.dataCenter != self.settings.runpod_datacenter or volume.type not in dc.get(
            "networkVolumeTypes", []
        ):
            raise RunPodError("volume_mismatch", 409)
        return volume

    async def gpu_options(self, preferences: ComputePreferences):
        data = await self.request(
            "GET",
            "/catalog/gpus",
            params={
                "include": "AVAILABILITY",
                "product": "POD",
                "count": 1,
                "cloud": "SECURE",
                "minCudaVersion": self.settings.runpod_min_cuda_version,
            },
        )
        try:
            if not isinstance(data.get("gpus"), list):
                raise ValueError()
            options = []
            for gpu in data["gpus"]:
                if gpu["manufacturer"] != "NVIDIA" or not gpu["secure"]:
                    continue
                memory = int(gpu["memory"])
                price = Decimal(str(gpu["price"]["secure"]))
                if not price.is_finite() or price <= 0:
                    raise ValueError()
                dc = next(
                    (dc for dc in gpu.get("dataCenters", []) if dc["id"] == self.settings.runpod_datacenter),
                    None,
                )
                stock = dc["availability"] if dc else "NONE"
                compatible = memory >= preferences.min_vram_gb
                reason = (
                    "insufficient_vram"
                    if not compatible
                    else "price_limit"
                    if price > preferences.max_hourly_price
                    else "unavailable"
                    if stock not in {"LOW", "MEDIUM", "HIGH"}
                    else None
                )
                options.append(
                    GpuOption(
                        id=gpu["id"],
                        name=gpu["name"],
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

    def parse_pod(self, data):
        try:
            return PodInfo.model_validate(data)
        except ValidationError:
            raise RunPodError("malformed_response") from None

    async def list_pods(self):
        data = await self.request("GET", "/pods")
        if not isinstance(data.get("pods"), list):
            raise RunPodError("malformed_response")
        return [self.parse_pod(pod) for pod in data["pods"]]

    async def get_pod(self, pod_id):
        return self.parse_pod(await self.request("GET", "/pods/" + quote(pod_id, safe="")))

    async def create_pod(self, name: str, gpu: GpuOption):
        real = self.settings.llm_provider == "llamacpp"
        if real and len(self.settings.llm_api_key) < 32:
            raise RunPodError("llm_key_missing", 422)
        args = startup_command(self.settings.runpod_llm_port, self.settings.runpod_startup_timeout)
        environment = {}
        if real:
            runtime = base64.b64encode(Path(__file__).with_name("remote_runtime.py").read_bytes()).decode(
                "ascii"
            )
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
            json={
                "name": name,
                "image": self.settings.runpod_image,
                "cloud": "SECURE",
                "gpu": {"id": gpu.id, "count": 1, "minCudaVersion": self.settings.runpod_min_cuda_version},
                "dataCenterIds": [self.settings.runpod_datacenter],
                "disk": 10,
                "mounts": {
                    "network": [{"volumeId": self.settings.runpod_network_volume_id, "path": "/workspace"}]
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
            records = data["records"]
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
