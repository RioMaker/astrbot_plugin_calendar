export const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        ch
      ],
  );
export const blankItem = () => ({
  marks: [],
  notes: [],
  values: {},
  festivals: [],
  holiday: null,
});
export const statusNames = {
  holiday: "放假",
  workday: "调休上班",
  weekend: "周末休息",
  normal: "普通工作日",
  unknown: "安排待确认",
};
export const shapeNames = {
  circle: "圆圈",
  square: "方框",
  diamond: "菱形",
  triangle: "三角形",
};

function penSVG(pen) {
  const attrs = `fill="none" stroke="${escapeHTML(pen.color)}" stroke-width="2" stroke-linejoin="round" class="mark-shape" data-pen="${escapeHTML(pen.id)}"`;
  const shapes = {
    circle: `<circle cx="12" cy="12" r="8" ${attrs}/>`,
    square: `<rect x="4" y="4" width="16" height="16" rx="2" ${attrs}/>`,
    diamond: `<polygon points="12,3 21,12 12,21 3,12" ${attrs}/>`,
    triangle: `<polygon points="12,3 22,21 2,21" ${attrs}/>`,
  };
  return `<svg class="marker-symbol" aria-hidden="true" viewBox="0 0 24 24">${shapes[pen.shape] || shapes.circle}</svg>`;
}

function visibleMarks(marks, pens) {
  return marks
    .map((mark) => ({ mark, pen: pens.find((p) => p.id === mark.pen_id) }))
    .filter(({ pen }) => pen);
}

export function markerLabel(marks, pens) {
  return visibleMarks(marks, pens)
    .map(({ mark, pen }) => `${pen.name} ${mark.layers}层`)
    .join("；");
}

export function markerBadges(marks, pens) {
  const entries = visibleMarks(marks, pens);
  if (!entries.length) return "";
  return `<span class="marker-list" aria-hidden="true">${entries
    .map(({ mark, pen }) => {
      const label = `${pen.name} · ${mark.layers}层`;
      return `<span class="marker-badge" data-pen="${escapeHTML(pen.id)}" data-layers="${mark.layers}" data-tooltip="${escapeHTML(label)}">${penSVG(pen)}<span class="marker-count">${mark.layers}</span></span>`;
    })
    .join("")}</span>`;
}

export function penGlyph(pen) {
  return `<span class="pen-glyph">${penSVG(pen)}</span>`;
}

export function noteBar(note, target) {
  return `<button class="note-bar" style="--note-color:${escapeHTML(note.color)}" data-note="${escapeHTML(note.id)}" data-target="${escapeHTML(target)}" data-tooltip="${escapeHTML(note.text)}" aria-label="备注：${escapeHTML(note.text.slice(0, 80))}">${escapeHTML(note.text.replace(/\s+/g, " "))}</button>`;
}

export function effectiveHoliday(day, item) {
  return item.holiday
    ? { ...item.holiday, source: "manual", known: true }
    : day.holiday;
}

export function stateText(item, fields) {
  return fields
    .filter((field) => Object.hasOwn(item.values, field.id))
    .map((field) => {
      const value = item.values[field.id];
      return `${field.name}：${typeof value === "boolean" ? (value ? "是" : "否") : value}`;
    });
}

export function dayCell(day, item, catalog, today, selected) {
  const e = escapeHTML,
    key = day.date;
  const holiday = effectiveHoliday(day, item);
  const festivals = [...day.festivals, ...item.festivals];
  const caption = festivals.join(" · ") || day.solar_term || day.lunar_short;
  const cls = `${day.in_month ? "" : "outside"} ${key === today ? "is-today" : ""} ${key === selected ? "selected" : ""}`;
  const badge = ["holiday", "workday"].includes(holiday.status)
    ? `<span class="holiday-badge ${holiday.status === "workday" ? "workday" : ""} ${holiday.source === "manual" ? "manual" : ""}" title="${e(holiday.name)} ${e(statusNames[holiday.status])}${holiday.source === "manual" ? "（手动）" : ""}">${holiday.status === "holiday" ? "休" : "班"}</span>`
    : "";
  const states = stateText(item, catalog.fields);
  const marksLabel = markerLabel(item.marks, catalog.pens);
  return `<td class="${cls}" data-target="${key}" data-editable="${day.editable}"><div class="day-top"><button class="number-target day-number" data-target="${key}" aria-label="${key} ${e(day.weekday_label)}${marksLabel ? `；标记：${e(marksLabel)}` : ""}" ${day.editable ? "" : "disabled"}><span class="date-label"><span class="numeral">${day.day}</span>${key === today ? '<span class="today-tick">今</span>' : ""}</span>${markerBadges(item.marks, catalog.pens)}</button></div><div class="day-caption"><div class="lunar-label ${festivals.length ? "festival-label" : day.solar_term ? "term-label" : ""}" title="${e([day.lunar, ...festivals, day.solar_term].filter(Boolean).join(" · "))}">${e(caption)}</div>${badge}</div><div class="day-ganzhi"><span>${e(day.ganzhi.day.text)}</span><span class="element-pair">${e(day.ganzhi.day.gan_element + day.ganzhi.day.zhi_element)}</span></div>${states.length ? `<div class="state-chip" data-tooltip="${e(states.join("\n"))}">${e(states.join(" · "))}</div>` : ""}<div class="cell-notes">${item.notes
    .slice(0, 2)
    .map((n) => noteBar(n, key))
    .join(
      "",
    )}${item.notes.length > 2 ? `<button class="more-notes" data-target="${key}">还有 ${item.notes.length - 2} 条备注</button>` : ""}</div></td>`;
}
