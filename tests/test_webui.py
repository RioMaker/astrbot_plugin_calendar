"""Real browser tests against the same domain service via a local preview bridge.

These do not claim to exercise a live AstrBot dashboard or OneBot deployment.
"""

import json
import os
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from scripts.preview import create_server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    executable = os.environ.get("CALENDAR_TEST_BROWSER")
    candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    ]
    if executable is None:
        executable = next((str(p) for p in candidates if p.exists()), None)
    with sync_playwright() as engine:
        instance = engine.chromium.launch(
            headless=True, **({"executable_path": executable} if executable else {})
        )
        yield instance
        instance.close()


@pytest.fixture
def calendar_page(browser, tmp_path):
    server = create_server(tmp_path / "browser.sqlite3", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}")
        expect(page.locator("#days td")).to_have_count(42, timeout=15000)
        jump(page, 2026, 11)
        yield page
    finally:
        context.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not errors, errors


def jump(page, year, month):
    page.locator("#jump-year").fill(str(year))
    page.locator("#jump-month").select_option(str(month))
    # The requested month may already be displayed. Wait for its actual response,
    # rather than accepting the unchanged old heading before navigation finishes.
    owner_page = page.locator("#jump-form").page
    with owner_page.expect_response(
        lambda response: "/api/month?" in response.url
    ) as response:
        page.locator("#jump-form button").click()
    response.value.finished()
    expect(page.locator("#calendar-caption")).to_have_text(f"{year}年{month}月")


def day(page, key="2026-11-11"):
    return page.locator(f'#days .day-number[data-target="{key}"]')


def saved(page):
    expect(page.locator("#save-status")).to_have_text("✓ 已保存", timeout=15000)


def close_detail(page):
    page.locator(".drawer-header .close-detail").click()
    expect(page.locator("#detail-dialog")).not_to_be_visible()


def test_three_rings_rapid_clicks_eraser_navigation_and_refresh(calendar_page):
    page = calendar_page
    page.locator('#pens button[data-pen="income"]').click()
    for _ in range(4):
        day(page).click()
    saved(page)
    expect(day(page).locator(".mark-shape")).to_have_count(3)
    expect(page.locator("#detail-dialog")).not_to_be_visible()
    page.locator("#year-target").click()
    page.locator("#month-target").click()
    saved(page)
    expect(page.locator("#year-target .mark-shape")).to_have_count(1)
    expect(page.locator("#month-target .mark-shape")).to_have_count(1)
    page.locator("#eraser").click()
    day(page).click()
    saved(page)
    expect(day(page).locator(".mark-shape")).to_have_count(2)
    page.locator("#next").click()
    expect(page.locator("#calendar-caption")).to_have_text("2026年12月")
    expect(page.locator("#month-target .mark-shape")).to_have_count(0)
    page.locator("#prev").click()
    expect(page.locator("#calendar-caption")).to_have_text("2026年11月")
    expect(day(page).locator(".mark-shape")).to_have_count(2)
    page.reload()
    expect(day(page).locator(".mark-shape")).to_have_count(2)
    day(page).click()
    expect(page.locator("#detail-dialog")).to_be_visible()


def test_create_custom_pen_and_edit_existing_shape(calendar_page):
    page = calendar_page
    page.locator("#add-pen").click()
    page.locator("#pen-name").fill("重要日")
    page.locator("#pen-shape").select_option("diamond")
    page.locator("#pen-color").fill("#2852aa")
    page.locator('#pen-form button[type="submit"]').click()
    expect(page.locator("#pen-list")).to_contain_text("重要日")
    page.locator("#close-settings").click()
    day(page).click()
    saved(page)
    expect(day(page).locator("polygon.mark-shape")).to_have_count(1)
    expect(day(page).locator(".mark-shape")).to_have_attribute("stroke", "#2852aa")
    page.locator("#manage-pens").click()
    row = page.locator("#pen-list .manager-row").filter(has_text="重要日")
    row.get_by_role("button", name="编辑", exact=True).click()
    page.locator("#pen-shape").select_option("square")
    page.locator('#pen-form button[type="submit"]').click()
    saved(page)
    page.locator("#close-settings").click()
    expect(day(page).locator("rect.mark-shape")).to_have_count(1)


