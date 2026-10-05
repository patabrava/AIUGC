"""Direct ModelArk wire, money, moderation and acknowledgement safety contracts."""
import json
from types import SimpleNamespace
import httpx
import pytest
from app.adapters.modelark_client import ModelArkClient, MODEL, BASE_URL, estimate_cost, cost_units
from app.adapters.video_provider_transport import VideoProviderError

KEY = "secret-modelark-test-key"
TASK = "cgt-20261005-abcdef"


def client(handler):
    return ModelArkClient(api_key=KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                          output_allowed_hosts=("output.test",))


def submit(c, **changes):
    values = dict(prompt_image="data:image/png;base64,YQ==", prompt_text="The reference subject speaks German.",
                  ratio="480:854", duration_seconds=8, audio=True, seed=23, correlation_id="test")
    values.update(changes)
    return c.submit_image_to_video(**values)


def test_direct_request_and_reference_role():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": TASK})
    c = client(handler)
    assert submit(c)["task_id"] == TASK
    request = seen[0]
    assert str(request.url) == BASE_URL + "/contents/generations/tasks"
    assert request.headers["authorization"] == "Bearer " + KEY
    assert "x-runway-version" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == MODEL
    assert (body["duration"], body["resolution"], body["ratio"], body["generate_audio"]) == (8, "480p", "9:16", True)
    assert body["content"][1]["role"] == "first_frame"
    submit(c, prompt_image="asset://asset-approved", reference_only=True)
    assert json.loads(seen[1].content)["content"][1]["role"] == "reference_image"
    assert KEY not in repr(c)


@pytest.mark.parametrize("status,kind", [(400,"rejected"),(401,"rejected"),(403,"rejected"),(429,"rate_limited"),(500,"ambiguous"),(302,"ambiguous")])
def test_submission_outcomes_are_not_retried(status, kind):
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={"error":{"code":"SensitiveContentDetected", "message":KEY}})
    with pytest.raises(VideoProviderError) as caught:
        submit(client(handler))
    assert caught.value.kind == kind
    assert len(seen) == 1
    assert KEY not in str(caught.value) and KEY not in str(caught.value.details)


def test_timeout_after_write_is_ambiguous():
    def handler(request): raise httpx.ReadTimeout("timeout", request=request)
    with pytest.raises(VideoProviderError) as caught: submit(client(handler))
    assert caught.value.kind == "ambiguous"


@pytest.mark.parametrize("body", [{}, {"id":"invalid"}, []])
def test_unreadable_acknowledgement_is_ambiguous(body):
    with pytest.raises(VideoProviderError) as caught: submit(client(lambda r:httpx.Response(200,json=body)))
    assert caught.value.kind == "ambiguous"


def test_task_usage_cost_and_terminal_zero():
    c=client(lambda r:httpx.Response(200,json={"id":TASK,"model":MODEL,"status":"succeeded",
        "usage":{"completion_tokens":76860},"content":{"video_url":"https://output.test/clip.mp4"}}))
    task=c.get_task(TASK,correlation_id="test")
    assert task.cost_credits == 822402 and task.usage_tokens == 76860
    assert task.status == "SUCCEEDED"
    c=client(lambda r:httpx.Response(200,json={"id":TASK,"status":"failed",
        "error":{"code":"SensitiveContentDetected","message":KEY}}))
    task=c.get_task(TASK,correlation_id="test")
    assert task.cost_credits == 0 and task.failure_code == "SensitiveContentDetected"
    assert KEY not in task.failure


def test_unknown_model_fails_closed():
    c=client(lambda r:httpx.Response(200,json={"id":TASK,"status":"succeeded","model":"other"}))
    with pytest.raises(VideoProviderError):c.get_task(TASK,correlation_id="test")


def test_pricing_is_microdollars_and_reserves_alignment_margin():
    pricing=estimate_cost("480p",8,SimpleNamespace())
    assert pricing["expected_tokens"] == 76860
    assert pricing["expected_units"] == 822402
    assert pricing["reserved_units"] == 945763
    assert cost_units(1) == 11
    assert pricing["unit"] == "usd_microdollar"


def test_api_credentials_cannot_be_redirected_to_other_hosts():
    with pytest.raises(VideoProviderError):ModelArkClient(api_key=KEY,base_url="https://attacker.test/api/v3")
    with pytest.raises(VideoProviderError):submit(client(lambda r:pytest.fail("No HTTP expected")), seed=2**31)


def test_output_download_never_sends_key_and_revalidates_redirects():
    seen=[]
    def handler(r):
        seen.append(r)
        return httpx.Response(302,headers={"location":"https://attacker.test/clip.mp4"})
    with pytest.raises(VideoProviderError):client(handler).download_output("https://output.test/clip.mp4",correlation_id="test")
    assert len(seen)==1 and "authorization" not in seen[0].headers


@pytest.mark.parametrize("status,confirmed", [("running", False), ("queued", False), ("succeeded", False), ("cancelled", True)])
def test_cancel_never_deletes_a_provider_task_record(status, confirmed):
    seen=[]
    def handler(request):
        seen.append(request.method)
        return httpx.Response(200,json={"id":TASK,"status":status})
    assert client(handler).cancel_task(TASK,correlation_id="test") is confirmed
    assert seen == ["GET"]
