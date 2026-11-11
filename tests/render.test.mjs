import test from "node:test";
import assert from "node:assert/strict";
import { markerSVG, noteBar, stateText } from "../pages/calendar/render.js";

test("three concentric circles have visible gaps rather than forming one thick stroke", () => {
  const svg=markerSVG([{pen_id:"red",layers:3}],[{id:"red",color:"#ce4545",shape:"circle"}]);
  const radii=[...svg.matchAll(/ry="([\d.]+)"/g)].map(m=>Number(m[1]));
  const stroke=Number(svg.match(/stroke-width="([\d.]+)"/)[1]);
  assert.equal(radii.length,3);
  assert.ok(radii[1]-radii[0]>stroke*2);
  assert.ok(radii[2]-radii[1]>stroke*2);
  assert.ok(radii[2]+stroke/2<45);
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