def add_note(page, text, color="#658773"):
    page.locator("#note-text").fill(text)
    page.locator("#note-color").fill(color)
    page.locator('#note-form button[type="submit"]').click()
    expect(page.locator("#note-text")).to_have_value("")


def test_multiple_notes_tooltip_safe_text_edit_color_and_delete(calendar_page):
    page = calendar_page
    day(page).click()
    add_note(page, "收到项目款\n第二笔到账", "#ce4545")
    add_note(page, '<img src=x onerror="window.noteInjected=true">', "#7761a4")
    add_note(page, "记得复盘", "#b78945")
    expect(page.locator("#note-list .note-card")).to_have_count(3)
    expect(page.locator("#note-list img")).to_have_count(0)
    assert page.evaluate("window.noteInjected") is None
    close_detail(page)
    cell = page.locator('td[data-target="2026-11-11"]')
    expect(cell.locator(".note-bar")).to_have_count(2)
    expect(cell.locator(".more-notes")).to_have_text("还有 1 条备注")
    cell.locator(".note-bar").first.hover()
    expect(page.locator("#tooltip")).to_be_visible()
    expect(page.locator("#tooltip")).to_have_text("收到项目款\n第二笔到账")
    cell.locator(".note-bar").first.click()
    expect(page.locator("#note-text")).to_have_value("收到项目款\n第二笔到账")
    page.locator("#note-text").fill("项目款已全部到账")
    page.locator("#note-color").fill("#2456ab")
    page.locator('#note-form button[type="submit"]').click()
    expect(page.locator("#note-text")).to_have_value("")
    expect(page.locator("#note-list .note-card").first).to_contain_text(
        "项目款已全部到账"
    )
    page.locator("#note-list [data-delete-note]").last.click()
    page.locator("#confirm-yes").click()
    expect(page.locator("#note-list .note-card")).to_have_count(2)
    close_detail(page)
    expect(cell.locator(".note-bar").first).to_have_attribute(
        "style", "--note-color:#2456ab"
    )
    page.reload()
    expect(cell.locator(".note-bar").first).to_have_text("项目款已全部到账")


def create_field(page, name, kind, options=""):
    page.locator("#new-field").click()
    page.locator("#field-name").fill(name)
    page.locator("#field-kind").select_option(kind)
    if kind == "select":
        page.locator("#field-options").fill(options)
    page.locator('#field-form button[type="submit"]').click()
    expect(page.locator("#field-list")).to_contain_text(name)


def test_month_day_custom_states_festival_and_manual_holiday(calendar_page):
    page = calendar_page
    page.locator("#settings").click()
    page.locator('[data-tab="fields"]').click()
    create_field(page, "财务状态", "select", "待记录、已确认")
    create_field(page, "已复盘", "boolean")
    create_field(page, "心情", "text")
    page.locator("#close-settings").click()
    day(page).click()
    page.locator("#custom-values").get_by_label("财务状态").select_option("已确认")
    page.locator("#custom-values").get_by_label("已复盘").select_option("false")
    page.locator("#custom-values").get_by_label("心情").fill("平静")
    page.locator("#custom-festivals").fill("纪念日")
    page.locator("#holiday-status").select_option("holiday")
    page.locator("#holiday-name").fill("公司安排")
    page.locator('#target-form button[type="submit"]').click()
    saved(page)
    expect(page.locator("#date-facts")).to_contain_text("公司安排（手动设置）")
    close_detail(page)
    cell = page.locator('td[data-target="2026-11-11"]')
    expect(cell.locator(".holiday-badge")).to_have_text("休")
    expect(cell.locator(".lunar-label")).to_have_text("纪念日")
    page.locator("#month-target").click()
    expect(page.locator("#day-overrides")).not_to_be_visible()
    page.locator("#custom-values").get_by_label("财务状态").select_option("待记录")
    page.locator('#target-form button[type="submit"]').click()
    saved(page)
    close_detail(page)
    expect(page.locator("#period-notes")).to_contain_text("财务状态：待记录")
    day(page).click()
    expect(page.locator("#custom-values").get_by_label("已复盘")).to_have_value("false")


