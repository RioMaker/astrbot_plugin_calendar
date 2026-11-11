import {
  escapeHTML as e,
  blankItem,
  markerBadges,
  markerLabel,
  penGlyph,
  noteBar,
  stateText,
  dayCell,
  effectiveHoliday,
  statusNames,
  shapeNames,
} from "./render.js";

const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const detail = $("detail-dialog"),
  settings = $("settings-dialog");
const confirmation = $("confirm-dialog");
const importDialog = $("import-dialog");
let importPreview = null,
  importBusy = false,
  importDirty = false;
let confirmResolve;
function askConfirmation(message) {
  $("confirm-message").textContent = message;
  confirmation.showModal();
  return new Promise((resolve) => {
    confirmResolve = resolve;
  });
}
function finishConfirmation(answer) {
  confirmation.close();
  const resolve = confirmResolve;
  confirmResolve = null;
  resolve?.(answer);
}
$("confirm-yes").onclick = () => finishConfirmation(true);
$("confirm-no").onclick = () => finishConfirmation(false);
confirmation.addEventListener("cancel", (event) => {
  event.preventDefault();
  finishConfirmation(false);
});
let data = null,
  mode = "browse",
  selected = null,
  pending = 0,
  queue = Promise.resolve();
let loadSequence = 0,
  writeEpoch = 0,
  toastTimer,
  noteDirty = false,
  targetDirty = false,
  settingsDirty = false;
let targetInputVersion = 0,
  settingsInputVersion = 0;
const uid = () =>
  crypto.randomUUID
    ? crypto.randomUUID()
    : Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) =>
        n.toString(16).padStart(2, "0"),
      ).join("");
const itemFor = (key) => data?.items[key] || blankItem();
const dayFor = (key) => data?.days.find((day) => day.date === key);
const splitList = (value) =>
  value
    .split(/[,，、\n]/)
    .map((s) => s.trim())
    .filter(Boolean);

function toast(message) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => ($("toast").hidden = true), 3600);
}
function showError(message) {
  $("error-message").textContent = message;
  $("error-banner").hidden = false;
  document.querySelectorAll("[data-inline-error]").forEach((box) => {
    box.querySelector("span").textContent = message;
    box.hidden = false;
  });
}
function clearError() {
  $("error-banner").hidden = true;
  document
    .querySelectorAll("[data-inline-error]")
    .forEach((box) => (box.hidden = true));
}
function savedStatus() {
  $("save-status").textContent = pending
    ? `正在保存 ${pending} 项…`
    : "✓ 已保存";
}
function dirtyStatus() {
  $("draft-status").textContent =
    noteDirty || targetDirty ? "有未保存的输入" : "修改后请点击对应的保存按钮";
}

async function loadMonth(year, month) {
  const sequence = ++loadSequence;
  await queue;
  try {
    const params = {};
    if (year !== undefined) params.year = year;
    if (month !== undefined) params.month = month;
    const snapshot = await bridge.apiGet("month", params);
    if (sequence !== loadSequence) return;
    if (data && snapshot.revision < data.revision)
      return loadMonth(year, month);
    if (data && snapshot.revision !== data.revision) invalidateImportPreview();
    data = snapshot;
    if (importDialog.open) renderImportTemplate();
    if (
      !["browse", "eraser"].includes(mode) &&
      !data.catalog.pens.some((p) => p.id === mode)
    )
      mode = "browse";
    clearError();
    render();
    savedStatus();
    if (detail.open) {
      renderNotesAndMarks();
      if (!targetDirty) renderTargetForm();
    }
    return true;
  } catch (error) {
    if (sequence === loadSequence)
      showError(error.message || "读取失败，请重试");
    return false;
  }
}

function mutate(payload, { path = "mutate", revision } = {}) {
  const epoch = writeEpoch;
  pending++;
  savedStatus();
  const task = queue.then(async () => {
    if (epoch !== writeEpoch)
      throw new Error("已取消排队操作，请核对日历后重试");
    try {
      const result = await bridge.apiPost(path, {
        ...payload,
        revision: revision ?? data.revision,
      });
      if (result.revision !== data.revision) invalidateImportPreview();
      data.revision = result.revision;
      data.catalog = result.catalog;
      if (result.items) Object.assign(data.items, result.items);
      if (result.target) data.items[result.target] = result.item;
      clearError();
      render();
      if (detail.open && selected) renderNotesAndMarks();
      return result;
    } catch (error) {
      writeEpoch++;
      showError(error.message || "保存失败，输入已保留");
      throw error;
    }
  });
  queue = task
    .catch(() => {})
    .finally(() => {
      pending--;
      savedStatus();
    });
  return task;
}

