(function () {
  const STATUS_OPTIONS = [
    { value: "participating", label: "참여 중" },
    { value: "inactive", label: "비활성" },
    { value: "closed", label: "종료" },
  ];

  const TAG_OPTIONS = [
    { value: "research_service", label: "연구용역" },
    { value: "production_service", label: "제작용역" },
    { value: "goods_purchase", label: "물품구매" },
    { value: "general_service", label: "일반용역" },
    { value: "other", label: "기타" },
  ];

  const DEADLINE_CONFIDENCE_OPTIONS = [
    { value: "exact", label: "확정" },
    { value: "estimated", label: "추정" },
    { value: "unknown", label: "확인필요" },
  ];

  const state = {
    currentMonth: currentMonthString(new Date()),
    notices: [],
    savedSites: [],
    events: [],
    selectedOnly: false,
    query: "",
    selectedBy: localStorage.getItem("calendar.selectedBy") || "관리자",
    selectedDetail: null,
    savingDetail: false,
    detailMessage: "",
  };

  const elements = {
    selectedBy: document.getElementById("selectedBy"),
    refreshButton: document.getElementById("refreshButton"),
    todayButton: document.getElementById("todayButton"),
    prevMonthButton: document.getElementById("prevMonthButton"),
    nextMonthButton: document.getElementById("nextMonthButton"),
    currentMonthLabel: document.getElementById("currentMonthLabel"),
    searchInput: document.getElementById("searchInput"),
    selectedOnlyCheckbox: document.getElementById("selectedOnlyCheckbox"),
    siteList: document.getElementById("siteList"),
    savedSiteList: document.getElementById("savedSiteList"),
    calendarGrid: document.getElementById("calendarGrid"),
    detailPanel: document.getElementById("detailPanel"),
    manualAddButton: document.getElementById("manualAddButton"),
    manualModal: document.getElementById("manualModal"),
    manualCloseButton: document.getElementById("manualCloseButton"),
    manualForm: document.getElementById("manualForm"),
    manualTitle: document.getElementById("manualTitle"),
    manualOrganization: document.getElementById("manualOrganization"),
    manualDeadline: document.getElementById("manualDeadline"),
    manualAmountValue: document.getElementById("manualAmountValue"),
    manualPriorityScore: document.getElementById("manualPriorityScore"),
    manualNoticeTag: document.getElementById("manualNoticeTag"),
    manualSourceUrl: document.getElementById("manualSourceUrl"),
    manualStatus: document.getElementById("manualStatus"),
    manualOwnerName: document.getElementById("manualOwnerName"),
    manualDeadlineConfidence: document.getElementById("manualDeadlineConfidence"),
    manualMemo: document.getElementById("manualMemo"),
    manualMessage: document.getElementById("manualMessage"),
    manualSubmitButton: document.getElementById("manualSubmitButton"),
  };

  init();

  function init() {
    elements.selectedBy.value = state.selectedBy;
    elements.selectedBy.addEventListener("change", () => {
      state.selectedBy = normalizeSelectedBy(elements.selectedBy.value);
      elements.selectedBy.value = state.selectedBy;
      localStorage.setItem("calendar.selectedBy", state.selectedBy);
    });

    elements.searchInput.addEventListener(
      "input",
      debounce(() => {
        state.query = elements.searchInput.value.trim();
        loadNotices();
      }, 250)
    );

    elements.selectedOnlyCheckbox.addEventListener("change", () => {
      state.selectedOnly = elements.selectedOnlyCheckbox.checked;
      loadNotices();
    });

    elements.refreshButton.addEventListener("click", refreshAll);
    elements.todayButton.addEventListener("click", async () => {
      state.currentMonth = currentMonthString(new Date());
      updateMonthBar();
      await loadEvents();
    });
    elements.prevMonthButton.addEventListener("click", async () => {
      if (!canGoPrev()) return;
      state.currentMonth = offsetMonth(state.currentMonth, -1);
      updateMonthBar();
      await loadEvents();
    });
    elements.nextMonthButton.addEventListener("click", async () => {
      if (!canGoNext()) return;
      state.currentMonth = offsetMonth(state.currentMonth, 1);
      updateMonthBar();
      await loadEvents();
    });

    elements.manualAddButton.addEventListener("click", openManualModal);
    elements.manualCloseButton.addEventListener("click", closeManualModal);
    elements.manualModal.addEventListener("click", (event) => {
      if (event.target === elements.manualModal) {
        closeManualModal();
      }
    });
    elements.manualForm.addEventListener("submit", submitManualNotice);

    updateMonthBar();
    renderDetail(null);
    refreshAll();
  }

  function normalizeSelectedBy(value) {
    return (value || "").trim() || "관리자";
  }

  function currentMonthString(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
  }

  function offsetMonth(month, delta) {
    const [year, monthNumber] = month.split("-").map(Number);
    return currentMonthString(new Date(year, monthNumber - 1 + delta, 1));
  }

  function monthLabel(month) {
    const [year, monthNumber] = month.split("-").map(Number);
    return `${year}년 ${monthNumber}월`;
  }

  function allowedStartMonth() {
    const now = new Date();
    return `${now.getFullYear() - 3}-01`;
  }

  function allowedEndMonth() {
    const now = new Date();
    return `${now.getFullYear()}-12`;
  }

  function canGoPrev() {
    return state.currentMonth > allowedStartMonth();
  }

  function canGoNext() {
    return state.currentMonth < allowedEndMonth();
  }

  function updateMonthBar() {
    elements.currentMonthLabel.textContent = monthLabel(state.currentMonth);
    elements.prevMonthButton.disabled = !canGoPrev();
    elements.nextMonthButton.disabled = !canGoNext();
  }

  async function refreshAll() {
    await Promise.allSettled([loadNotices(), loadEvents()]);
    if (state.selectedDetail?.id) {
      await openSavedNotice(state.selectedDetail.id);
    }
  }

  async function fetchJson(url, options, fallbackMessage) {
    const response = await fetch(url, options);
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (!response.ok) {
      throw new Error(payload?.detail || fallbackMessage);
    }
    return payload;
  }

  async function loadNotices() {
    const params = new URLSearchParams();
    if (state.selectedOnly) params.set("selected_only", "true");
    if (state.query) params.set("q", state.query);

    try {
      const queryString = params.toString();
      const data = await fetchJson(
        `/api/calendar/notices${queryString ? `?${queryString}` : ""}`,
        undefined,
        "원본 공고 목록을 불러오지 못했습니다."
      );
      state.notices = data.sites || [];
      state.savedSites = data.saved_sites || [];
      renderNotices();
      renderSavedNotices();
    } catch (error) {
      state.notices = [];
      state.savedSites = [];
      elements.siteList.innerHTML = `<div class="detail-empty">${escapeHtml(error.message)}</div>`;
      elements.savedSiteList.innerHTML = `<div class="detail-empty">${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadEvents() {
    try {
      const data = await fetchJson(
        `/api/calendar/events?month=${state.currentMonth}`,
        undefined,
        "캘린더 일정을 불러오지 못했습니다."
      );
      state.events = data.events || [];
      renderCalendar();
    } catch (error) {
      state.events = [];
      renderCalendarError(error.message);
    }
  }

  async function toggleSelection(noticeId, selected) {
    const selectedBy = requireSelectedBy();
    return fetchJson(
      "/api/calendar/selections",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          notice_id: noticeId,
          selected,
          selected_by: selectedBy,
        }),
      },
      "선택 상태를 저장하지 못했습니다."
    );
  }

  async function deactivateSavedNotice(savedNoticeId) {
    const selectedBy = requireSelectedBy();
    return fetchJson(
      `/api/calendar/saved-notices/${savedNoticeId}/deactivate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_by: selectedBy }),
      },
      "참여사업을 비활성화하지 못했습니다."
    );
  }

  async function openSavedNotice(savedNoticeId) {
    try {
      state.selectedDetail = await fetchJson(
        `/api/calendar/saved-notices/${savedNoticeId}`,
        undefined,
        "상세 정보를 불러오지 못했습니다."
      );
      state.detailMessage = "";
      renderDetail(state.selectedDetail);
    } catch (error) {
      state.detailMessage = error.message;
      renderDetail(state.selectedDetail);
    }
  }

  function renderNotices() {
    elements.siteList.innerHTML = "";
    if (!state.notices.length) {
      elements.siteList.innerHTML = '<div class="detail-empty">표시할 원본 공고가 없습니다.</div>';
      return;
    }

    for (const site of state.notices) {
      const selectedCount = site.items.filter((item) => item.selected).length;
      const wrapper = document.createElement("details");
      wrapper.className = "site-group";
      wrapper.open = false;

      const summary = document.createElement("summary");
      summary.textContent =
        selectedCount > 0
          ? `${site.site_name} (${site.items.length}, 선택 ${selectedCount})`
          : `${site.site_name} (${site.items.length})`;
      wrapper.appendChild(summary);

      const items = document.createElement("div");
      items.className = "site-items";

      for (const item of site.items) {
        const row = document.createElement("div");
        row.className = `notice-row${item.selected ? " selected" : ""}`;

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = item.selected;
        checkbox.addEventListener("change", async (event) => {
          try {
            const result = await toggleSelection(item.notice_id, event.target.checked);
            await Promise.allSettled([loadNotices(), loadEvents()]);
            if (result?.saved_notice_id && event.target.checked) {
              state.detailMessage = "참여사업으로 등록했습니다.";
              await openSavedNotice(result.saved_notice_id);
            } else if (!event.target.checked && state.selectedDetail?.source_notice_id === item.notice_id) {
              state.selectedDetail = null;
              state.detailMessage = "";
              renderDetail(null);
            }
          } catch (error) {
            event.target.checked = !event.target.checked;
            alert(error.message);
          }
        });

        const main = document.createElement("div");
        main.className = "notice-main";
        if (item.selected && item.saved_notice_id) {
          main.addEventListener("click", async () => openSavedNotice(item.saved_notice_id));
        }

        const title = document.createElement("div");
        title.className = "notice-title";
        title.innerHTML = [
          `<span class="priority">${escapeHtml(item.priority_label)}</span>`,
          `<span class="pill tag-${escapeHtml(item.notice_tag || "other")}">${escapeHtml(item.notice_tag_label)}</span>`,
          `<span>${escapeHtml(item.title)}</span>`,
        ].join(" ");

        const sub = document.createElement("div");
        sub.className = "notice-sub";
        const deadlineLabel = item.deadline_confidence_label
          ? `${formatDateTime(item.primary_deadline_at)} | ${item.deadline_confidence_label}`
          : formatDateTime(item.primary_deadline_at);
        sub.textContent = [
          item.organization || "미기재",
          deadlineLabel,
          item.amount_text || "미기재",
        ].join(" | ");

        const actions = document.createElement("div");
        actions.className = "notice-row-actions";
        if (item.selected && item.saved_notice_id) {
          const openButton = document.createElement("button");
          openButton.type = "button";
          openButton.className = "small-button";
          openButton.textContent = "상세";
          openButton.addEventListener("click", () => openSavedNotice(item.saved_notice_id));
          actions.appendChild(openButton);
        }

        main.appendChild(title);
        main.appendChild(sub);
        row.appendChild(checkbox);
        row.appendChild(main);
        row.appendChild(actions);
        items.appendChild(row);
      }

      wrapper.appendChild(items);
      setupAccordionBehavior(wrapper, elements.siteList);
      elements.siteList.appendChild(wrapper);
    }
  }

  function renderSavedNotices() {
    elements.savedSiteList.innerHTML = "";
    if (!state.savedSites.length) {
      elements.savedSiteList.innerHTML =
        '<div class="detail-empty">직접등록 또는 과거이관 참여사업이 없습니다.</div>';
      return;
    }

    for (const site of state.savedSites) {
      const wrapper = document.createElement("details");
      wrapper.className = "site-group";
      wrapper.open = false;

      const summary = document.createElement("summary");
      summary.textContent = `${site.site_name} (${site.items.length})`;
      wrapper.appendChild(summary);

      const items = document.createElement("div");
      items.className = "site-items";

      for (const item of site.items) {
        const row = document.createElement("div");
        row.className = "notice-row saved-row selected";

        const main = document.createElement("div");
        main.className = "notice-main";
        main.addEventListener("click", () => openSavedNotice(item.saved_notice_id));

        const title = document.createElement("div");
        title.className = "notice-title";
        title.innerHTML = [
          `<span class="priority">${escapeHtml(item.priority_label)}</span>`,
          `<span class="pill tag-${escapeHtml(item.notice_tag || "other")}">${escapeHtml(item.notice_tag_label)}</span>`,
          `<span class="pill">${escapeHtml(item.origin_type_label)}</span>`,
          `<span>${escapeHtml(item.title)}</span>`,
        ].join(" ");

        const sub = document.createElement("div");
        sub.className = "notice-sub";
        const deadlineLabel = item.deadline_confidence_label
          ? `${formatDateTime(item.primary_deadline_at)} | ${item.deadline_confidence_label}`
          : formatDateTime(item.primary_deadline_at);
        sub.textContent = [
          item.organization || "미기재",
          deadlineLabel,
          item.amount_text || "미기재",
          item.status_label || "",
        ]
          .filter(Boolean)
          .join(" | ");

        const detailButton = document.createElement("button");
        detailButton.type = "button";
        detailButton.className = "small-button";
        detailButton.textContent = "상세";
        detailButton.addEventListener("click", () => openSavedNotice(item.saved_notice_id));

        const deactivateButton = document.createElement("button");
        deactivateButton.type = "button";
        deactivateButton.className = "small-button";
        deactivateButton.textContent = "비활성화";
        deactivateButton.addEventListener("click", async () => {
          try {
            await deactivateSavedNotice(item.saved_notice_id);
            if (state.selectedDetail?.id === item.saved_notice_id) {
              state.selectedDetail = null;
              renderDetail(null);
            }
            await Promise.allSettled([loadNotices(), loadEvents()]);
          } catch (error) {
            alert(error.message);
          }
        });

        main.appendChild(title);
        main.appendChild(sub);
        row.appendChild(main);
        row.appendChild(detailButton);
        row.appendChild(deactivateButton);
        items.appendChild(row);
      }

      wrapper.appendChild(items);
      setupAccordionBehavior(wrapper, elements.savedSiteList);
      elements.savedSiteList.appendChild(wrapper);
    }
  }

  function renderCalendar() {
    elements.calendarGrid.innerHTML = "";

    const [year, month] = state.currentMonth.split("-").map(Number);
    const firstDay = new Date(year, month - 1, 1);
    const startDate = new Date(firstDay);
    startDate.setDate(firstDay.getDate() - firstDay.getDay());

    const eventsByDate = new Map();
    for (const event of state.events) {
      if (!event.primary_deadline_at) continue;
      const key = localDateKey(new Date(event.primary_deadline_at));
      if (!eventsByDate.has(key)) {
        eventsByDate.set(key, []);
      }
      eventsByDate.get(key).push(event);
    }

    for (let index = 0; index < 42; index += 1) {
      const cellDate = new Date(startDate);
      cellDate.setDate(startDate.getDate() + index);
      const key = localDateKey(cellDate);
      const events = eventsByDate.get(key) || [];

      const cell = document.createElement("div");
      cell.className = "calendar-cell";
      if (cellDate.getMonth() !== month - 1) cell.classList.add("muted");
      if (localDateKey(new Date()) === key) cell.classList.add("today");

      const day = document.createElement("div");
      day.className = "calendar-day";
      day.textContent = String(cellDate.getDate());
      cell.appendChild(day);

      events.slice(0, 2).forEach((event) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "calendar-event";
        button.textContent = `${event.priority_label} ${event.title}`;
        button.addEventListener("click", () => openSavedNotice(event.saved_notice_id));
        cell.appendChild(button);
      });

      if (events.length > 2) {
        const more = document.createElement("button");
        more.type = "button";
        more.className = "calendar-more";
        more.textContent = `+${events.length - 2} more`;
        more.addEventListener("click", () => openSavedNotice(events[0].saved_notice_id));
        cell.appendChild(more);
      }

      elements.calendarGrid.appendChild(cell);
    }
  }

  function renderCalendarError(message) {
    elements.calendarGrid.innerHTML = `<div class="detail-empty">${escapeHtml(message)}</div>`;
  }

  function buildIrisHelperUrl(detail) {
    if (!detail || detail.site_code !== "iris") {
      return "";
    }

    const params = new URLSearchParams();
    params.set("title", detail.title || "");

    const rawYear = detail.raw_payload?.bsns_yy;
    const deadlineYear = detail.primary_deadline_at
      ? new Date(detail.primary_deadline_at).getFullYear()
      : null;
    const year = rawYear || (deadlineYear ? String(deadlineYear) : "");
    if (year) {
      params.set("year", year);
    }

    if (detail.organization) {
      params.set("organization", detail.organization);
    }
    if (detail.source_url) {
      params.set("source_url", detail.source_url);
    }

    return `/helpers/iris-search?${params.toString()}`;
  }

  function renderDetail(detail) {
    if (!detail) {
      elements.detailPanel.innerHTML =
        '<div class="detail-empty">사업을 선택하면 상세 정보가 표시됩니다.</div>';
      return;
    }

    const statusOptions = STATUS_OPTIONS.map((option) => {
      const selected = detail.status === option.value ? " selected" : "";
      return `<option value="${escapeHtml(option.value)}"${selected}>${escapeHtml(option.label)}</option>`;
    }).join("");

    const deadlineConfidenceOptions = DEADLINE_CONFIDENCE_OPTIONS.map((option) => {
      const selected = detail.deadline_confidence === option.value ? " selected" : "";
      return `<option value="${escapeHtml(option.value)}"${selected}>${escapeHtml(option.label)}</option>`;
    }).join("");

    const tagOptions = TAG_OPTIONS.map((option) => {
      const selected = detail.notice_tag === option.value ? " selected" : "";
      return `<option value="${escapeHtml(option.value)}"${selected}>${escapeHtml(option.label)}</option>`;
    }).join("");

    const saveLabel = state.savingDetail ? "저장 중..." : "저장";
    const disabled = state.savingDetail ? " disabled" : "";
    const messageHtml = state.detailMessage
      ? `<div class="detail-message">${escapeHtml(state.detailMessage)}</div>`
      : "";
    const sourceLinkHtml = detail.source_url
      ? `<a href="${escapeHtml(detail.source_url)}" target="_blank" rel="noreferrer">원문 열기</a>`
      : `<span class="detail-empty">원문 링크 없음</span>`;
    const irisHelperUrl = buildIrisHelperUrl(detail);
    const irisHelperHtml = irisHelperUrl
      ? `<a href="${escapeHtml(irisHelperUrl)}" target="_blank" rel="noreferrer">IRIS 검색 도우미</a>`
      : "";
    const attachmentHtml = Array.isArray(detail.attachments) && detail.attachments.length
      ? `<div class="detail-attachments"><strong>첨부파일</strong>${detail.attachments.map((attachment) => (
          `<a href="${escapeHtml(attachment.download_url)}" target="_blank" rel="noreferrer">` +
          `${escapeHtml(attachment.attachment_category_label)} - ${escapeHtml(attachment.attachment_name)}` +
          `${attachment.is_summary_source ? " (요약원본)" : ""}</a>`
        )).join("")}</div>`
      : "";
    const summaryHtml = detail.summary && detail.summary.summary_status === "done"
      ? `<div class="detail-summary">
          <strong>요약</strong>
          <div class="detail-row"><strong>사업목적</strong>${formatSummaryValueHtml(detail.summary.purpose)}</div>
          <div class="detail-row"><strong>핵심수행업무</strong>${formatSummaryValueHtml(detail.summary.core_tasks)}</div>
          <div class="detail-row"><strong>요구성능</strong>${formatSummaryValueHtml(detail.summary.required_performance)}</div>
          <div class="detail-row"><strong>정량 목표</strong>${formatSummaryValueHtml(detail.summary.quantitative_targets)}</div>
          <div class="detail-row"><strong>기간</strong>${formatSummaryValueHtml(detail.summary.period_text)}</div>
        </div>`
      : "";
    const aiEvaluationHtml = detail.ai_evaluation && detail.ai_evaluation.status === "done"
      ? `<div class="detail-summary">
          <strong>AI 적합성</strong>
          <div class="detail-row"><strong>적합도</strong>${escapeHtml(formatAiFitScore(detail.ai_evaluation))}</div>
          <div class="detail-row"><strong>판단 이유</strong>${formatSummaryValueHtml(detail.ai_evaluation.summary_for_slack || detail.ai_evaluation.reason || "미확인")}</div>
          <div class="detail-row"><strong>매칭 역량</strong>${formatSummaryValueHtml((detail.ai_evaluation.matched_capabilities || []).join(" / ") || "미확인")}</div>
          <div class="detail-row"><strong>검토 리스크</strong>${formatSummaryValueHtml((detail.ai_evaluation.risks || []).join(" / ") || "미확인")}</div>
        </div>`
      : "";

    elements.detailPanel.innerHTML = `
      <div class="detail-grid">
        <div class="detail-header">
          <h2>${escapeHtml(detail.title)}</h2>
          <div class="detail-badges">
            <span class="pill priority">${escapeHtml(detail.priority_label)}</span>
            <span class="pill tag-${escapeHtml(detail.notice_tag || "other")}">${escapeHtml(detail.notice_tag_label)}</span>
            <span class="pill">${escapeHtml(detail.site_name)}</span>
            <span class="pill">${escapeHtml(detail.origin_type_label)}</span>
          </div>
        </div>
        ${messageHtml}
        <div class="detail-row"><strong>발주처</strong>${escapeHtml(detail.organization || "미기재")}</div>
        <div class="detail-row"><strong>입찰서 제출 마감일</strong>${formatDateTime(detail.primary_deadline_at)}</div>
        <div class="detail-row"><strong>금액</strong>${escapeHtml(detail.amount_text || "미기재")}</div>
        <div class="detail-row"><strong>선택자</strong>${escapeHtml(detail.selected_by)}</div>
        <div class="detail-row"><strong>선택일시</strong>${formatDateTime(detail.selected_at)}</div>
        <div class="detail-row"><strong>날짜 신뢰도</strong>${escapeHtml(detail.deadline_confidence_label)}</div>
        <div class="detail-actions">
          <label>상태
            <select id="detailStatusSelect"${disabled}>${statusOptions}</select>
          </label>
          <label>담당자
            <input id="detailOwnerInput" type="text" value="${escapeHtml(detail.owner_name || "")}"${disabled} />
          </label>
          <label>입찰서 제출 마감일
            <input id="detailDeadlineInput" type="datetime-local" value="${toDatetimeLocalValue(detail.primary_deadline_at)}"${disabled} />
          </label>
          <label>금액(원)
            <input id="detailAmountValueInput" type="number" min="0" step="1" value="${detail.amount_value ?? ""}"${disabled} />
          </label>
          <label>중요도
            <select id="detailPrioritySelect"${disabled}>
              <option value="3"${detail.priority_score === 3 ? " selected" : ""}>★★★</option>
              <option value="2"${detail.priority_score === 2 ? " selected" : ""}>★★☆</option>
              <option value="1"${detail.priority_score === 1 ? " selected" : ""}>★☆☆</option>
              <option value="0"${detail.priority_score === 0 ? " selected" : ""}>☆☆☆</option>
            </select>
          </label>
          <label>태그
            <select id="detailTagSelect"${disabled}>${tagOptions}</select>
          </label>
          <label>날짜 신뢰도
            <select id="detailDeadlineConfidenceSelect"${disabled}>${deadlineConfidenceOptions}</select>
          </label>
          <label>원문 링크
            <input id="detailSourceUrlInput" type="url" value="${escapeHtml(detail.source_url || "")}"${disabled} />
          </label>
          <label>메모
            <textarea id="detailMemoInput" rows="5"${disabled}>${escapeHtml(detail.memo || "")}</textarea>
          </label>
          <button id="detailSaveButton" type="button"${disabled}>${saveLabel}</button>
          ${sourceLinkHtml}
          ${irisHelperHtml}
          ${aiEvaluationHtml}
          ${summaryHtml}
          ${attachmentHtml}
        </div>
      </div>
    `;

    document.getElementById("detailSaveButton").addEventListener("click", saveDetailChanges);
  }

  async function saveDetailChanges() {
    if (!state.selectedDetail) return;
    state.savingDetail = true;
    state.detailMessage = "";
    renderDetail(state.selectedDetail);

    const payload = {
      status: document.getElementById("detailStatusSelect").value,
      owner_name: document.getElementById("detailOwnerInput").value.trim(),
      memo: document.getElementById("detailMemoInput").value,
      primary_deadline_at: fromDatetimeLocalValue(document.getElementById("detailDeadlineInput").value),
      amount_value: toNullableNumber(document.getElementById("detailAmountValueInput").value),
      priority_score: Number(document.getElementById("detailPrioritySelect").value),
      notice_tag: document.getElementById("detailTagSelect").value,
      source_url: document.getElementById("detailSourceUrlInput").value.trim(),
      deadline_confidence: document.getElementById("detailDeadlineConfidenceSelect").value,
    };

    try {
      state.selectedDetail = await fetchJson(
        `/api/calendar/saved-notices/${state.selectedDetail.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
        "상세 정보를 저장하지 못했습니다."
      );
      state.detailMessage = "저장했습니다.";
      await Promise.allSettled([loadNotices(), loadEvents()]);
      renderDetail(state.selectedDetail);
    } catch (error) {
      state.detailMessage = error.message;
      renderDetail(state.selectedDetail);
    } finally {
      state.savingDetail = false;
    }
  }

  function openManualModal() {
    elements.manualForm.reset();
    elements.manualMessage.classList.add("hidden");
    elements.manualMessage.textContent = "";
    elements.manualModal.classList.remove("hidden");
    elements.manualTitle.focus();
  }

  function closeManualModal() {
    elements.manualModal.classList.add("hidden");
  }

  async function submitManualNotice(event) {
    event.preventDefault();
    const selectedBy = requireSelectedBy();
    elements.manualSubmitButton.disabled = true;

    const payload = {
      title: elements.manualTitle.value.trim(),
      organization: elements.manualOrganization.value.trim() || null,
      primary_deadline_at: fromDatetimeLocalValue(elements.manualDeadline.value),
      amount_value: toNullableNumber(elements.manualAmountValue.value),
      priority_score: Number(elements.manualPriorityScore.value),
      notice_tag: elements.manualNoticeTag.value,
      source_url: elements.manualSourceUrl.value.trim() || null,
      status: elements.manualStatus.value,
      owner_name: elements.manualOwnerName.value.trim() || null,
      memo: elements.manualMemo.value || null,
      selected_by: selectedBy,
      deadline_confidence: elements.manualDeadlineConfidence.value,
    };

    try {
      const result = await fetchJson(
        "/api/calendar/manual-notices",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
        "직접 등록에 실패했습니다."
      );
      elements.manualMessage.classList.remove("hidden");
      elements.manualMessage.textContent = "참여사업을 직접 등록했습니다.";
      await Promise.allSettled([loadNotices(), loadEvents()]);
      await openSavedNotice(result.id);
      setTimeout(closeManualModal, 500);
    } catch (error) {
      elements.manualMessage.classList.remove("hidden");
      elements.manualMessage.textContent = error.message;
    } finally {
      elements.manualSubmitButton.disabled = false;
    }
  }

  function renderCalendarError(message) {
    elements.calendarGrid.innerHTML = `<div class="detail-empty">${escapeHtml(message)}</div>`;
  }

  function requireSelectedBy() {
    const selectedBy = normalizeSelectedBy(elements.selectedBy.value);
    elements.selectedBy.value = selectedBy;
    state.selectedBy = selectedBy;
    localStorage.setItem("calendar.selectedBy", selectedBy);
    return selectedBy;
  }

  function setupAccordionBehavior(wrapper, parent) {
    wrapper.addEventListener("toggle", () => {
      const groups = [...parent.querySelectorAll("details.site-group")];
      if (!wrapper.open) {
        wrapper.style.order = "0";
        return;
      }
      groups.forEach((other) => {
        if (other !== wrapper) {
          other.open = false;
        }
        other.style.order = other === wrapper ? "-1" : "0";
      });
    });
  }

  function localDateKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
      date.getDate()
    ).padStart(2, "0")}`;
  }

  function formatDateTime(value) {
    if (!value) return "미기재";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
      date.getDate()
    ).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(
      date.getMinutes()
    ).padStart(2, "0")}`;
  }

  function formatSummaryValueHtml(value) {
    const parts = String(value || "")
      .split("/")
      .map((part) => part.trim().replace(/^[◦\-·]\s*/, ""))
      .filter(Boolean);
    const items = (parts.length ? parts : ["미확인"])
      .map((part) => `<li>${escapeHtml(part)}</li>`)
      .join("");
    return `<ul class="detail-summary-list">${items}</ul>`;
  }

  function formatAiFitScore(evaluation) {
    const levelLabels = { high: "높음", medium: "보통", low: "낮음" };
    const actionLabels = { bid: "참여 검토", review: "검토 권장", watch: "관찰", ignore: "제외 권장" };
    const score = evaluation.fit_score ?? "미확인";
    const level = levelLabels[evaluation.fit_level] || evaluation.fit_level || "미확인";
    const action = actionLabels[evaluation.recommended_action] || evaluation.recommended_action || "미확인";
    return `${score}점 / ${level} / ${action}`;
  }

  function toDatetimeLocalValue(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function fromDatetimeLocalValue(value) {
    if (!value) return null;
    return new Date(value).toISOString();
  }

  function toNullableNumber(value) {
    if (value === "" || value === null || value === undefined) return null;
    const parsed = Number(String(value).replaceAll(",", ""));
    return Number.isFinite(parsed) ? parsed : null;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function debounce(fn, delay) {
    let timeoutId = null;
    return (...args) => {
      if (timeoutId) clearTimeout(timeoutId);
      timeoutId = setTimeout(() => fn(...args), delay);
    };
  }
})();
