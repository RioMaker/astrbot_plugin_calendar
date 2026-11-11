import test from "node:test";
import assert from "node:assert/strict";
import { markerBadges, markerLabel, noteBar, stateText } from "../pages/calendar/render.js";

test("coexisting pens keep separate symbols and independent layer counts", () => {
  const pens = [
    { id: "red", name: "得财", color: "#ce4545", shape: "circle" },
    { id: "black", name: "破财", color: "#252a34", shape: "triangle" },
  ];
  const marks = [{ pen_id: "red", layers: 3 }, { pen_id: "black", layers: 2 }];
  const rendered = markerBadges(marks, pens);
  assert.equal((rendered.match(/class="marker-badge"/g) || []).length, 2);
  assert.equal((rendered.match(/class="mark-shape"/g) || []).length, 2);
  assert.ok(rendered.includes('data-pen="red" data-layers="3"'));
  assert.ok(rendered.includes('data-pen="black" data-layers="2"'));
  assert.equal(markerLabel(marks, pens), "得财 3层；破财 2层");
  assert.equal(markerBadges([], pens), "");
});

test("pen labels in marker tooltips remain inert text", () => {
  const rendered = markerBadges([{ pen_id: "x", layers: 1 }], [
    { id: "x", name: '<img src=x onerror="attack()">', color: "#123456", shape: "square" },
  ]);
  assert.ok(!rendered.includes("<img"));
  assert.ok(rendered.includes("&lt;img"));
  assert.ok(rendered.includes("&quot;attack()&quot;"));
});

test("note content stays text in label, tooltip and markup", () => {
  const rendered=noteBar({id:"n",color:"#123456",text:'<img src=x onerror="attack()"> & more'},"2026-11-11");
  assert.ok(!rendered.includes("<img"));
  assert.ok(rendered.includes("&lt;img"));
  assert.ok(rendered.includes("&quot;attack()&quot;"));
});

test("a false condition is shown while an unset condition is omitted", () => {
  assert.deepEqual(stateText({values:{done:false}},[{id:"done",name:"已复盘"},{id:"unset",name:"未设置"}]),["已复盘：否"]);
});