function setMode(next) {
  mode =
    mode === next && !["browse", "eraser"].includes(next) ? "browse" : next;
  renderTools();
  hideTooltip();
}
function renderTools() {
  $("pens").innerHTML = data.catalog.pens
    .map(
      (p) =>
        `<button class="tool ${mode === p.id ? "active" : ""}" data-pen="${e(p.id)}" aria-pressed="${mode === p.id}" title="${e(p.name)} · ${shapeNames[p.shape]}">${penGlyph(p)}${e(p.name)}</button>`,
    )
    .join("");
  for (const name of ["browse", "eraser"]) {
    $(name).classList.toggle("active", mode === name);
    $(name).setAttribute("aria-pressed", String(mode === name));
  }
  const pen = data.catalog.pens.find((p) => p.id === mode);
  $("mode-hint").textContent = pen
    ? `正在使用「${pen.name}」：点击年、月或日增加一层，最多三层；旁边的数字表示层数，多种标记独立显示。再次点击画笔可取消选择。`
    : mode === "eraser"
      ? "橡皮：点击年、月或日，按最后绘制的顺序擦除一层。"
      : "点击日期添加备注；选择画笔，为年、月、日做标记。";
}
function render() {
  if (!data) return;
  $("book-title").textContent = data.catalog.title;
  document.title = `${data.catalog.title} · 我的日历`;
  $("today-label").textContent = `今天 ${data.today.replaceAll("-", ".")}`;
  renderTools();
  renderCalendar();
}
function renderCalendar() {
  const year = String(data.year),
    month = `${year}-${String(data.month).padStart(2, "0")}`;
  for (const [id, key, label] of [
    ["year-target", year, year],
    ["month-target", month, String(data.month).padStart(2, "0")],
  ]) {
    const marks = itemFor(key).marks;
    const marksLabel = markerLabel(marks, data.catalog.pens);
    $(id).innerHTML =
      `<span class="numeral">${label}</span>${markerBadges(marks, data.catalog.pens)}`;
    $(id).dataset.target = key;
    $(id).setAttribute(
      "aria-label",
      `编辑${key.length === 4 ? "年份" : "月份"} ${key}${marksLabel ? `；标记：${marksLabel}` : ""}`,
    );
  }
  const first = data.days.find((d) => d.in_month);
  $("month-summary").textContent =
    `农历${first.lunar}起 · ${data.catalog.year_boundary === "lichun" ? "立春日" : "春节"}换年 · 点击年月数字也可标记`;
  $("jump-year").value = year;
  $("jump-month").value = data.month;
  $("prev").disabled = data.year === data.bounds.min && data.month === 1;
  $("next").disabled = data.year === data.bounds.max && data.month === 12;
  const labels = ["一", "二", "三", "四", "五", "六", "日"];
  $("weekdays").innerHTML = `<tr>${Array.from({ length: 7 }, (_, i) => {
    const n = (i + data.catalog.week_start) % 7;
    return `<th scope="col" class="${n >= 5 ? "weekend" : ""}">周${labels[n]}</th>`;
  }).join("")}</tr>`;
  $("calendar-caption").textContent = `${data.year}年${data.month}月`;
  $("days").innerHTML = Array.from(
    { length: 6 },
    (_, row) =>
      `<tr>${data.days
        .slice(row * 7, row * 7 + 7)
        .map((d) =>
          dayCell(d, itemFor(d.date), data.catalog, data.today, selected),
        )
        .join("")}</tr>`,
  ).join("");
  const period = [year, month]
    .map((key) => {
      const item = itemFor(key),
        notes = item.notes
          .slice(0, 2)
          .map((n) => noteBar(n, key))
          .join("");
      const states = stateText(item, data.catalog.fields);
      return notes || states.length
        ? `<div class="period-group"><span>${key.length === 4 ? "本年" : "本月"}</span>${notes}${item.notes.length > 2 ? `<button class="quiet small" data-target="${key}">另 ${item.notes.length - 2} 条</button>` : ""}${states.length ? `<button class="quiet small" data-target="${key}" data-tooltip="${e(states.join("\n"))}">${e(states[0])}</button>` : ""}</div>`
        : "";
    })
    .join("");
  $("period-notes").innerHTML = period;
  $("period-notes").hidden = !period;
  $("coverage-warning").hidden = data.holiday_data.supported;
  $("coverage-warning").textContent =
    `${data.year} 年的放假调休安排未收录，不能据此判断法定休息日。可在日期详情中手动补充，或更新节假日数据依赖。`;
}

