"""Actual Chromium checks of production template/JS against a local progress service.

Run with RUN_OPTIONAL_QA_BROWSER=1. No provider, database or paid submission.
"""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import pytest

pytestmark=pytest.mark.skipif(os.getenv('RUN_OPTIONAL_QA_BROWSER')!='1',reason='Opt-in actual browser regression')


@pytest.fixture
def review_page(tmp_path,monkeypatch):
    from jinja2 import Environment,FileSystemLoader
    from app.features.batches import handlers
    from tests.test_semantic_video_ui import _semantic_batch
    root=Path(__file__).resolve().parents[1]
    media=tmp_path/'video.mp4'
    subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i','color=c=blue:s=180x320:d=8','-f','lavfi','-i','sine=frequency=440:duration=8','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(media)],check=True)
    progress={'run_id':'run-1','revision':1,'stage':'completed','candidate_generation_status':'idle','candidate_count':0,'total_takes':1,'generated_takes':1,'verified_takes':1,'progress_percent':100,'elapsed_seconds':0,'estimated_remaining_seconds':0,'status_message':'Video ready.','failed_take_indexes':[],'takes':[],'optional_qa':{'status':'pending','findings':[]},'final_video_url':'/media.mp4','final_caption_url':'/media.mp4'}
    state={'progress':progress,'requests':0,'fail':False}
    run={'id':'run-1','stage':'completed','revision':1,'requested_duration_seconds':8,'final_video_uri':'/media.mp4','final_video_sha256':'a'*64,'final_caption_uri':'/media.mp4','final_caption_sha256':'b'*64,'master_snapshot':{},'plan_snapshot':{'take_count':1},'artifact_manifest':{'optional_qa':progress['optional_qa'],'delivery_duration_seconds':8.0}}
    monkeypatch.setattr(handlers.semantic_video_queries,'get_run_by_post',lambda p:run)
    monkeypatch.setattr(handlers.semantic_video_queries,'list_attempts',lambda r:[])
    monkeypatch.setattr(handlers.semantic_video_queries,'list_approvals',lambda r:[])
    batch=_semantic_batch();item=handlers._build_semantic_video_post_projection(batch['posts'][0])
    partial=Environment(loader=FileSystemLoader(root/'templates'),autoescape=True).get_template('batches/detail/_semantic_video.html').render(batch=batch,batch_view={'semantic_workflow':{'current_step':{'key':'delivery'}},'semantic_video':{'posts':[item],'requested_duration_seconds':8,'duration_contract':{}}})
    def wrap(fragment):
        return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><script src="https://cdn.tailwindcss.com"></script><link rel="stylesheet" href="/brand.css"></head><body><main class="mx-auto max-w-5xl p-4">{fragment}</main><script src="/action_feedback.js"></script><script src="/semantic_video.js"></script></body></html>'
    html=wrap(partial)
    pending_item={**item,'stage':'generating','final_video_url':'','final_caption_url':'','optional_qa':None}
    sibling={**pending_item,'post_id':'post-2','run_id':'run-2'}
    pending_partial=Environment(loader=FileSystemLoader(root/'templates'),autoescape=True).get_template('batches/detail/_semantic_video.html').render(batch=batch,batch_view={'semantic_workflow':{'current_step':{'key':'production'}},'semantic_video':{'posts':[pending_item,sibling],'requested_duration_seconds':8,'duration_contract':{}}})
    pending_html=wrap(pending_partial)
    template = Environment(loader=FileSystemLoader(root/'templates'), autoescape=True).get_template(
        'batches/detail/_semantic_video.html'
    )

    def approval_html(step, cards):
        return wrap(template.render(batch=batch, batch_view={
            'semantic_workflow': {'current_step': {'key': step}},
            'semantic_video': {'posts': cards, 'requested_duration_seconds': 8, 'duration_contract': {}},
        }))

    plan_item = {
        **pending_item, 'stage': 'awaiting_paid_approval', 'plan_hash': 'shown-hash',
        'master_hash_is_current': True, 'estimated_cost_usd': '3.20',
    }
    plan_html = approval_html('plan', [plan_item, {**plan_item, 'post_id': 'post-2'}])
    retry_html = approval_html('production', [{
        **plan_item, 'stage': 'retry_approval_required', 'initial_plan_is_approved': True,
        'failed_take_indexes': [0], 'retry_provider_seconds': 8, 'retry_estimated_cost_usd': '3.20',
    }])
    script_card = Environment(loader=FileSystemLoader(root/'templates'), autoescape=True).get_template(
        'batches/detail/_semantic_script_review_card.html'
    ).render(
        batch=batch,
        post={**batch['posts'][0], 'seed_data': {'script_review_status': 'pending', 'script': 'Test script.'}},
        batch_view={'semantic_video': {'duration_contract': {}}},
    )
    script_html = wrap(f'<div id="batch-detail-root">{script_card}</div>')
    script_html = script_html.replace('</head>', '<script src="https://unpkg.com/htmx.org@1.9.10"></script></head>')
    script_html = script_html.replace('</body>', '<script src="/detail.js"></script></body>')
    scene_html = approval_html('scene', [{
        **pending_item, 'stage': 'awaiting_reference_approval', 'script_review_status': 'approved',
    }])
    pages = {'/generating': pending_html, '/plan': plan_html, '/retry': retry_html,
             '/scripts': script_html, '/scene': scene_html}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path.endswith('/progress'):
                state['requests']+=1
                payload=state['progress']
                if '/post-2/' in self.path:payload={**payload,'stage':'generating','optional_qa':None,'final_video_url':'','final_caption_url':''}
                body=json.dumps({'data':payload}).encode();kind='application/json';status=503 if state['fail'] else 200
            elif self.path=='/semantic_video.js':body=(root/'static/js/batches/semantic_video.js').read_bytes();kind='application/javascript';status=200
            elif self.path=='/action_feedback.js':body=(root/'static/js/action_feedback.js').read_bytes();kind='application/javascript';status=200
            elif self.path=='/detail.js':body=(root/'static/js/batches/detail.js').read_bytes();kind='application/javascript';status=200
            elif self.path=='/brand.css':body=(root/'static/css/brand.css').read_bytes();kind='text/css';status=200
            elif self.path=='/media.mp4':body=media.read_bytes();kind='video/mp4';status=200
            elif self.path=='/favicon.ico':body=b'';kind='image/x-icon';status=200
            else:body=pages.get(self.path, html).encode();kind='text/html';status=200
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}/',state
    server.shutdown();server.server_close();thread.join(timeout=3)


