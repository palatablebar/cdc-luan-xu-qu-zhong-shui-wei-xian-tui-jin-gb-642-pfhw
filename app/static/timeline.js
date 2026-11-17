/* 时间轴：把后端给的事件刻度与水位线画到一条横向轨道上（只做坐标换算）。 */
(function () {
  "use strict";

  function parseTime(text) {
    if (!text) return null;
    var value = new Date(text).getTime();
    return Number.isNaN(value) ? null : value;
  }

  function pad(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function label(millis) {
    var date = new Date(millis);
    return pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  function render(root, data) {
    root.innerHTML = "";
    var lane = document.createElement("div");
    lane.className = "tl-lane";
    root.appendChild(lane);

    var axis = document.createElement("div");
    axis.className = "tl-axis";
    lane.appendChild(axis);

    var ticks = (data && data.ticks) || [];
    var start = parseTime(data && data.min);
    var end = parseTime(data && data.max);
    if (!ticks.length || start === null || end === null) {
      var empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "还没有运行刻度。";
      root.appendChild(empty);
      return;
    }
    var span = Math.max(end - start, 60000);
    function pct(millis) {
      var ratio = (millis - start) / span;
      return Math.min(99.4, Math.max(0.4, ratio * 100));
    }

    var hour = new Date(start);
    hour.setMinutes(0, 0, 0);
    if (hour.getTime() < start) hour.setHours(hour.getHours() + 1);
    while (hour.getTime() <= end) {
      var tick = document.createElement("span");
      tick.className = "tl-tick";
      tick.style.left = pct(hour.getTime()) + "%";
      lane.appendChild(tick);
      var text = document.createElement("span");
      text.className = "tl-hour";
      text.style.left = pct(hour.getTime()) + "%";
      text.textContent = label(hour.getTime());
      lane.appendChild(text);
      hour.setHours(hour.getHours() + 1);
    }

    var finalMark = parseTime(data.final);
    ticks.forEach(function (item) {
      var at = parseTime(item.event);
      if (at === null) return;
      var mark = document.createElement("span");
      mark.className = "tl-event";
      if (item.late) mark.classList.add("is-late");
      if (finalMark !== null && at > finalMark) mark.classList.add("is-pending");
      mark.style.left = pct(at) + "%";
      mark.title = item.ref + " · " + label(at) + (item.late ? " · 迟到" : "");
      lane.appendChild(mark);
    });

    if (finalMark !== null) {
      var line = document.createElement("span");
      line.className = "tl-mark";
      line.style.left = pct(finalMark) + "%";
      lane.appendChild(line);
      var tag = document.createElement("span");
      tag.className = "tl-mark-label";
      tag.style.left = pct(finalMark) + "%";
      tag.textContent = "水位 " + label(finalMark);
      lane.appendChild(tag);
    }
  }

  function boot() {
    var root = document.getElementById("timeline");
    var holder = document.getElementById("timeline-data");
    if (!root || !holder) return;
    var data = {};
    try {
      data = JSON.parse(holder.textContent.trim() || "{}");
    } catch (err) {
      data = {};
    }
    render(root, data);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