def test_calendar_settings_unknown_year_and_week_start(calendar_page):
    page = calendar_page
    page.locator("#settings").click()
    page.locator("#settings-name").fill("我的流年日历")
    page.locator("#week-start").select_option("6")
    page.locator("#year-boundary").select_option("lichun")
    page.locator('#settings-form button[type="submit"]').click()
    expect(page.locator("#book-title")).to_have_text("我的流年日历")
    saved(page)
    page.locator("#close-settings").click()
    expect(page.locator("#weekdays th").first).to_have_text("周日")
    expect(page.locator("#days td").first).to_have_attribute(
        "data-target", "2026-11-01"
    )
    expect(page.locator("#month-summary")).to_contain_text("立春日")
    jump(page, 2099, 1)
    expect(page.locator("#coverage-warning")).to_be_visible()
    expect(page.locator("#days .holiday-badge")).to_have_count(0)


def test_conflict_keeps_draft_and_can_refresh_inside_dialog(calendar_page):
    page = calendar_page
    other = page.context.new_page()
    other.goto(page.url)
    expect(other.locator("#days td")).to_have_count(42)
    day(page).click()
    page.locator("#note-text").fill("不能丢失的输入")
    other.locator('#pens button[data-pen="income"]').click()
    day(other).click()
    saved(other)
    page.locator('#note-form button[type="submit"]').click()
    error = page.locator("#detail-dialog [data-inline-error]")
    expect(error).to_be_visible()
    expect(error).to_contain_text("另一页面更新")
    expect(page.locator("#note-text")).to_have_value("不能丢失的输入")
    error.get_by_role("button", name="刷新数据，保留输入").click()
    expect(error).not_to_be_visible()
    expect(page.locator("#note-text")).to_have_value("不能丢失的输入")
    page.locator('#note-form button[type="submit"]').click()
    expect(page.locator("#note-list")).to_contain_text("不能丢失的输入")
    expect(page.locator("#detail-marks")).to_contain_text("得财 ×1")
    other.close()


def test_unsaved_note_close_can_be_cancelled(calendar_page):
    page = calendar_page
    day(page).click()
    page.locator("#note-text").fill("未保存的文字")
    page.locator(".drawer-header .close-detail").click()
    page.locator("#confirm-no").click()
    expect(page.locator("#detail-dialog")).to_be_visible()
    expect(page.locator("#note-text")).to_have_value("未保存的文字")
    page.locator(".drawer-header .close-detail").click()
    page.locator("#confirm-yes").click()
    expect(page.locator("#detail-dialog")).not_to_be_visible()


def test_mobile_keeps_standard_table_without_document_overflow(calendar_page):
    page = calendar_page
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator("#weekdays th")).to_have_count(7)
    expect(page.locator("#days tr")).to_have_count(6)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator(".calendar-scroll").evaluate(
        "node => node.scrollWidth > node.clientWidth"
    )
    day(page).click()
    expect(page.locator("#detail-dialog")).to_be_visible()
    assert page.locator("#detail-dialog").evaluate(
        "node=>node.getBoundingClientRect().width <= innerWidth"
    )
    close_detail(page)
    out = ROOT / ".preview"
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / "mobile.png"), full_page=True)


def test_export_download_and_inert_script_text(calendar_page, tmp_path):
    page = calendar_page
    day(page).click()
    add_note(page, "需要保留的记录")
    close_detail(page)
    with page.expect_download() as info:
        page.locator("#export").click()
    download = info.value
    path = tmp_path / "export.json"
    download.save_as(path)
    import json

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["format"] == "liji-calendar"
    assert record["items"]["2026-11-11"]["notes"][0]["text"] == "需要保留的记录"