@pytest.mark.parametrize('width',[1280,390])
def test_ready_video_keeps_playing_while_optional_findings_arrive(review_page,tmp_path,width):
    from playwright.sync_api import sync_playwright,expect
    url,state=review_page
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"))
        page=browser.new_page(viewport={'width':width,'height':900},ignore_https_errors=True)
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('console',lambda message:errors.append(message.text) if message.type=='error' else None)
        page.goto(url,wait_until='networkidle')
        card=page.get_by_role('region',name='Optional quality checks')
        expect(card).to_be_visible()
        # Require real production Tailwind styles for responsive layout verification.
        page.wait_for_function("getComputedStyle(document.querySelector('[data-field=optional-qa]')).backgroundColor === 'rgb(246, 250, 255)'")
        player=page.locator('[data-field=final-player]')
        expect(player).to_be_visible();player.evaluate('(video)=>video.play()')
        assert player.evaluate('(video)=>!video.paused')
        state['progress']['optional_qa']={'status':'running','findings':[]}
        expect(card).to_contain_text('checks are running',timeout=15000)
        state['progress']['optional_qa']={'status':'completed','findings':['White subtitles detected. <script>unsafe</script>']}
        expect(card).to_contain_text('White subtitles detected. <script>unsafe</script>',timeout=15000)
        assert card.locator('script').count()==0
        expect(player).to_be_visible()
        assert page.locator('[data-field=final-caption-link]').get_attribute('href')=='/media.mp4'
        requests=state['requests'];page.wait_for_timeout(12000)
        assert state['requests']==requests
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        card.scroll_into_view_if_needed()
        page.screenshot(path=str(tmp_path/f'optional-qa-{width}.png'),full_page=True)
        assert errors==[]
        browser.close()


def test_failed_optional_checks_leave_video_available_and_stop_polling(review_page):
    from playwright.sync_api import sync_playwright,expect
    url,state=review_page
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"));page=browser.new_page(ignore_https_errors=True)
        page.goto(url,wait_until='networkidle')
        state['progress']['optional_qa']={'status':'failed','findings':[]}
        expect(page.locator('[data-field=optional-qa]')).to_contain_text('video remains available',timeout=15000)
        expect(page.locator('[data-field=final-player]')).to_be_visible()
        requests=state['requests'];page.wait_for_timeout(12000)
        assert state['requests']==requests
        assert page.locator('[data-field=generation-error]:visible').count()==0
        browser.close()


def test_one_ready_post_is_playable_while_a_sibling_is_still_generating(review_page):
    from playwright.sync_api import sync_playwright,expect
    url,state=review_page
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page=browser.new_page(ignore_https_errors=True)
        page.goto(url+'generating',wait_until='networkidle')
        first=page.locator('[data-semantic-video-controller][data-post-id=post-1]')
        sibling=page.locator('[data-semantic-video-controller][data-post-id=post-2]')
        expect(first.locator('[data-field=final-player]')).to_be_visible()
        expect(first.locator('[data-field=optional-qa]')).to_contain_text('video is ready')
        expect(sibling).to_have_attribute('data-stage','generating')
        expect(sibling.locator('[data-field=final-delivery]')).to_be_hidden()
        browser.close()