function handleTarget(event) {
  const element = event.target.closest("[data-target]");
  if (!element || !data) return;
  const key = element.dataset.target;
  if (dayFor(key)?.editable === false) return;
  if (mode === "browse")
    openDetail(key, event.target.closest("[data-note]")?.dataset.note);
  else
    mutate({
      operation: mode === "eraser" ? "erase" : "paint",
      target: key,
      ...(mode === "eraser" ? {} : { pen_id: mode }),
    })
      .then((result) => {
        if (result.message !== "已保存") toast(result.message);
      })
      .catch(() => {});
}
function openDetail(key, noteId) {
  selected = key;
  noteDirty = false;
  targetDirty = false;
  hideTooltip();
  $("detail-title").textContent =
    key.length === 10
      ? key.replaceAll("-", " / ")
      : key.length === 7
        ? key.replace("-", " 年 ") + " 月"
        : key + " 年";
  $("detail-kind").textContent =
    key.length === 10 ? "这一天" : key.length === 7 ? "这个月" : "这一年";
  $("note-count").previousElementSibling.textContent =
    key.length === 10 ? "当天备注" : key.length === 7 ? "本月备注" : "本年备注";
  const day = dayFor(key);
  $("detail-subtitle").textContent = day
    ? `${day.weekday_label} · 农历${day.lunar}`
    : "标记、备注与状态仅属于所选时段";
  renderNotesAndMarks();
  renderTargetForm();
  resetNote();
  if (noteId) editNote(noteId);
  renderCalendar();
  if (!detail.open) detail.showModal();
}
function renderFacts() {
  const day = dayFor(selected);
  if (!day) {
    $("date-facts").innerHTML = "";
    return;
  }
  const item = itemFor(selected),
    holiday = effectiveHoliday(day, item);
  $("date-facts").innerHTML = `<div class="pillar-grid">${[
    ["year", "年干支"],
    ["month", "月干支"],
    ["day", "日干支"],
  ]
    .map(([key, label]) => {
      const p = day.ganzhi[key];
      return `<div class="pillar-box"><small>${label}</small><strong>${e(p.text)}</strong><span>${e(p.gan)}·${p.gan_element} / ${e(p.zhi)}·${p.zhi_element}</span></div>`;
    })
    .join(
      "",
    )}</div><p class="facts-note">节日：${e([...day.festivals, ...item.festivals].join("、") || "无")}${day.solar_term ? ` · ${e(day.solar_term)}` : ""}\n安排：${e(statusNames[holiday.status])}${holiday.name ? ` · ${e(holiday.name)}` : ""}（${holiday.source === "manual" ? "手动设置" : holiday.known ? "日期库" : "数据待更新"}）\n干支年按${data.catalog.year_boundary === "lichun" ? "立春日" : "春节"}换年；五行为天干与地支本气。</p>`;
}
function renderNotesAndMarks() {
  const item = itemFor(selected);
  renderFacts();
  $("detail-marks").innerHTML = item.marks.length
    ? item.marks
        .map((mark) => {
          const p = data.catalog.pens.find((p) => p.id === mark.pen_id);
          return `<span class="mark-chip">${penGlyph(p)}${e(p.name)} ×${mark.layers}<button data-remove-mark="${e(p.id)}" aria-label="擦除一层${e(p.name)}">擦一层</button></span>`;
        })
        .join("")
    : '<p class="empty-hint">还没有标记，可关闭详情后选择画笔添加。</p>';
  $("note-count").textContent = `${item.notes.length} 条`;
  $("note-list").innerHTML = item.notes.length
    ? item.notes
        .map(
          (note) =>
            `<article class="note-card" style="--note-color:${e(note.color)}"><p>${e(note.text)}</p><div class="note-actions"><button data-edit-note="${e(note.id)}">编辑</button><button data-delete-note="${e(note.id)}">删除</button></div></article>`,
        )
        .join("")
    : '<p class="empty-hint">第一条记录，从这里开始。</p>';
}
function resetNote() {
  $("note-form").reset();
  $("note-id").value = "";
  $("note-color").value = data.catalog.note_color;
  $("note-form-label").textContent = "写一条新备注";
  $("cancel-note").hidden = true;
  noteDirty = false;
  dirtyStatus();
}
async function editNote(id) {
  const note = itemFor(selected).notes.find((n) => n.id === id);
  if (!note) return;
  if (noteDirty && !(await askConfirmation("放弃当前未保存的备注输入？")))
    return;
  $("note-id").value = id;
  $("note-text").value = note.text;
  $("note-color").value = note.color;
  $("note-form-label").textContent = "编辑这条备注";
  $("cancel-note").hidden = false;
  noteDirty = false;
  dirtyStatus();
  $("note-text").focus();
}
function renderTargetForm() {
  const item = itemFor(selected);
  $("custom-values").innerHTML = data.catalog.fields.length
    ? data.catalog.fields
        .map((f) => {
          const value = item.values[f.id];
          let control;
          if (f.kind === "text")
            control = `<input data-field="${e(f.id)}" value="${e(value ?? "")}" maxlength="200" placeholder="未填写">`;
          else {
            const options =
              f.kind === "boolean"
                ? [
                    ["true", "是"],
                    ["false", "否"],
                  ]
                : f.options.map((x) => [x, x]);
            control = `<select data-field="${e(f.id)}"><option value="">未设置</option>${options.map(([v, label]) => `<option value="${e(v)}" ${String(value) === v ? "selected" : ""}>${e(label)}</option>`).join("")}</select>`;
          }
          return `<label>${e(f.name)}${control}</label>`;
        })
        .join("")
    : '<p class="empty-hint">还没有扩展字段。可添加心情、财务状态、是否完成等条件。</p>';
  $("day-overrides").hidden = selected.length !== 10;
  $("custom-festivals").value = item.festivals.join("、");
  $("holiday-status").value = item.holiday?.status || "auto";
  $("holiday-name").value = item.holiday?.name || "";
  targetDirty = false;
  dirtyStatus();
}
async function closeDetail() {
  if (pending) {
    toast("正在保存，请稍候");
    return;
  }
  if (
    (noteDirty || targetDirty) &&
    !(await askConfirmation("有未保存的输入，放弃这些输入并关闭？"))
  )
    return;
  detail.close();
  selected = null;
  noteDirty = false;
  targetDirty = false;
  renderCalendar();
}

