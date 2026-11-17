/* 表格：点表头排序、数字列按数值比较。只影响展示，不改数据。 */
(function () {
  "use strict";

  function cellValue(row, index) {
    var cell = row.children[index];
    if (!cell) return "";
    return cell.textContent.trim();
  }

  function numeric(text) {
    var clean = text.replace(/,/g, "").replace(/%/g, "");
    if (!clean || !/^-?\d+(\.\d+)?$/.test(clean)) return null;
    return parseFloat(clean);
  }

  function sortTable(table, index, ascending) {
    var body = table.tBodies[0];
    if (!body) return;
    var rows = Array.prototype.slice.call(body.rows);
    rows.sort(function (left, right) {
      var a = cellValue(left, index);
      var b = cellValue(right, index);
      var na = numeric(a);
      var nb = numeric(b);
      if (na !== null && nb !== null) {
        return ascending ? na - nb : nb - na;
      }
      return ascending ? a.localeCompare(b, "zh-Hans-CN") : b.localeCompare(a, "zh-Hans-CN");
    });
    rows.forEach(function (row) {
      body.appendChild(row);
    });
  }

  document.addEventListener("click", function (event) {
    var th = event.target.closest("th");
    if (!th || th.classList.contains("no-sort")) return;
    var table = th.closest("table");
    if (!table || !table.hasAttribute("data-sortable")) return;
    var headers = Array.prototype.slice.call(th.parentNode.children);
    var index = headers.indexOf(th);
    if (index < 0) return;
    var ascending = th.getAttribute("aria-sort") !== "ascending";
    headers.forEach(function (item) {
      item.removeAttribute("aria-sort");
    });
    th.setAttribute("aria-sort", ascending ? "ascending" : "descending");
    sortTable(table, index, ascending);
  });

  document.querySelectorAll("table[data-sortable] th").forEach(function (th, i) {
    if (th.classList.contains("no-sort")) return;
    th.setAttribute("tabindex", "0");
    th.setAttribute("title", "点击排序");
    th.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        th.click();
      }
    });
  });
})();
