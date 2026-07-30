import asyncio

from workers import publish_scheduler


def test_run_cycle_dispatches_social_and_blog_schedules(monkeypatch):
    calls: list[str] = []

    async def social_job():
        calls.append("social")
        return {"processed": 1}

    async def blog_job():
        calls.append("blog")
        return {"processed": 2}

    monkeypatch.setattr(publish_scheduler, "run_scheduled_publish_job", social_job)
    monkeypatch.setattr(publish_scheduler, "run_scheduled_blog_publish_job", blog_job)

    social, blog = asyncio.run(publish_scheduler.run_cycle())

    assert set(calls) == {"social", "blog"}
    assert social == {"processed": 1}
    assert blog == {"processed": 2}


def test_run_cycle_keeps_other_queue_alive_after_failure(monkeypatch):
    async def social_job():
        raise RuntimeError("social unavailable")

    async def blog_job():
        return {"processed": 1}

    monkeypatch.setattr(publish_scheduler, "run_scheduled_publish_job", social_job)
    monkeypatch.setattr(publish_scheduler, "run_scheduled_blog_publish_job", blog_job)

    social, blog = asyncio.run(publish_scheduler.run_cycle())

    assert social == {"processed": 0, "error": "social unavailable"}
    assert blog == {"processed": 1}