async function selectTab(tab, force = false) {
  if (
    !force &&
    settingsDirty &&
    !(await askConfirmation("放弃当前未保存的设置输入？"))
  )
    return;
  settingsDirty = false;
  for (const name of ["pens", "fields", "calendar"])
    $("tab-" + name).hidden = name !== tab;
  document
    .querySelectorAll("[data-tab]")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  renderManagers();
  if (tab === "pens") resetPen();
  if (tab === "fields") resetField();
  if (tab === "calendar") {
    $("settings-name").value = data.catalog.title;
    $("week-start").value = data.catalog.week_start;
    $("year-boundary").value = data.catalog.year_boundary;
    $("default-note-color").value = data.catalog.note_color;
    $("data-note").innerHTML =
      `放假调休：中国大陆 · chinesecalendar ${e(data.holiday_data.version)}<br>当前已收录 ${data.holiday_data.first_year}–${data.holiday_data.last_year} 年。未收录年份显示待确认，周末不会自动推断为法定放假。<br>农历、节气及干支：lunar-python。<br>默认红圈“得财”、黑圈“破财”为自行记录的含义。<br><a href="https://github.com/liriansu-opus/chinese-calendar" target="_blank" rel="noopener noreferrer">节假日数据来源</a>`;
  }
}
function openSettings(tab) {
  if (!data) return;
  hideTooltip();
  selectTab(tab, true);
  if (!settings.open) settings.showModal();
}
async function closeSettings() {
  if (pending) {
    toast("正在保存，请稍候");
    return;
  }
  if (settingsDirty && !(await askConfirmation("放弃未保存的设置输入？")))
    return;
  settings.close();
  settingsDirty = false;
  if (detail.open) {
    renderNotesAndMarks();
    if (!targetDirty) renderTargetForm();
  }
}
function renderManagers() {
  $("pen-list").innerHTML = data.catalog.pens
    .map(
      (p) =>
        `<div class="manager-row">${penGlyph(p)}<span class="row-name">${e(p.name)} · ${shapeNames[p.shape]}</span><button data-edit-pen="${e(p.id)}">编辑</button><button data-delete-pen="${e(p.id)}">删除</button></div>`,
    )
    .join("");
  $("field-list").innerHTML = data.catalog.fields.length
    ? data.catalog.fields
        .map(
          (f) =>
            `<div class="manager-row"><span class="row-name">${e(f.name)} · ${{ text: "文本", boolean: "是 / 否", select: "下拉状态" }[f.kind]}</span><button data-edit-field="${e(f.id)}">编辑</button><button data-delete-field="${e(f.id)}">删除</button></div>`,
        )
        .join("")
    : '<p class="empty-hint">让日历记录更多：创建第一个扩展字段。</p>';
}
function resetPen() {
  $("pen-form").reset();
  $("pen-id").value = "";
  settingsDirty = false;
}
function resetField() {
  $("field-form").reset();
  $("field-id").value = "";
  $("field-options-label").hidden = false;
  settingsDirty = false;
}