def test_astrbot_sandbox_without_native_modals_or_same_origin(calendar_page):
    page = calendar_page
    base = page.url.rstrip("/")
    host_html = """<!doctype html><meta charset="utf-8">
    <script src="/preview-bridge.js"></script>
    <iframe id="calendar-frame" src="/sandbox-frame"
      sandbox="allow-scripts allow-forms allow-downloads" style="width:1200px;height:1000px"></iframe>
    <script>
    addEventListener('message',async event=>{
      if(event.source!==document.querySelector('iframe').contentWindow || event.data.type!=='calendar-test')return;
      const {id,method,args}=event.data;
      try {const value=await window.AstrBotPluginView[method](...args);event.source.postMessage({id,value},'*');}
      catch(error){event.source.postMessage({id,error:error.message},'*');}
    });
    </script>"""
    frame_bridge = """<script>
    let requestId=0;const waiting=new Map();
    addEventListener('message',event=>{
      if(event.source!==parent)return;const callback=waiting.get(event.data.id);if(!callback)return;
      waiting.delete(event.data.id);event.data.error?callback.reject(new Error(event.data.error)):callback.resolve(event.data.value);
    });
    const call=(method,...args)=>new Promise((resolve,reject)=>{
      const id=++requestId;waiting.set(id,{resolve,reject});parent.postMessage({type:'calendar-test',id,method,args},'*');
    });
    window.AstrBotPluginPage={ready:async()=>({isDark:false}),apiGet:(...args)=>call('apiGet',...args),apiPost:(...args)=>call('apiPost',...args)};
    </script>"""
    html = (ROOT / "pages/calendar/index.html").read_text(encoding="utf-8")
    html = html.replace('<script type="module"', frame_bridge + '<script type="module"')
    page.context.route(
        "**/sandbox-host",
        lambda route: route.fulfill(body=host_html, content_type="text/html"),
    )
    page.context.route(
        "**/sandbox-frame",
        lambda route: route.fulfill(body=html, content_type="text/html"),
    )

    def static_with_cors(route):
        response = route.fetch()
        route.fulfill(
            response=response,
            headers={**response.headers, "Access-Control-Allow-Origin": "*"},
        )

    for asset in ["app.js", "render.js", "style.css"]:
        page.context.route(f"**/{asset}", static_with_cors)
    page.goto(base + "/sandbox-host")
    frame = page.frame_locator("#calendar-frame")
    expect(frame.locator("#days td")).to_have_count(42, timeout=15000)
    jump(frame, 2026, 11)
    day(frame).click()
    add_note(frame, "受限页面中的备注")
    frame.locator("#note-list [data-delete-note]").click()
    expect(frame.locator("#confirm-dialog")).to_be_visible()
    frame.locator("#confirm-yes").click()
    expect(frame.locator("#note-list .note-card")).to_have_count(0)
    add_note(frame, "可以导出的备注")
    close_detail(frame)
    frame.locator('#pens button[data-pen="income"]').click()
    day(frame).click()
    saved(frame)
    expect(day(frame).locator(".mark-shape")).to_have_count(1)
    with page.expect_download() as download:
        frame.locator("#export").click()
    assert download.value.suggested_filename.endswith(".json")
    frame.locator("#open-import").click()
    frame.locator("#import-template-details summary").click()
    with page.expect_download() as template_download:
        frame.locator("#download-import-template").click()
    assert "批量导入模板" in template_download.value.suggested_filename
    # Clipboard permission is commonly unavailable inside AstrBot's opaque iframe.
    frame.locator("#copy-import-prompt").click()
    expect(frame.locator("#toast")).to_contain_text("手动复制")
    assert (
        frame.locator("#import-prompt").evaluate(
            "node => node.selectionEnd - node.selectionStart"
        )
        > 100
    )
    frame.locator("#import-template-details summary").click()
    frame.locator("#import-text").fill(import_source())
    frame.locator("#preview-import").click()
    expect(frame.locator("#import-preview")).to_be_visible()
    frame.locator("#commit-import").click()
    expect(frame.locator("#import-result")).to_contain_text("导入完成")
    frame.locator("#close-import").click()
    expect(day(frame).locator(".mark-shape")).to_have_count(2)