def test_optional_status_transport_failure_stops_refresh_without_blocking_playback(review_page):
    from playwright.sync_api import sync_playwright,expect
    url,state=review_page
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page=browser.new_page(ignore_https_errors=True)
        page.goto(url,wait_until='networkidle')
        state['fail']=True
        expect(page.locator('[data-field=optional-qa]')).to_contain_text('status could not be refreshed',timeout=40000)
        expect(page.locator('[data-field=final-player]')).to_be_visible()
        assert page.locator('[data-field=generation-error]:visible').count()==0
        count=state['requests'];page.wait_for_timeout(12000)
        assert state['requests']==count
        browser.close()


def test_slow_status_request_counts_once_and_refresh_has_pending_feedback(review_page):
    from playwright.sync_api import expect, sync_playwright

    url, state = review_page
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"))
        page = browser.new_page(viewport={"width": 390, "height": 900}, ignore_https_errors=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        requests = []
        page.route("**/posts/post-1/progress", lambda route: requests.append(route))
        page.add_init_script("""(() => {
            for (const name of ['setTimeout', 'setInterval']) {
                const original = window[name].bind(window);
                window[name] = (callback, delay, ...args) =>
                    original(callback, ({8000: 500, 15000: 4000})[delay] || delay, ...args);
            }
        })();""")
        page.goto(url + "generating")
        root = page.locator("[data-semantic-video-controller][data-post-id=post-1]")
        panel = root.locator("[data-field=generation-error]")
        status = root.locator("[data-field=status]")

        # Hold one request across the normal 8-second tick, inside its 15-second deadline.
        page.wait_for_timeout(650)
        assert len(requests) == 1
        requests[-1].fulfill(status=503, json={"message": "Temporarily unavailable"})
        expect(status).to_contain_text("Retrying the connection")
        assert root.get_attribute("data-progress-failures") == "1"
        expect(panel).to_be_hidden()

        for count in (2, 3):
            page.wait_for_timeout(650)
            assert len(requests) == count
            requests[-1].fulfill(status=503, json={"message": "Temporarily unavailable"})
            expect(root).to_have_attribute("data-progress-failures", str(count))
        expect(panel.get_by_role("heading", name="Video status unavailable")).to_be_visible()
        expect(root.locator("[data-action=retry-generation]")).to_be_hidden()
        page.wait_for_timeout(650)
        assert len(requests) == 3

        button = panel.get_by_role("button", name="Check status again")
        button.focus()
        page.keyboard.press("Enter")
        expect(panel.get_by_role("button", name="Checking status…")).to_be_disabled()
        assert len(requests) == 4
        requests[-1].fulfill(json={"data": {
            **state["progress"], "stage": "generating", "optional_qa": None,
        }})
        expect(panel).to_be_hidden()
        expect(status).to_have_text("Status refreshed.")
        expect(root.locator("[data-field=stage]")).to_have_text("generating")
        page.wait_for_timeout(650)
        assert len(requests) == 5
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert errors == []
        browser.close()


@pytest.mark.parametrize(
    "path,action,stage,expected_error",
    [
        ("plan", "approve-plan", "awaiting_paid_approval", "approval hash is stale"),
        ("plan", "approve-batch-plans", "awaiting_paid_approval", "hash changed before batch approval"),
        ("retry", "approve-retry", "retry_approval_required", "approval hash is stale"),
    ],
)
def test_paid_approval_uses_hash_from_the_displayed_price(review_page, path, action, stage, expected_error):
    from playwright.sync_api import expect, sync_playwright

    url, state = review_page
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"))
        page = browser.new_page(ignore_https_errors=True)
        requests = []
        confirmations = []

        def api(route):
            if route.request.method == "POST":
                requests.append(route.request.url)
                route.fulfill(status=409, json={"message": "Unexpected paid request"})
                return
            route.fulfill(json={"data": {
                **state["progress"], "stage": stage, "plan_hash": "changed-hash", "optional_qa": None,
            }})

        def confirm(dialog):
            confirmations.append(dialog.message)
            dialog.accept()

        page.on("dialog", confirm)
        page.route("**/semantic-videos/**", api)
        page.goto(url + path)
        card = page.locator("[data-semantic-video-controller]").first
        expect(card).to_have_attribute("data-plan-hash", "changed-hash")
        page.locator(f"[data-action={action}]").first.click()
        error = page.locator("[data-field=batch-plan-status]") if action == "approve-batch-plans" else card.locator("[data-field=status]")
        expect(error).to_contain_text(expected_error)
        assert len(confirmations) == 1 and "$" in confirmations[0]
        assert not requests
        browser.close()


@pytest.mark.parametrize(
    "label,pending,removes_card",
    [("Approve script", "Approving…", False), ("Remove script", "Removing…", True)],
)
def test_rejected_script_review_keeps_inline_error_and_allows_retry(review_page, label, pending, removes_card):
    from playwright.sync_api import expect, sync_playwright

    url, _ = review_page
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"))
        page = browser.new_page(ignore_https_errors=True)
        dialogs = []
        requests = []

        def on_dialog(dialog):
            dialogs.append(dialog.type)
            dialog.accept()

        page.on("dialog", on_dialog)
        page.route("**/posts/post-1/script-review", lambda route: requests.append(route))
        page.goto(url + "scripts")
        card = page.locator("#post-post-1")
        button = card.get_by_role("button", name=label, exact=True)
        button.click()
        expect(card.get_by_role("button", name=pending)).to_be_disabled()
        assert len(requests) == 1
        requests[-1].fulfill(status=422, json={"message": "Check the script length."})
        expect(card.get_by_role("status")).to_have_text("Check the script length.")
        expect(button).to_be_enabled()
        button.click()
        expect(card.get_by_role("button", name=pending)).to_be_disabled()
        assert len(requests) == 2
        requests[-1].fulfill(json={"data": {"batch_state": "S2_SEEDED"}})
        if removes_card:
            expect(card).to_have_count(0)
        else:
            expect(card.locator("[data-script-review-badge]")).to_have_text("Approved")
        assert dialogs == (["confirm", "confirm"] if removes_card else [])
        browser.close()


def test_scene_enqueue_reconciliation_shares_one_failed_progress_check(review_page):
    from playwright.sync_api import expect, sync_playwright

    url, state = review_page
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"))
        page = browser.new_page(ignore_https_errors=True)
        progress_reads, queue_writes = [], []

        def wait_for_route(requests, count):
            # Playwright emits request before it dispatches the route callback.
            for _ in range(60):
                if len(requests) >= count:
                    return requests[count - 1]
                page.wait_for_timeout(50)
            pytest.fail(f"Expected {count} routed requests, got {len(requests)}")

        def read_progress(route):
            progress_reads.append(route)
            if len(progress_reads) == 1:
                route.fulfill(json={"data": {
                    **state["progress"], "stage": "awaiting_reference_approval", "optional_qa": None,
                }})

        page.route("**/posts/post-1/progress", read_progress)
        page.route("**/posts/post-1/scene-image", lambda route: queue_writes.append(route))
        page.add_init_script("""(() => {
            for (const name of ['setTimeout', 'setInterval']) {
                const original = window[name].bind(window);
                window[name] = (callback, delay, ...args) =>
                    original(callback, delay === 2000 ? 200 : delay, ...args);
            }
        })();""")
        page.goto(url + "scene")
        card = page.locator("[data-semantic-video-controller]")
        expect(card).to_have_attribute("data-progress-failures", "0")
        with page.expect_request("**/posts/post-1/progress"):
            card.locator("[data-action=generate-candidates]").click()
        wait_for_route(progress_reads, 2).fulfill(status=503, json={"message": "Temporarily unavailable"})
        expect(card).to_have_attribute("data-progress-failures", "1")

        with page.expect_request("**/posts/post-1/progress"):
            pass
        wait_for_route(queue_writes, 1).fulfill(status=503, json={"message": "Queue acknowledgement unavailable"})
        # Let the scene POST's rejection enter reconciliation while this GET is still open.
        page.wait_for_timeout(75)
        wait_for_route(progress_reads, 3).fulfill(status=503, json={"message": "Temporarily unavailable"})
        expect(card).to_have_attribute("data-progress-failures", "2")
        assert card.get_attribute("data-generation-blocked") == "false"

        with page.expect_request("**/posts/post-1/scene-image"):
            pass
        wait_for_route(queue_writes, 2).fulfill(json={"data": {"job_id": "scene-job", "revision": 2}})
        expect(card).to_have_attribute("data-scene-image-job-id", "scene-job")
        wait_for_route(progress_reads, 4).fulfill(json={"data": {
            **state["progress"], "stage": "scene_image_generating",
            "revision": 2, "scene_image_job_id": "scene-job",
            "candidate_generation_status": "generating", "optional_qa": None,
        }})
        expect(card).to_have_attribute("data-stage", "scene_image_generating")
        expect(card).to_have_attribute("data-progress-failures", "0")
        browser.close()