$("jump-month").innerHTML = Array.from(
  { length: 12 },
  (_, i) => `<option value="${i + 1}">${i + 1}月</option>`,
).join("");
$("pens").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-pen]");
  if (b) setMode(b.dataset.pen);
});
$("browse").onclick = () => data && setMode("browse");
$("eraser").onclick = () => data && setMode("eraser");
for (const id of ["days", "year-target", "month-target", "period-notes"])
  $(id).addEventListener("click", handleTarget);
$("prev").onclick = () => navigate(-1);
$("next").onclick = () => navigate(1);
function navigate(delta) {
  if (!data) return;
  const value = data.year * 12 + data.month - 1 + delta;
  loadMonth(Math.floor(value / 12), (value % 12) + 1);
}
$("today").onclick = () => {
  if (data) {
    const [y, m] = data.today.split("-");
    loadMonth(y, m);
  }
};
$("jump-form").onsubmit = (ev) => {
  ev.preventDefault();
  if (data) loadMonth($("jump-year").value, $("jump-month").value);
};
$("retry").onclick = () => {
  if (!bridge) {
    location.reload();
    return;
  }
  if (data) loadMonth(data.year, data.month);
  else loadMonth();
};
document.querySelectorAll(".refresh-data").forEach(
  (button) =>
    (button.onclick = () => {
      if (data) loadMonth(data.year, data.month);
    }),
);
$("add-pen").onclick = () => openSettings("pens");
$("manage-pens").onclick = () => openSettings("pens");
$("settings").onclick = () => openSettings("calendar");
$("add-field-from-detail").onclick = () => openSettings("fields");
document
  .querySelectorAll(".close-detail")
  .forEach((button) => (button.onclick = closeDetail));
detail.addEventListener("cancel", (ev) => {
  ev.preventDefault();
  closeDetail();
});
settings.addEventListener("cancel", (ev) => {
  ev.preventDefault();
  closeSettings();
});
$("close-settings").onclick = closeSettings;
document
  .querySelectorAll("[data-tab]")
  .forEach((button) => (button.onclick = () => selectTab(button.dataset.tab)));

$("detail-marks").onclick = (ev) => {
  const b = ev.target.closest("[data-remove-mark]");
  if (b)
    mutate({
      operation: "erase",
      target: selected,
      pen_id: b.dataset.removeMark,
    }).catch(() => {});
};
$("note-list").onclick = async (ev) => {
  const edit = ev.target.closest("[data-edit-note]"),
    del = ev.target.closest("[data-delete-note]");
  if (edit) editNote(edit.dataset.editNote);
  if (del && (await askConfirmation("删除这条备注？")))
    mutate({
      operation: "note.delete",
      target: selected,
      id: del.dataset.deleteNote,
    })
      .then(() => {
        if ($("note-id").value === del.dataset.deleteNote) resetNote();
      })
      .catch(() => {});
};
$("note-form").addEventListener("input", () => {
  noteDirty = true;
  dirtyStatus();
});
$("target-form").addEventListener("input", () => {
  targetDirty = true;
  targetInputVersion++;
  dirtyStatus();
});
$("cancel-note").onclick = async () => {
  if (!noteDirty || (await askConfirmation("放弃当前备注输入？"))) resetNote();
};
$("note-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const id = $("note-id").value || uid();
  $("note-id").value = id;
  const payload = {
    operation: "note.save",
    target: selected,
    id,
    text: $("note-text").value,
    color: $("note-color").value,
  };
  try {
    await mutate(payload);
    if (
      $("note-text").value === payload.text &&
      $("note-color").value === payload.color
    )
      resetNote();
    toast("备注已保存");
  } catch {}
};
$("target-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const values = {},
    inputVersion = targetInputVersion;
  $("custom-values")
    .querySelectorAll("[data-field]")
    .forEach((input) => {
      const field = data.catalog.fields.find(
        (f) => f.id === input.dataset.field,
      );
      if (!input.value) return;
      values[field.id] =
        field.kind === "boolean" ? input.value === "true" : input.value;
    });
  const status = $("holiday-status").value;
  try {
    await mutate({
      operation: "target.save",
      target: selected,
      values,
      festivals: splitList($("custom-festivals").value),
      holiday:
        status === "auto" ? null : { status, name: $("holiday-name").value },
    });
    if (inputVersion === targetInputVersion) targetDirty = false;
    dirtyStatus();
    toast("条件与状态已保存");
  } catch {}
};

