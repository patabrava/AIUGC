from __future__ import annotations

import copy
import asyncio
from pathlib import Path
import re
import shutil
import subprocess
import threading
from unittest.mock import MagicMock

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
import pytest

from app.core.errors import ErrorCode, FlowForgeException
from app.features.blog import handlers, queries, sources


@pytest.mark.parametrize("all_missing", [False, True])
def test_preview_reads_filters_and_never_changes_saved_draft(monkeypatch, all_missing):
    refs = [{"title": "Old reference", "url": "https://journal.de/missing"}]
    if not all_missing:
        refs += [{"title": "Printed study"}, {"title": "Temporarily blocked", "url": "https://journal.de/blocked"}]
    row = {"id": "post-1", "topic_title": "Saved draft", "seed_data": {}, "blog_status": "draft",
           "blog_content": {"name": "Saved draft", "body_html": "<p>Article remains unchanged.</p>", "sources": refs},
           "blog_published_at": None, "blog_scheduled_at": None, "blog_webflow_item_id": None}
    original = copy.deepcopy(row)
    db = MagicMock()
    db.client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [copy.deepcopy(row)]
    monkeypatch.setattr(queries, "get_supabase", lambda: db)
    monkeypatch.setattr(sources, "_request_status", lambda url, method, timeout: (404 if "missing" in url else 403, None))
    assert queries.get_blog_preview_source_names("post-1") == ([] if all_missing else ["Printed study", "Temporarily blocked"])
    assert row == original
    db.client.table.return_value.update.assert_not_called()
    assert all(call.args[0] == "posts" for call in db.client.table.call_args_list)


def test_preview_http_boundary_is_get_only_names_only_and_offloaded(monkeypatch):
    request_thread = threading.get_ident()
    worker_threads = []

    def read_preview(post_id):
        worker_threads.append(threading.get_ident())
        assert post_id == "post-1"
        return ["Studie <B> & C"]

    monkeypatch.setattr(handlers, "get_blog_preview_source_names", read_preview)
    api = FastAPI()
    api.include_router(handlers.router)
    client = TestClient(api)
    response = client.get("/blog/posts/post-1/blog/sources-preview")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": {"post_id": "post-1", "sources": ["Studie <B> & C"]}}
    assert response.headers["cache-control"] == "no-store"
    assert worker_threads and worker_threads[0] != request_thread
    asyncio.run(handlers.preview_blog_sources("post-1", Response()))
    assert worker_threads[-1] != request_thread
    assert client.post("/blog/posts/post-1/blog/sources-preview").status_code == 405


def test_preview_preserves_missing_post_error(monkeypatch):
    def missing(_post_id):
        raise FlowForgeException(code=ErrorCode.NOT_FOUND, message="Post not found", status_code=404)

    monkeypatch.setattr(queries, "_load_post_for_blog", missing)
    with pytest.raises(FlowForgeException) as exc:
        queries.get_blog_preview_source_names("missing")
    assert exc.value.status_code == 404


def test_preview_component_loading_retry_empty_cache_and_stale_response():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the production component regression")
    template = Path("templates/batches/detail/_blog_panel.html").read_text()
    script = re.search(r"<script>(.*?)</script>", template, re.S).group(1)
    scenarios = r'''
const component = blogPanelComponent();
component.initPosts([{id:'first',blog_content:{sources:[{title:'Invalid old source',url:'https://example.com'}]}},
                     {id:'second',blog_content:{sources:[]}}]);
const post = component.posts[0];
const original = JSON.stringify(post.blog_content);
let resolveFirst;
let calls = [];
globalThis.fetch = (url,options) => {
  calls.push({url,options});
  return new Promise(resolve => { resolveFirst = resolve; });
};
component.toggleExpand('first');
assert.equal(post.sourcePreview.status,'loading');
assert.deepEqual(post.sourcePreview.names,[]);
assert.equal(calls.length,1);
assert.equal(calls[0].options.cache,'no-store');
component.toggleExpand('first');
component.toggleExpand('first');
assert.equal(calls.length,1);
resolveFirst({ok:true,json:async()=>({ok:true,data:{post_id:'first',sources:['Retained <study>']}})});
await new Promise(resolve=>setImmediate(resolve));
assert.equal(post.sourcePreview.status,'ready');
assert.deepEqual(post.sourcePreview.names,['Retained <study>']);
assert.equal(JSON.stringify(post.blog_content),original);
component.toggleExpand('first');component.toggleExpand('first');
assert.equal(calls.length,1);
const second=component.posts[1];
globalThis.fetch=async()=>{throw new Error('offline')};
await component.loadSourcePreview(second);
assert.equal(second.sourcePreview.status,'error');
assert.deepEqual(second.sourcePreview.names,[]);
globalThis.fetch=async()=>({ok:true,json:async()=>({ok:true,data:{sources:[]}})});
await component.loadSourcePreview(second);
assert.equal(second.sourcePreview.status,'ready');
assert.deepEqual(second.sourcePreview.names,[]);
// In-flight old checks must never replace a regenerated draft's preview state.
second.sourcePreview={status:'idle',names:[]};
let resolveOld;
globalThis.fetch=()=>new Promise(resolve=>{resolveOld=resolve});
const pending=component.loadSourcePreview(second);
second.sourcePreview={status:'idle',names:[]};
resolveOld({ok:true,json:async()=>({ok:true,data:{sources:['Old result']}})});
await pending;
assert.equal(second.sourcePreview.status,'idle');
assert.deepEqual(second.sourcePreview.names,[]);
globalThis.fetch=async()=>({ok:true,json:async()=>({ok:true,data:{sources:[{url:'https://example.com'}]}})});
await component.loadSourcePreview(second);
assert.equal(second.sourcePreview.status,'error');
assert.deepEqual(second.sourcePreview.names,[]);
'''
    result = subprocess.run([node, "--input-type=module"], input="import assert from 'node:assert/strict';\nglobalThis.window={};\n" + script + scenarios,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