def import_source():
    return json.dumps(
        {
            "format": "liji-import-v1",
            "entries": [
                {
                    "date": "2026-11-11",
                    "marks": [{"pen": "得财", "layers": 2}],
                    "events": [
                        {
                            "text": "计划：复盘 <script>window.importExecuted=true</script>"
                        }
                    ],
                },
                {
                    "date": "2026-12-01",
                    "marks": [{"pen": "破财"}],
                    "events": [{"text": "计划：整理账单", "color": "#aabbcc"}],
                },
            ],
        },
        ensure_ascii=False,
    )


def test_batch_import_preview_repeat_cross_month_and_edit_invalidation(calendar_page):
    page = calendar_page
    page.locator("#open-import").click()
    page.locator("#import-text").fill("```json\n" + import_source() + "\n```")
    page.locator("#preview-import").click()
    expect(page.locator("#import-summary")).to_contain_text("新增 2 个事件")
    expect(page.locator("#import-rows")).to_contain_text("2026-12-01")
    # Preview alone has no effect on the visible calendar or storage.
    expect(day(page).locator(".mark-shape")).to_have_count(0)
    assert page.evaluate("window.importExecuted") is None
    page.locator("#import-text").fill(import_source() + " ")
    expect(page.locator("#commit-import")).to_be_disabled()
    expect(page.locator("#import-preview")).not_to_be_visible()
    page.locator("#preview-import").click()
    expect(page.locator("#commit-import")).to_be_enabled()
    page.locator("#commit-import").click()
    expect(page.locator("#import-result")).to_contain_text("导入完成")
    page.locator("#preview-import").click()
    expect(page.locator("#import-summary")).to_contain_text("跳过 4 个")
    page.locator("#commit-import").click()
    expect(page.locator("#import-result")).to_contain_text("新增 0 个事件")
    page.locator("#close-import").click()
    expect(day(page).locator(".mark-shape")).to_have_count(2)
    day(page).click()
    expect(page.locator("#note-list .note-card")).to_have_count(1)
    close_detail(page)
    jump(page, 2026, 12)
    expect(day(page, "2026-12-01").locator(".mark-shape")).to_have_count(1)
    page.reload()
    jump(page, 2026, 12)
    day(page, "2026-12-01").click()
    expect(page.locator("#note-list")).to_contain_text("计划：整理账单")


def test_batch_import_errors_preserve_text_and_no_partial_changes(calendar_page):
    page = calendar_page
    page.locator("#open-import").click()
    invalid = import_source().replace("2026-12-01", "2026-02-30")
    page.locator("#import-text").fill(invalid)
    page.locator("#preview-import").click()
    expect(page.locator("#import-error")).to_contain_text("第 2 条记录")
    expect(page.locator("#commit-import")).to_be_disabled()
    expect(page.locator("#import-text")).to_have_value(invalid)
    page.locator("#close-import").click()
    expect(day(page).locator(".mark-shape")).to_have_count(0)
    page.locator("#open-import").click()
    expect(page.locator("#import-text")).to_have_value(invalid)
    page.locator("#import-text").fill(import_source().replace("得财", "不存在的画笔"))
    page.locator("#preview-import").click()
    expect(page.locator("#import-error")).to_contain_text("不存在")
    page.route("**/api/month?*", lambda route: route.abort(), times=1)
    page.locator("#refresh-import").click()
    expect(page.locator("#import-error")).to_contain_text("Failed to fetch")
    expect(page.locator("#commit-import")).to_be_disabled()