settings.addEventListener("input", () => {
  settingsDirty = true;
  settingsInputVersion++;
});
$("new-pen").onclick = async () => {
  if (!settingsDirty || (await askConfirmation("放弃画笔输入？"))) resetPen();
};
$("new-field").onclick = async () => {
  if (!settingsDirty || (await askConfirmation("放弃字段输入？"))) resetField();
};
$("field-kind").onchange = () =>
  ($("field-options-label").hidden = $("field-kind").value !== "select");
$("pen-list").onclick = async (ev) => {
  const edit = ev.target.closest("[data-edit-pen]"),
    del = ev.target.closest("[data-delete-pen]");
  if (edit) {
    if (settingsDirty && !(await askConfirmation("放弃当前画笔输入？"))) return;
    const p = data.catalog.pens.find((p) => p.id === edit.dataset.editPen);
    $("pen-id").value = p.id;
    $("pen-name").value = p.name;
    $("pen-color").value = p.color;
    $("pen-shape").value = p.shape;
    settingsDirty = false;
  }
  if (del && (await askConfirmation("删除这支画笔？仍有标记的画笔会保留。")))
    mutate({ operation: "pen.delete", id: del.dataset.deletePen })
      .then(() => {
        renderManagers();
        resetPen();
        if (mode === del.dataset.deletePen) setMode("browse");
      })
      .catch(() => {});
};
$("field-list").onclick = async (ev) => {
  const edit = ev.target.closest("[data-edit-field]"),
    del = ev.target.closest("[data-delete-field]");
  if (edit) {
    if (settingsDirty && !(await askConfirmation("放弃当前字段输入？"))) return;
    const f = data.catalog.fields.find((f) => f.id === edit.dataset.editField);
    $("field-id").value = f.id;
    $("field-name").value = f.name;
    $("field-kind").value = f.kind;
    $("field-options").value = f.options.join("、");
    $("field-options-label").hidden = f.kind !== "select";
    settingsDirty = false;
  }
  if (
    del &&
    (await askConfirmation("删除这个字段？仍有状态记录的字段会保留。"))
  )
    mutate({ operation: "field.delete", id: del.dataset.deleteField })
      .then(() => {
        renderManagers();
        resetField();
      })
      .catch(() => {});
};
$("pen-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const id = $("pen-id").value || uid(),
    inputVersion = settingsInputVersion;
  $("pen-id").value = id;
  try {
    await mutate({
      operation: "pen.save",
      id,
      name: $("pen-name").value,
      color: $("pen-color").value,
      shape: $("pen-shape").value,
    });
    if (inputVersion === settingsInputVersion) settingsDirty = false;
    renderManagers();
    mode = id;
    renderTools();
    toast("画笔已保存，关闭窗口后即可使用");
  } catch {}
};
$("field-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const id = $("field-id").value || uid(),
    inputVersion = settingsInputVersion;
  $("field-id").value = id;
  try {
    await mutate({
      operation: "field.save",
      id,
      name: $("field-name").value,
      kind: $("field-kind").value,
      options:
        $("field-kind").value === "select"
          ? splitList($("field-options").value)
          : [],
    });
    if (inputVersion === settingsInputVersion) settingsDirty = false;
    renderManagers();
    toast("扩展字段已保存");
  } catch {}
};
$("settings-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const inputVersion = settingsInputVersion;
  try {
    await mutate({
      operation: "settings.save",
      title: $("settings-name").value,
      week_start: Number($("week-start").value),
      year_boundary: $("year-boundary").value,
      note_color: $("default-note-color").value,
    });
    if (inputVersion === settingsInputVersion) settingsDirty = false;
    await loadMonth(data.year, data.month);
    toast("日历设置已保存");
  } catch {}
};
$("export").onclick = async () => {
  if (!data) return;
  try {
    await queue;
    const record = await bridge.apiGet("export");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(record, null, 2)], {
        type: "application/json;charset=utf-8",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `历记-${data.today}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    toast("日历记录已导出");
  } catch (err) {
    showError(err.message);
  }
};

function importTemplate() {
  const month = `${data.year}-${String(data.month).padStart(2, "0")}`;
  return {
    format: "liji-import-v1",
    entries: [1, 2].map((day, index) => ({
      date: `${month}-${String(day).padStart(2, "0")}`,
      marks: data.catalog.pens.length
        ? [
            {
              pen: data.catalog.pens[index % data.catalog.pens.length].name,
              layers: 1,
            },
          ]
        : [],
      events: [
        {
          text: "示例事件：请替换为该日期的实际内容",
          color: data.catalog.note_color,
        },
      ],
    })),
  };
}
function renderImportTemplate() {
  const available = data.catalog.pens
    .map(
      (pen) =>
        `${JSON.stringify(pen.name)}（${shapeNames[pen.shape]}，${pen.color}）`,
    )
    .join("；");
  $("import-prompt").value = `请按以下要求生成“历记”日历批量导入 JSON。
日期范围：【填写开始和结束日期，或指定日期列表】
生成要求：【填写每个日期需要的标记、层数与事件内容】

仅输出一个 JSON 对象，不加解释，不添加模板之外的字段。
format 固定为 "liji-import-v1"，entries 是 1–500 条记录的数组。
date 使用有效公历 YYYY-MM-DD，支持 1901–2099 年；年或月记录也可用 YYYY、YYYY-MM，各层级独立保存。
marks 是标记数组，pen 必须与现有画笔名称完全一致，layers 为 1–3 的整数（省略为 1）。每条记录最多四种不同画笔。
当前可用画笔：${available || "暂无；marks 请填 []。如需标记，请先在日历新建画笔后重新复制提示词。"}
events 是事件数组，text 为 1–4000 字正文，color 可省略，省略时使用当前默认色 ${data.catalog.note_color}；指定时使用 #RRGGBB。每个日期合计最多 50 个事件。
marks、events 可以省略其一，但每条记录至少包含一个标记或事件。同一日期可以有多个事件。
保留已有内容：标记只补足到 layers，不减少或重复叠加；同一日期、正文和颜色相同的事件跳过。需要区分的同名事件请在正文中写明时间或编号。
只生成指定范围内的日期；不要编造已发生事件，计划内容请写明“计划”。不确定的信息不要当作事实填写。
总文本不超过 512 KiB，超出 500 条请分批。以下仅为格式示例，请按上述日期和要求替换：
${JSON.stringify(importTemplate(), null, 2)}`;
}
function importSummary(summary) {
  return `${summary.entries} 条记录 · ${summary.targets} 个年月日 · 补充 ${summary.mark_layers_added} 层标记 · 新增 ${summary.events_added} 个事件 · 跳过 ${summary.skipped} 个已满足标记或重复事件`;
}
function updateImportControls() {
  $("import-text").disabled = importBusy;
  $("preview-import").disabled = importBusy;
  $("refresh-import").disabled = importBusy;
  $("commit-import").disabled = importBusy || !importPreview;
}
function invalidateImportPreview() {
  importPreview = null;
  $("import-preview").hidden = true;
  $("import-status").textContent = "文本或数据变化后，请重新校验预览";
  updateImportControls();
}
function importError(message) {
  $("import-error").textContent = message;
  $("import-error").hidden = false;
  $("import-status").textContent = "未完成导入，文本已保留";
}
function renderImportPreview(result) {
  $("import-summary").textContent = importSummary(result.summary);
  $("import-rows").innerHTML = result.rows
    .map(
      (row, index) =>
        `<article class="import-row"><h4>${index + 1}. ${e(row.date)}</h4>${row.marks
          .map(
            (mark) =>
              `<p>标记「${e(mark.pen)}」 ${mark.layers} 层 · ${mark.added ? `补充 ${mark.added} 层` : "已满足，跳过"}</p>`,
          )
          .join("")}${row.events
          .map(
            (event) =>
              `<div class="import-event" style="--note-color:${e(event.color)}"><span>${event.added ? "新增事件" : "重复事件，跳过"}</span><p>${e(event.text)}</p></div>`,
          )
          .join("")}</article>`,
    )
    .join("");
  $("import-preview").hidden = false;
  $("import-status").textContent = result.summary.changed_targets
    ? "请核对日期和内容，确认后一次性保存"
    : "全部内容已存在，本次不会新增记录";
}
$("open-import").onclick = () => {
  if (!data) return;
  if (pending) return toast("正在保存，请稍候");
  hideTooltip();
  renderImportTemplate();
  importDialog.showModal();
};
function closeImport() {
  if (importBusy) return;
  // Keep the pasted draft and preview when returning to the calendar.
  importDialog.close();
}
$("close-import").onclick = closeImport;
importDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeImport();
});
$("import-text").addEventListener("input", () => {
  importDirty = Boolean($("import-text").value.trim());
  invalidateImportPreview();
  $("import-error").hidden = true;
  $("import-result").hidden = true;
});
$("refresh-import").onclick = async () => {
  if (importBusy) return;
  importBusy = true;
  invalidateImportPreview();
  $("import-error").hidden = true;
  $("import-result").hidden = true;
  try {
    if (await loadMonth(data.year, data.month)) {
      $("import-status").textContent = "请重新校验预览后再导入";
    } else {
      importError($("error-message").textContent || "刷新失败，请重试");
    }
  } finally {
    importBusy = false;
    updateImportControls();
  }
};
$("import-form").onsubmit = async (event) => {
  event.preventDefault();
  if (importBusy) return;
  importBusy = true;
  invalidateImportPreview();
  $("import-error").hidden = true;
  $("import-result").hidden = true;
  $("import-status").textContent = "正在校验…";
  try {
    await queue;
    const source = $("import-text").value;
    const result = await bridge.apiPost("import/preview", {
      text: source,
      revision: data.revision,
    });
    if (result.revision !== data.revision)
      throw new Error("日历数据已变化，请重新预览");
    importPreview = { ...result, text: source };
    renderImportPreview(result);
  } catch (error) {
    importError(error.message || "校验失败，请重试");
  } finally {
    importBusy = false;
    updateImportControls();
  }
};
$("commit-import").onclick = async () => {
  if (importBusy || !importPreview) return;
  const preview = importPreview;
  importBusy = true;
  updateImportControls();
  $("import-error").hidden = true;
  $("import-status").textContent = "正在导入…";
  try {
    const result = await mutate(
      { text: preview.text },
      { path: "import", revision: preview.revision },
    );
    invalidateImportPreview();
    importDirty = false;
    $("import-result").textContent =
      `导入完成。${importSummary(result.summary)}。可关闭窗口查看对应年月日。`;
    $("import-result").hidden = false;
    $("import-status").textContent = "已保存，可继续粘贴下一批文本";
  } catch (error) {
    invalidateImportPreview();
    importError(
      error.message || "未收到保存结果，请刷新后重新预览；重复内容会自动跳过",
    );
  } finally {
    importBusy = false;
    updateImportControls();
  }
};
$("copy-import-prompt").onclick = async () => {
  const prompt = $("import-prompt");
  try {
    await navigator.clipboard.writeText(prompt.value);
    toast("已复制，补充日期范围和要求后交给 AI");
  } catch {
    $("import-template-details").open = true;
    prompt.focus();
    prompt.select();
    toast("提示词已选中，请手动复制（Ctrl+C 或长按复制）");
  }
};
$("download-import-template").onclick = () => {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(importTemplate(), null, 2)], {
      type: "application/json;charset=utf-8",
    }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "历记-批量导入模板.json";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
};

function hideTooltip() {
  $("tooltip").hidden = true;
}
function showTooltip(target, x, y) {
  const tip = $("tooltip");
  tip.textContent = target.dataset.tooltip;
  tip.hidden = false;
  tip.style.left = `${Math.max(8, Math.min(x + 12, innerWidth - tip.offsetWidth - 12))}px`;
  tip.style.top = `${Math.max(8, Math.min(y + 14, innerHeight - tip.offsetHeight - 12))}px`;
}
document.addEventListener("pointerover", (ev) => {
  const target = ev.target.closest("[data-tooltip]");
  if (target && ev.pointerType !== "touch")
    showTooltip(target, ev.clientX, ev.clientY);
});
document.addEventListener("pointerout", (ev) => {
  if (ev.target.closest("[data-tooltip]")) hideTooltip();
});
document.addEventListener("focusin", (ev) => {
  const target = ev.target.closest("[data-tooltip]");
  if (target) {
    const box = target.getBoundingClientRect();
    showTooltip(target, box.left, box.bottom);
  }
});
document.addEventListener("focusout", hideTooltip);
document.addEventListener("scroll", hideTooltip, true);
window.addEventListener("beforeunload", (ev) => {
  if (
    pending ||
    noteDirty ||
    targetDirty ||
    settingsDirty ||
    importDirty ||
    importBusy
  ) {
    ev.preventDefault();
    ev.returnValue = "";
  }
});

try {
  if (!bridge)
    throw new Error(
      "请从 AstrBot WebUI → 插件 → 历记 → 可视化页面打开此日历。",
    );
  const context = await bridge.ready();
  const theme = (ctx) =>
    (document.documentElement.dataset.theme = ctx?.isDark ? "dark" : "light");
  theme(context);
  if (bridge.onContext) bridge.onContext(theme);
  await loadMonth();
} catch (error) {
  showError(error.message);
  $("save-status").textContent = "未连接";
}
