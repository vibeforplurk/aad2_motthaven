/* ==========================================================================
   平行世界角色年表 — 前端互動
   --------------------------------------------------------------------------
   這個檔案只負責「瀏覽輔助」：年份跳轉、目前年份、隱藏空白年份、聚焦世界線。
   即使停用 JavaScript，年表內容仍然完整可讀。
   ========================================================================== */

(() => {
  "use strict";

  const timeline = document.getElementById("timeline");
  if (!timeline) return;

  const bands = Array.from(timeline.querySelectorAll(".band"));
  const topbar = document.querySelector(".topbar");
  const yearOutput = document.getElementById("current-year");
  const jump = document.getElementById("year-jump");
  const compactButton = document.getElementById("compact-toggle");
  const focusButtons = Array.from(document.querySelectorAll("[data-focus-side]"));
  const backgroundSegments = Array.from(
    timeline.querySelectorAll(".timeline__bg--segment")
  );
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const storageKey = "parallel-timeline-ui:v1";

  const validFocus = new Set(
    focusButtons.map((button) => button.dataset.focusSide).filter(Boolean)
  );

  let scrollFrame = 0;
  let backgroundFrame = 0;
  let pinnedYear = null;

  /* ---------------------------------------------------------------- 偏好 */

  function readPrefs() {
    try {
      const raw = window.localStorage.getItem(storageKey);
      return raw ? JSON.parse(raw) || {} : {};
    } catch (error) {
      return {};
    }
  }

  function writePrefs(patch) {
    try {
      const next = Object.assign(readPrefs(), patch);
      window.localStorage.setItem(storageKey, JSON.stringify(next));
    } catch (error) {
      /* 無痕模式或停用儲存時忽略即可。 */
    }
  }

  /* ------------------------------------------------------------ 年份顯示 */

  function setYearDisplay(year, syncSelect = true) {
    if (yearOutput && yearOutput.textContent !== String(year)) {
      yearOutput.textContent = year;
    }
    if (
      syncSelect &&
      jump &&
      document.activeElement !== jump &&
      jump.value !== String(year)
    ) {
      jump.value = year;
    }
  }

  function clearPinnedYear() {
    if (pinnedYear === null) return;
    pinnedYear = null;
    requestYearUpdate();
  }

  /* ------------------------------------------------------------ 工具列高度 */

  function syncTopbarHeight() {
    if (!topbar) return;
    const height = Math.round(topbar.getBoundingClientRect().height);
    document.documentElement.style.setProperty(
      "--topbar-height",
      `${height}px`
    );
  }

  /* -------------------------------------------------------------- 目前年份 */

  function updateCurrentYear() {
    scrollFrame = 0;
    if (!bands.length) return;

    if (pinnedYear !== null) {
      setYearDisplay(pinnedYear);
      return;
    }

    const topbarHeight = topbar ? topbar.getBoundingClientRect().height : 0;
    const offset =
      topbarHeight + Math.min(180, window.innerHeight * 0.22);
    let current = null;

    for (const band of bands) {
      if (band.offsetParent === null) continue; // 精簡模式中被隱藏
      const top = band.getBoundingClientRect().top;
      if (top <= offset) {
        current = band;
      } else {
        break;
      }
    }

    if (!current) {
      current = bands.find((band) => band.offsetParent !== null) || bands[0];
    }

    // 捲到頁面底部時，讓讀數停在最後一個可見年份（通常是最新年份）。
    const documentElement = document.documentElement;
    const atBottom =
      documentElement.scrollHeight > window.innerHeight + 200 &&
      window.innerHeight + window.scrollY >= documentElement.scrollHeight - 2;
    if (atBottom) {
      const visibleBands = bands.filter((band) => band.offsetParent !== null);
      if (visibleBands.length) {
        current = visibleBands[visibleBands.length - 1];
      }
    }

    if (!current) return;

    setYearDisplay(current.dataset.year);
  }

  function requestYearUpdate() {
    if (scrollFrame) return;
    scrollFrame = window.requestAnimationFrame(updateCurrentYear);
  }

  /* ---------------------------------------------------------- 背景切換點 */

  function boundaryPosition(year, row) {
    const band = document.getElementById(`year-${year}`);
    if (!band || band.offsetParent === null) {
      const next = bands.find(
        (item) =>
          item.offsetParent !== null && Number(item.dataset.year) > year
      );
      return next
        ? next.getBoundingClientRect().top + window.scrollY
        : null;
    }

    if (row <= 1) {
      return band.getBoundingClientRect().top + window.scrollY;
    }

    const entries = Array.from(band.querySelectorAll(".entry"));
    const candidates = entries
      .filter((entry) => Number(entry.dataset.row) >= row)
      .map((entry) => entry.getBoundingClientRect().top + window.scrollY);
    if (candidates.length) {
      return Math.min(...candidates);
    }
    return band.getBoundingClientRect().bottom + window.scrollY;
  }

  function updateTimelineBackgrounds() {
    backgroundFrame = 0;
    if (!backgroundSegments.length) return;

    const rect = timeline.getBoundingClientRect();
    const timelineTop = rect.top + window.scrollY;
    const timelineBottom = timelineTop + timeline.offsetHeight;

    for (const layer of backgroundSegments) {
      const startYear = Number(layer.dataset.bgStartYear);
      const startRow = Number(layer.dataset.bgStartRow || 1);
      const endYear = Number(layer.dataset.bgEndYear || 0);
      const endRow = Number(layer.dataset.bgEndRow || 1);

      const start = startYear ? boundaryPosition(startYear, startRow) : null;
      const end = endYear ? boundaryPosition(endYear, endRow) : null;
      const top = Math.max(timelineTop, start ?? timelineBottom);
      const bottom = Math.min(timelineBottom, end ?? timelineBottom);

      if (bottom > top + 1) {
        layer.style.top = `${top - timelineTop}px`;
        layer.style.height = `${bottom - top}px`;
        layer.classList.add("is-ready");
      } else {
        layer.classList.remove("is-ready");
        layer.style.height = "0px";
      }
    }
  }

  function requestBackgroundUpdate() {
    if (backgroundFrame) return;
    backgroundFrame = window.requestAnimationFrame(updateTimelineBackgrounds);
  }

  /* -------------------------------------------------------------- 精簡模式 */

  function setCompact(on, persist = true) {
    timeline.classList.toggle("is-compact", on);
    if (compactButton) {
      compactButton.setAttribute("aria-pressed", String(on));
    }
    if (persist) writePrefs({ compact: on });
    requestYearUpdate();
    requestBackgroundUpdate();
  }

  /* ------------------------------------------------------------ 世界線聚焦 */

  function setFocus(side, persist = true) {
    const next = validFocus.has(side) ? side : "all";
    document.body.dataset.focusSide = next;
    for (const button of focusButtons) {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.focusSide === next)
      );
    }
    if (persist) writePrefs({ focus: next });
  }

  /* ---------------------------------------------------------------- 跳轉 */

  function scrollToYear(year) {
    const target = document.getElementById(`year-${year}`);
    if (!target) return;

    if (
      timeline.classList.contains("is-compact") &&
      target.classList.contains("band--empty")
    ) {
      setCompact(false);
    }

    // 讓讀數停在使用者選定的年份，直到他主動捲動為止。
    pinnedYear = String(year);
    setYearDisplay(year);

    syncTopbarHeight();
    window.requestAnimationFrame(() => {
      target.scrollIntoView({
        behavior: reduceMotion.matches ? "auto" : "smooth",
        block: "start",
      });
    });

    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, "", `#year-${year}`);
    }
  }

  /* ---------------------------------------------------------------- 初始化 */

  const prefs = readPrefs();
  setCompact(prefs.compact !== false, false);
  setFocus(typeof prefs.focus === "string" ? prefs.focus : "all", false);

  syncTopbarHeight();
  updateCurrentYear();
  updateTimelineBackgrounds();

  if (window.ResizeObserver && topbar) {
    new window.ResizeObserver(syncTopbarHeight).observe(topbar);
  }
  if (window.ResizeObserver) {
    new window.ResizeObserver(requestBackgroundUpdate).observe(timeline);
  }

  window.addEventListener("scroll", requestYearUpdate, { passive: true });
  window.addEventListener("wheel", clearPinnedYear, { passive: true });
  window.addEventListener("touchstart", clearPinnedYear, { passive: true });
  document.addEventListener("pointerdown", clearPinnedYear, true);
  window.addEventListener("resize", () => {
    syncTopbarHeight();
    requestYearUpdate();
    requestBackgroundUpdate();
  });
  window.addEventListener("load", requestBackgroundUpdate);

  if (jump) {
    jump.addEventListener("change", () => {
      if (jump.value) scrollToYear(jump.value);
    });
  }

  if (compactButton) {
    compactButton.addEventListener("click", () => {
      setCompact(compactButton.getAttribute("aria-pressed") !== "true");
    });
  }

  for (const button of focusButtons) {
    button.addEventListener("click", () => {
      setFocus(button.dataset.focusSide || "all");
    });
  }

  document.addEventListener("keydown", (event) => {
    if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) {
      clearPinnedYear();
    }
    if (event.key === "Escape" && document.body.dataset.focusSide !== "all") {
      setFocus("all");
    }
  });

  const initialHash = window.location.hash;
  if (/^#year-\d{4}$/.test(initialHash)) {
    const target = document.getElementById(initialHash.slice(1));
    if (target) {
      window.setTimeout(() => {
        syncTopbarHeight();
        target.scrollIntoView({ block: "start", behavior: "auto" });
      }, 0);
    }
  }
})();
