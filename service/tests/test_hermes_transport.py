"""Exercise Hermes HTTP failures and run cleanup without opening local sockets."""
import asyncio
import json

import httpx
import pytest

from jarvis.backends.hermes import Hermes, HermesError


def test_health_does_not_report_a_failed_model_endpoint_as_healthy():
    async def check():
        def handle(request):
            if request.url.path == "/health":
                return httpx.Response(200, json={"version": "0.19.0"})
            return httpx.Response(503)
        client = Hermes()
        client._client = httpx.AsyncClient(base_url=client.url, transport=httpx.MockTransport(handle))
        try:
            assert not (await client.status())["healthy"]
        finally:
            await client.close()
    asyncio.run(check())


@pytest.mark.parametrize("ending", ["completed", "truncated", "broken", "approval_failed", "cancelled"])
def test_delegation_completion_and_cleanup(ending):
    async def check():
        requests = []

        def handle(request):
            requests.append(request)
            path = request.url.path
            if path == "/v1/runs":
                return httpx.Response(202, json={"run_id": "test-run"})
            if path.endswith("/stop"):
                return httpx.Response(200)
            if path.endswith("/approval"):
                return httpx.Response(500)
            if ending == "broken":
                raise httpx.ReadError("connection lost", request=request)
            events = [{"event": "message.delta", "delta": "hello"}]
            if ending == "completed":
                events.append({"event": "run.completed", "output": "hello"})
            elif ending == "approval_failed":
                events.append({"event": "approval.request"})
            elif ending == "cancelled":
                events.append({"event": "run.cancelled"})
            return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))

        client = Hermes()
        client._client = httpx.AsyncClient(base_url=client.url, transport=httpx.MockTransport(handle))
        try:
            result = await client.delegate("hello", session_id="session", history=[{"role": "user", "content": "before"}],
                                           memory_scope="conversation")
            sent = json.loads(requests[0].content)
            assert sent["session_id"] == "session" and sent["conversation_history"][0]["content"] == "before"
            assert requests[0].headers["X-Hermes-Session-Key"] == "conversation"
            assert not client.runs
            stopped = any(r.url.path.endswith("/stop") for r in requests)
            assert result["ok"] == (ending == "completed")
            assert stopped == (ending in {"truncated", "broken", "approval_failed"})
            if ending == "completed":
                assert result["output"] == "hello"
            else:
                assert result["error"]
        finally:
            await client.close()
    asyncio.run(check())


def test_task_cancellation_stops_remote_run_and_propagates():
    async def check():
        started = asyncio.Event()
        stopped = []
        client = Hermes()

        async def start(*args, **kwargs):
            return "test-run"

        async def events(run_id):
            started.set()
            await asyncio.Event().wait()
            yield {}

        async def stop(run_id):
            stopped.append(run_id)
            return True

        client.start_run, client.events, client.stop = start, events, stop
        task = asyncio.create_task(client.delegate("hello"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped == ["test-run"] and not client.runs
    asyncio.run(check())
