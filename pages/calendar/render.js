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

function shape(type, cx, rx, ry, attrs) {
  if (type === "square")
    return `<rect x="${cx - rx}" y="${45 - ry}" width="${rx * 2}" height="${ry * 2}" rx="4" ${attrs}/>`;
  if (type === "diamond")
    return `<polygon points="${cx},${45 - ry} ${cx + rx},45 ${cx},${45 + ry} ${cx - rx},45" ${attrs}/>`;
  if (type === "triangle")
    return `<polygon points="${cx},${45 - ry} ${cx + rx},${45 + ry} ${cx - rx},${45 + ry}" ${attrs}/>`;
  return `<ellipse cx="${cx}" cy="45" rx="${rx}" ry="${ry}" ${attrs}/>`;
}

export function markerSVG(marks, pens, wide = false) {
  let ring = 0,
    paths = "";
  const layers = marks.reduce((sum, mark) => sum + mark.layers, 0);
  const spacing = Math.min(5, 12 / Math.max(1, layers - 1));
  const stroke = Math.min(1.8, spacing * 0.6);
  for (const mark of marks) {
    const pen = pens.find((p) => p.id === mark.pen_id);
    if (!pen) continue;
    for (let layer = 0; layer < mark.layers; layer++, ring++) {
      const angular = ["diamond", "triangle"].includes(pen.shape);
      const attrs = `fill="none" stroke="${escapeHTML(pen.color)}" stroke-width="${stroke}" class="mark-shape" data-pen="${escapeHTML(pen.id)}"`;
      paths += shape(
        pen.shape,
        wide ? 90 : 50,
        (wide ? (angular ? 78 : 65) : angular ? 38 : 26) + ring * spacing,
        (angular ? 30 : 24) + ring * spacing,
        attrs,
      );
    }
  }
  return `<svg aria-hidden="true" viewBox="0 0 ${wide ? 180 : 100} 90">${paths}</svg>`;
}

export function penGlyph(pen) {
  return `<span class="pen-glyph">${markerSVG([{ pen_id: pen.id, layers: 1 }], [pen])}</span>`;
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
  return `<td class="${cls}" data-target="${key}" data-editable="${day.editable}"><div class="day-top"><button class="number-target day-number" data-target="${key}" aria-label="${key} ${e(day.weekday_label)}" ${day.editable ? "" : "disabled"}><span class="numeral">${day.day}</span>${markerSVG(item.marks, catalog.pens)}${key === today ? '<span class="today-tick">今</span>' : ""}</button>${badge}</div><div class="lunar-label ${festivals.length ? "festival-label" : day.solar_term ? "term-label" : ""}" title="${e([day.lunar, ...festivals, day.solar_term].filter(Boolean).join(" · "))}">${e(caption)}</div><div class="day-ganzhi"><span>${e(day.ganzhi.day.text)}</span><span class="element-pair">${e(day.ganzhi.day.gan_element + day.ganzhi.day.zhi_element)}</span></div>${states.length ? `<div class="state-chip" data-tooltip="${e(states.join("\n"))}">${e(states.join(" · "))}</div>` : ""}<div class="cell-notes">${item.notes
    .slice(0, 2)
    .map((n) => noteBar(n, key))
    .join(
      "",
    )}${item.notes.length > 2 ? `<button class="more-notes" data-target="${key}">还有 ${item.notes.length - 2} 条备注</button>` : ""}</div></td>`;
}