def test_batch_import_stale_preview_conflict_refresh_and_retry(calendar_page):
    page = calendar_page
    other = page.context.new_page()
    other.goto(page.url)
    expect(other.locator("#days td")).to_have_count(42)
    jump(other, 2026, 11)
    page.locator("#open-import").click()
    page.locator("#import-text").fill(import_source())
    page.locator("#preview-import").click()
    expect(page.locator("#commit-import")).to_be_enabled()
    other.locator('#pens button[data-pen="expense"]').click()
    day(other).click()
    saved(other)
    page.locator("#commit-import").click()
    expect(page.locator("#import-error")).to_contain_text("另一页面更新")
    expect(page.locator("#import-text")).to_have_value(import_source())
    expect(page.locator("#commit-import")).to_be_disabled()
    page.locator("#refresh-import").click()
    expect(page.locator("#import-error")).not_to_be_visible()
    page.locator("#preview-import").click()
    expect(page.locator("#commit-import")).to_be_enabled()
    page.locator("#commit-import").click()
    expect(page.locator("#import-result")).to_contain_text("导入完成")
    page.locator("#close-import").click()
    expect(day(page).locator(".mark-shape")).to_have_count(3)
    other.close()


def test_batch_import_lost_response_can_retry_without_duplicates(calendar_page):
    page = calendar_page
    page.locator("#open-import").click()
    page.locator("#import-text").fill(import_source())
    page.locator("#preview-import").click()
    expect(page.locator("#commit-import")).to_be_enabled()

    def lose_response(route):
        route.fetch()
        route.abort()

    page.route("**/api/import", lose_response, times=1)
    page.locator("#commit-import").click()
    expect(page.locator("#import-error")).to_be_visible()
    expect(page.locator("#import-text")).to_have_value(import_source())
    page.locator("#refresh-import").click()
    page.locator("#preview-import").click()
    expect(page.locator("#import-summary")).to_contain_text("跳过 4 个")
    page.locator("#commit-import").click()
    expect(page.locator("#import-result")).to_contain_text("新增 0 个事件")
    page.locator("#close-import").click()
    day(page).click()
    expect(page.locator("#note-list .note-card")).to_have_count(1)


def test_batch_template_uses_current_catalog_and_mobile_preview(
    calendar_page, tmp_path
):
    page = calendar_page
    page.locator("#manage-pens").click()
    page.locator('[data-edit-pen="income"]').click()
    page.locator("#pen-name").fill("重要安排")
    page.locator('#pen-form button[type="submit"]').click()
    saved(page)
    page.locator("#close-settings").click()
    page.locator("#open-import").click()
    page.locator("#import-template-details summary").click()
    prompt = page.locator("#import-prompt").input_value()
    assert "重要安排" in prompt and '"得财"' not in prompt
    with page.expect_download() as download:
        page.locator("#download-import-template").click()
    path = tmp_path / "template.json"
    download.value.save_as(path)
    template = path.read_text(encoding="utf-8")
    assert json.loads(template)["entries"][0]["marks"][0]["pen"] == "重要安排"
    page.locator("#import-template-details summary").click()
    page.locator("#import-text").fill(template)
    page.locator("#preview-import").click()
    expect(page.locator("#commit-import")).to_be_enabled()
    out = ROOT / ".preview"
    out.mkdir(exist_ok=True)
    page.locator("#import-dialog").screenshot(path=str(out / "import-desktop.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator("#import-dialog").evaluate(
        "node => node.scrollWidth <= node.clientWidth"
    )
    page.locator("#commit-import").scroll_into_view_if_needed()
    page.screenshot(path=str(out / "import-mobile.png"))
    page.evaluate("document.documentElement.dataset.theme='dark'")
    page.screenshot(path=str(out / "import-dark.png"), animations="disabled")
    page.locator("#commit-import").click()
    expect(page.locator("#import-result")).to_contain_text("导入完成")
