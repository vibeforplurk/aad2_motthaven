/* ==========================================================================
   世界線角色年表 — 前端互動
   --------------------------------------------------------------------------
   這個檔案負責：左右版本切換、年份跳轉、scrollspy、角色卡同步、
   時間軸背景切換。
   年表內容與九種排版組合都由 build.py 預先產生；這裡只做顯示切換。
   ========================================================================== */

(() => {
  "use strict";

  const timeline = document.getElementById("timeline");
  if (!timeline) return;

  const variants = Array.from(
    timeline.querySelectorAll(".timeline-variant")
  );
  const topbar = document.querySelector(".topbar");
  const yearOutput = document.getElementById("current-year");
  const jump = document.getElementById("year-jump");
  const pairButtons = Array.from(
    document.querySelectorAll("[data-pair-side]")
  );
  const castCards = Array.from(document.querySelectorAll(".char-card"));
  const backgroundSegments = Array.from(
    timeline.querySelectorAll(".timeline__bg--segment")
  );
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const storageKey = "parallel-timeline-ui:v3";

  function isVariantEnabled(side, variant) {
    return pairButtons.some(
      (button) =>
        button.dataset.pairSide === side &&
        Number(button.dataset.pairVariant) === Number(variant) &&
        !button.disabled
    );
  }

  function firstEnabledVariant(side) {
    const button = pairButtons.find(
      (item) => item.dataset.pairSide === side && !item.disabled
    );
    return button ? Number(button.dataset.pairVariant) : 1;
  }

  function resolveVariant(side, variant) {
    const value = Number(variant);
    return isVariantEnabled(side, value)
      ? value
      : firstEnabledVariant(side);
  }

  function initialVariant(side, paramValue, prefValue) {
    if (Number.isInteger(paramValue)) {
      return resolveVariant(side, paramValue);
    }
    if (
      Number.isInteger(prefValue) &&
      isVariantEnabled(side, prefValue)
    ) {
      return prefValue;
    }
    return firstEnabledVariant(side);
  }

  let currentPair = { left: 1, right: 1 };
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

  /* ------------------------------------------------------------ 版本查詢 */

  function variantFor(left, right) {
    return variants.find(
      (variant) =>
        Number(variant.dataset.left) === left &&
        Number(variant.dataset.right) === right
    );
  }

  function activeVariant() {
    return variantFor(currentPair.left, currentPair.right);
  }

  function activeBands() {
    const variant = activeVariant();
    return variant ? Array.from(variant.querySelectorAll(".band")) : [];
  }

  function findBand(year) {
    const variant = activeVariant();
    if (!variant) return null;
    return variant.querySelector(`.band[data-year="${year}"]`);
  }

  function nearestBand(year) {
    const bands = activeBands();
    if (!bands.length) return null;
    if (!Number.isFinite(year)) return bands[0];
    return (
      bands.find((band) => Number(band.dataset.year) >= year) ||
      bands[bands.length - 1]
    );
  }

  /* ------------------------------------------------------------ 年份顯示 */

  function setYearDisplay(year, syncSelect = true) {
    if (yearOutput && yearOutput.textContent !== String(year)) {
      yearOutput.textContent = String(year);
    }
    if (
      syncSelect &&
      jump &&
      !jump.disabled &&
      document.activeElement !== jump &&
      jump.value !== String(year)
    ) {
      jump.value = String(year);
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
    const bands = activeBands();

    if (!bands.length) {
      if (yearOutput) yearOutput.textContent = "—";
      return;
    }

    if (pinnedYear !== null) {
      setYearDisplay(pinnedYear);
      return;
    }

    const topbarHeight = topbar ? topbar.getBoundingClientRect().height : 0;
    const offset = topbarHeight + Math.min(180, window.innerHeight * 0.22);
    let current = null;

    for (const band of bands) {
      const top = band.getBoundingClientRect().top;
      if (top <= offset) {
        current = band;
      } else {
        break;
      }
    }

    if (!current) {
      current = bands[0];
    }

    const documentElement = document.documentElement;
    const atBottom =
      documentElement.scrollHeight > window.innerHeight + 200 &&
      window.innerHeight + window.scrollY >= documentElement.scrollHeight - 2;
    if (atBottom) {
      current = bands[bands.length - 1];
    }

    setYearDisplay(current.dataset.year);
  }

  function requestYearUpdate() {
    if (scrollFrame) return;
    scrollFrame = window.requestAnimationFrame(updateCurrentYear);
  }

  /* ------------------------------------------------------------ 年份選單 */

  function populateYearJump() {
    if (!jump) return;

    const years = Array.from(
      new Set(activeBands().map((band) => Number(band.dataset.year)))
    ).sort((a, b) => a - b);
    const endYear = Number(timeline.dataset.endYear);
    const desired = Number(yearOutput ? yearOutput.textContent : NaN);

    jump.innerHTML = "";
    jump.disabled = years.length === 0;

    for (const year of years) {
      const option = document.createElement("option");
      option.value = String(year);
      option.textContent =
        year === endYear ? `${year}（現在）` : String(year);
      jump.append(option);
    }

    if (years.includes(desired)) {
      jump.value = String(desired);
    } else if (years.length) {
      jump.value = String(years[0]);
    }
  }

  /* ---------------------------------------------------------- 背景切換點 */

  function boundaryPosition(year, row) {
    const variant = activeVariant();
    if (!variant) return null;

    const band = variant.querySelector(`.band[data-year="${year}"]`);
    if (!band) {
      const next = activeBands().find(
        (item) => Number(item.dataset.year) > year
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
    backgroundFrame = window.requestAnimationFrame(
      updateTimelineBackgrounds
    );
  }

  /* ---------------------------------------------------------- 角色與版本 */

  function updateCast() {
    for (const card of castCards) {
      const world = card.dataset.world;
      const variant = Number(card.dataset.variant);
      const visible =
        world === "I016"
          ? variant === currentPair.left
          : variant === currentPair.right;
      card.hidden = !visible;
    }
  }

  function updatePairButtons() {
    for (const button of pairButtons) {
      const side = button.dataset.pairSide;
      const variant = Number(button.dataset.pairVariant);
      const active =
        !button.disabled &&
        (side === "left"
          ? variant === currentPair.left
          : variant === currentPair.right);
      button.setAttribute("aria-pressed", String(active));
    }
  }

  function updateUrlState() {
    if (!window.history || !window.history.replaceState) return;
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("left", String(currentPair.left));
      url.searchParams.set("right", String(currentPair.right));
      window.history.replaceState(null, "", url);
    } catch (error) {
      /* file:// 或特殊環境不支援時忽略。 */
    }
  }

  function setPair(left, right, options = {}) {
    const { persist = true, updateUrl = true } = options;
    const nextLeft = resolveVariant("left", left);
    const nextRight = resolveVariant("right", right);
    const stickyOffset = topbar
      ? topbar.getBoundingClientRect().height
      : 0;
    const timelineTop = timeline.getBoundingClientRect().top + window.scrollY;
    const readingTimeline =
      window.scrollY + stickyOffset >= timelineTop - 8;
    const previousYear = Number(yearOutput ? yearOutput.textContent : NaN);

    currentPair = { left: nextLeft, right: nextRight };
    pinnedYear = null;

    for (const variant of variants) {
      variant.hidden = !(
        Number(variant.dataset.left) === nextLeft &&
        Number(variant.dataset.right) === nextRight
      );
    }

    updateCast();
    updatePairButtons();
    populateYearJump();
    if (updateUrl) updateUrlState();
    if (persist) writePrefs({ left: nextLeft, right: nextRight });

    const target =
      readingTimeline && Number.isFinite(previousYear)
        ? findBand(previousYear) || nearestBand(previousYear)
        : null;

    if (target) {
      pinnedYear = String(target.dataset.year);
      setYearDisplay(target.dataset.year);
      syncTopbarHeight();
      window.requestAnimationFrame(() => {
        target.scrollIntoView({
          behavior: reduceMotion.matches ? "auto" : "smooth",
          block: "start",
        });
      });
    } else {
      updateCurrentYear();
    }

    requestBackgroundUpdate();
  }

  /* ---------------------------------------------------------------- 跳轉 */

  function scrollToYear(year) {
    const target = findBand(year);
    if (!target) return;

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
      try {
        const url = new URL(window.location.href);
        url.hash = `year-${year}`;
        window.history.replaceState(null, "", url);
      } catch (error) {
        /* 忽略不支援的環境。 */
      }
    }
  }

  /* ---------------------------------------------------------------- 初始化 */

  const prefs = readPrefs();
  const params = new URLSearchParams(window.location.search);
  const paramLeft = params.has("left")
    ? Number(params.get("left"))
    : NaN;
  const paramRight = params.has("right")
    ? Number(params.get("right"))
    : NaN;
  const initialLeft = initialVariant(
    "left",
    paramLeft,
    Number(prefs.left)
  );
  const initialRight = initialVariant(
    "right",
    paramRight,
    Number(prefs.right)
  );

  syncTopbarHeight();
  setPair(initialLeft, initialRight, {
    persist: true,
    updateUrl: true,
  });

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
      if (jump.value) scrollToYear(Number(jump.value));
    });
  }

  for (const button of pairButtons) {
    button.addEventListener("click", () => {
      if (button.disabled) return;
      const side = button.dataset.pairSide;
      const variant = Number(button.dataset.pairVariant);
      if (side === "left") {
        setPair(variant, currentPair.right);
      } else {
        setPair(currentPair.left, variant);
      }
    });
  }

  document.addEventListener("keydown", (event) => {
    if (
      ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(
        event.key
      )
    ) {
      clearPinnedYear();
    }
  });

  const initialHash = window.location.hash;
  const hashMatch = initialHash.match(/^#year-(\d{4})$/);
  if (hashMatch) {
    const target = findBand(Number(hashMatch[1]));
    if (target) {
      window.setTimeout(() => {
        syncTopbarHeight();
        pinnedYear = hashMatch[1];
        setYearDisplay(hashMatch[1]);
        target.scrollIntoView({ block: "start", behavior: "auto" });
      }, 0);
    }
  }
})();
