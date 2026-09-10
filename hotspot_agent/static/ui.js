(function () {
  "use strict";

  var root = document.documentElement;
  var sidebarKey = "hotspot.sidebar.expanded";
  var mobileQuery = window.matchMedia("(max-width: 760px)");
  var toggleButtons = Array.from(document.querySelectorAll("[data-sidebar-toggle]"));

  function storeSidebar(expanded) {
    try {
      localStorage.setItem(sidebarKey, String(expanded));
    } catch (error) {}
  }

  function syncSidebarControls() {
    var mobileOpen = root.dataset.mobileSidebar === "open";
    var desktopExpanded = root.dataset.sidebar === "expanded";
    toggleButtons.forEach(function (button) {
      var expanded = mobileQuery.matches ? mobileOpen : desktopExpanded;
      button.setAttribute("aria-expanded", String(expanded));
      if (button.classList.contains("desktop-sidebar-toggle")) {
        button.setAttribute("aria-label", desktopExpanded ? "收起侧栏" : "展开侧栏");
      } else {
        button.setAttribute("aria-label", mobileOpen ? "关闭导航" : "打开导航");
      }
    });
  }

  function closeMobileSidebar() {
    delete root.dataset.mobileSidebar;
    syncSidebarControls();
  }

  toggleButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      if (mobileQuery.matches) {
        root.dataset.mobileSidebar = root.dataset.mobileSidebar === "open" ? "closed" : "open";
      } else {
        var expanded = root.dataset.sidebar !== "expanded";
        root.dataset.sidebar = expanded ? "expanded" : "collapsed";
        storeSidebar(expanded);
      }
      syncSidebarControls();
    });
  });

  document.querySelectorAll(".side-link").forEach(function (link) {
    link.addEventListener("click", function () {
      if (mobileQuery.matches) {
        closeMobileSidebar();
      } else if (root.dataset.sidebar !== "expanded") {
        root.dataset.sidebar = "expanded";
        storeSidebar(true);
      }
    });
  });

  var dismissButton = document.querySelector("[data-sidebar-dismiss]");
  if (dismissButton) dismissButton.addEventListener("click", closeMobileSidebar);

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && root.dataset.mobileSidebar === "open") {
      closeMobileSidebar();
      var mobileButton = document.querySelector(".mobile-menu-button");
      if (mobileButton) mobileButton.focus();
    }
  });

  function handleViewportChange() {
    closeMobileSidebar();
    syncSidebarControls();
  }

  if (typeof mobileQuery.addEventListener === "function") {
    mobileQuery.addEventListener("change", handleViewportChange);
  } else {
    mobileQuery.addListener(handleViewportChange);
  }

  var modal = document.querySelector("[data-app-modal]");
  var modalTitle = modal && modal.querySelector("[data-modal-title]");
  var modalBody = modal && modal.querySelector("[data-modal-body]");
  var modalActions = modal && modal.querySelector("[data-modal-actions]");
  var modalConfirm = modal && modal.querySelector("[data-modal-confirm]");
  var modalCancel = modal && modal.querySelector("[data-modal-cancel]");
  var modalResolver = null;
  var modalPreviousFocus = null;

  function closeModal(result) {
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    modal.classList.remove("is-open");
    document.body.classList.remove("modal-open");
    if (modalResolver) {
      var resolve = modalResolver;
      modalResolver = null;
      resolve(result === true);
    }
    if (modalPreviousFocus && typeof modalPreviousFocus.focus === "function") {
      modalPreviousFocus.focus();
    }
    modalPreviousFocus = null;
  }

  function openModal(title, body, options) {
    if (!modal) return Promise.resolve(false);
    options = options || {};
    if (modalResolver) closeModal(false);
    modalPreviousFocus = document.activeElement;
    modalTitle.textContent = title || "确认操作";
    modalBody.replaceChildren();
    if (typeof body === "string") {
      var paragraph = document.createElement("p");
      paragraph.textContent = body;
      modalBody.appendChild(paragraph);
    } else if (body) {
      modalBody.appendChild(body);
    }
    modalActions.hidden = !options.confirm;
    modalConfirm.classList.toggle("danger-button", Boolean(options.danger));
    modalConfirm.classList.toggle("primary-button", !options.danger);
    modalConfirm.textContent = options.confirmLabel || "确认";
    modal.hidden = false;
    modal.classList.add("is-open");
    document.body.classList.add("modal-open");
    if (options.confirm) {
      modalResolver = null;
      var result = new Promise(function (resolve) { modalResolver = resolve; });
      window.setTimeout(function () {
        (options.danger ? modalConfirm : modalCancel).focus();
      }, 0);
      return result;
    }
    window.setTimeout(function () {
      var closeButton = modal.querySelector("[data-modal-close]");
      if (closeButton) closeButton.focus();
    }, 0);
    return Promise.resolve(false);
  }

  if (modal) {
    modal.querySelectorAll("[data-modal-close], [data-modal-cancel]").forEach(function (button) {
      button.addEventListener("click", function () { closeModal(false); });
    });
    modalConfirm.addEventListener("click", function () { closeModal(true); });
    modal.addEventListener("click", function (event) {
      if (event.target === modal) closeModal(false);
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && modal && !modal.hidden) closeModal(false);
  });

  function setButtonBusy(button, label) {
    if (!button || button.dataset.busy === "true") return;
    button.dataset.idleHtml = button.innerHTML;
    button.dataset.busy = "true";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.innerHTML = '<span class="button-spinner" aria-hidden="true"></span><span>' + (label || "正在处理...") + "</span>";
  }

  function restoreButton(button) {
    if (!button || button.dataset.busy !== "true") return;
    button.innerHTML = button.dataset.idleHtml || button.innerHTML;
    delete button.dataset.idleHtml;
    delete button.dataset.busy;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }

  document.querySelectorAll("form[data-confirm-title]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.confirmed === "true") {
        delete form.dataset.confirmed;
        return;
      }
      event.preventDefault();
      var submitter = event.submitter;
      openModal(form.dataset.confirmTitle, form.dataset.confirmMessage, {
        confirm: true,
        danger: true,
        confirmLabel: "确认删除"
      }).then(function (confirmed) {
        if (!confirmed) return;
        form.dataset.confirmed = "true";
        if (submitter && submitter.form === form) {
          form.requestSubmit(submitter);
        } else {
          form.requestSubmit();
        }
      });
    });
  });

  document.querySelectorAll("form").forEach(function (form) {
    if (form.hasAttribute("data-chat-form") || form.hasAttribute("data-confirm-title")) return;
    form.addEventListener("submit", function (event) {
      if (event.defaultPrevented) return;
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }

      var submitter = event.submitter;
      if (submitter && submitter.name) {
        var proxy = document.createElement("input");
        proxy.type = "hidden";
        proxy.name = submitter.name;
        proxy.value = submitter.value;
        proxy.dataset.submitterProxy = "true";
        form.appendChild(proxy);
      }

      form.dataset.submitting = "true";
      form.setAttribute("aria-busy", "true");
      form.querySelectorAll('button[type="submit"]').forEach(function (button) {
        button.disabled = true;
      });

      if (submitter) {
        var pendingLabel = submitter.dataset.pendingLabel || "正在处理...";
        setButtonBusy(submitter, pendingLabel);
      }
    });
  });

  document.addEventListener("submit", function (event) {
    if (event.defaultPrevented || !event.target || !event.target.closest) return;
    var dynamicForm = event.target.closest("form[data-confirm-title]");
    if (!dynamicForm || dynamicForm.dataset.confirmed === "true") return;
    event.preventDefault();
    var submitter = event.submitter;
    openModal(dynamicForm.dataset.confirmTitle, dynamicForm.dataset.confirmMessage, {
      confirm: true,
      danger: true,
      confirmLabel: "确认删除"
    }).then(function (confirmed) {
      if (!confirmed) return;
      dynamicForm.dataset.confirmed = "true";
      if (submitter && submitter.form === dynamicForm) dynamicForm.requestSubmit(submitter);
      else dynamicForm.requestSubmit();
    });
  });

  document.querySelectorAll("[data-detail-trigger]").forEach(function (link) {
    link.addEventListener("click", function (event) {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      var originalHtml = link.innerHTML;
      link.classList.add("is-loading");
      link.setAttribute("aria-busy", "true");
      link.innerHTML = '<span class="button-spinner" aria-hidden="true"></span><span>加载中</span>';
      var loading = document.createElement("p");
      loading.className = "modal-loading";
      loading.innerHTML = '<span class="button-spinner" aria-hidden="true"></span><span>正在加载详情...</span>';
      openModal("候选详情", loading);
      fetch(link.href, { headers: { Accept: "text/html" } })
        .then(function (response) {
          if (!response.ok) throw new Error("详情加载失败");
          return response.text();
        })
        .then(function (html) {
          var documentFragment = new DOMParser().parseFromString(html, "text/html");
          var page = documentFragment.querySelector(".page-content");
          if (!page) throw new Error("详情内容不可用");
          page.querySelectorAll(".back-link").forEach(function (item) { item.remove(); });
          if (modal && !modal.hidden) {
            modalTitle.textContent = (documentFragment.title || "候选详情").split(" · ")[0];
            modalBody.replaceChildren(page);
            modalActions.hidden = true;
          }
        })
        .catch(function (error) {
          var message = document.createElement("p");
          message.className = "modal-error";
          message.textContent = error.message || "详情加载失败，请重试。";
          if (modal && !modal.hidden) {
            modalTitle.textContent = "详情加载失败";
            modalBody.replaceChildren(message);
            modalActions.hidden = true;
          }
        })
        .finally(function () {
          link.classList.remove("is-loading");
          link.removeAttribute("aria-busy");
          link.innerHTML = originalHtml;
        });
    });
  });

  var chatPanel = document.querySelector("[data-chat-panel]");
  if (chatPanel) {
    var chatForm = chatPanel.querySelector("[data-chat-form]");
    var chatInput = chatPanel.querySelector("[data-chat-input]");
    var chatMessages = chatPanel.querySelector("[data-chat-messages]");
    var chatPlan = chatPanel.querySelector("[data-chat-plan]");
    var chatConfirm = chatPanel.querySelector("[data-chat-confirm]");
    var chatStatus = chatPanel.querySelector("[data-chat-status]");
    var chatClear = chatPanel.querySelector("[data-chat-clear]");
    var thoughtToggle = chatPanel.querySelector("[data-chat-thought-toggle]");
    var thoughtList = chatPanel.querySelector("[data-chat-thoughts]");
    var thoughtSteps = thoughtList ? Array.from(thoughtList.querySelectorAll("[data-thought-step]")) : [];
    var chatSessionKey = "hotspot.chat.session";
    var thoughtKey = "hotspot.chat.thoughts";
    var chatSessionId = null;
    var currentTaskId = null;
    var pollTimer = null;
    var welcomeMarkup = chatMessages.innerHTML;

    try { chatSessionId = localStorage.getItem(chatSessionKey); } catch (error) {}
    try {
      thoughtToggle.checked = localStorage.getItem(thoughtKey) === "true";
    } catch (error) {}
    if (thoughtList) thoughtList.hidden = !thoughtToggle.checked;

    function syncClearButton() {
      if (chatClear) chatClear.disabled = !chatSessionId && chatMessages.children.length <= 1;
    }

    function setThoughtStep(stage) {
      var order = { parse: 0, sources: 1, run: 2, result: 3 };
      var active = stage === "succeeded" || stage === "partial_success" ? 3
        : stage === "running" ? 2
        : stage === "queued" || stage === "awaiting_confirmation" ? 1
        : stage === "failed" ? -1 : 0;
      thoughtSteps.forEach(function (step) {
        var index = order[step.dataset.thoughtStep];
        step.classList.toggle("is-done", index < active || (active === 3 && index <= active));
        step.classList.toggle("is-active", index === active && active >= 0);
        step.classList.toggle("is-failed", stage === "failed" && index === 2);
      });
    }

    function addChatMessage(role, content) {
      var item = document.createElement("div");
      item.className = "chat-message chat-message-" + role;
      var label = document.createElement("span");
      label.className = "chat-message-role";
      label.textContent = role === "user" ? "You" : "Agent";
      var paragraph = document.createElement("p");
      paragraph.textContent = content;
      item.appendChild(label);
      item.appendChild(paragraph);
      chatMessages.appendChild(item);
      chatMessages.scrollTop = chatMessages.scrollHeight;
      syncClearButton();
    }

    function setChatStatus(label, state) {
      chatStatus.textContent = label;
      if (state) chatStatus.dataset.state = state;
      else delete chatStatus.dataset.state;
      chatPanel.setAttribute("aria-busy", String(state === "queued" || state === "running"));
      setThoughtStep(state || "parse");
    }

    function setPlanValue(selector, value) {
      var target = chatPanel.querySelector(selector);
      if (target) target.textContent = value || "未指定";
    }

    function renderChatPlan(plan, notice, taskId) {
      var sourceLabels = { demo: "演示快照", live: "海外 RSS / Atom", domestic: "国内 RSS / Atom" };
      var outputLabels = { raw_data: "原始数据", summary: "摘要报告", candidate_pool: "候选内容池" };
      setPlanValue("[data-plan-query]", plan.query);
      setPlanValue("[data-plan-source]", sourceLabels[plan.source_mode] || plan.source_mode);
      setPlanValue("[data-plan-time]", plan.time_range);
      setPlanValue("[data-plan-output]", outputLabels[plan.output_mode] || plan.output_mode);
      var noticeBox = chatPanel.querySelector("[data-plan-notice]");
      noticeBox.textContent = notice || "";
      noticeBox.hidden = !notice;
      chatPlan.hidden = false;
      chatConfirm.disabled = false;
      chatConfirm.dataset.taskId = taskId;
      currentTaskId = taskId;
      setChatStatus("等待确认", "awaiting_confirmation");
    }

    function showTaskResult(data) {
      var result = data.result || {};
      var count = result.candidate_count == null ? "" : "，生成 " + result.candidate_count + " 条候选";
      var message = data.status === "partial_success"
        ? "任务完成，但部分来源失败" + count + "。请查看运行记录核验。"
        : (result.output_mode === "candidate_pool"
          ? "任务完成" + count + "，结果已写入候选内容池。"
          : "任务完成，结果已保存到运行记录。");
      addChatMessage("assistant", message);
      if (result.run_id) {
        var item = document.createElement("div");
        item.className = "chat-message chat-message-assistant";
        var link = document.createElement("a");
        link.className = "chat-result-link";
        link.href = "/candidates?scan=" + encodeURIComponent(result.run_id);
        link.textContent = "打开本次候选结果";
        item.appendChild(link);
        chatMessages.appendChild(item);
      }
      chatMessages.scrollTop = chatMessages.scrollHeight;
      syncClearButton();
    }

    function pollChatTask(taskId) {
      if (pollTimer) window.clearTimeout(pollTimer);
      fetch("/api/tasks/" + encodeURIComponent(taskId), { headers: { Accept: "application/json" } })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok) throw new Error(data.detail || "任务状态读取失败");
            return data;
          });
        })
        .then(function (data) {
          var status = data.status;
          setChatStatus(data.progress_label || status, status);
          if (status === "queued" || status === "running") {
            pollTimer = window.setTimeout(function () { pollChatTask(taskId); }, 900);
            return;
          }
          chatConfirm.disabled = true;
          if (status === "succeeded" || status === "partial_success") {
            showTaskResult(data);
          } else if (status === "failed") {
            addChatMessage("assistant", "任务失败：" + (data.error || "未提供错误原因"));
          }
        })
        .catch(function (error) {
          setChatStatus("状态读取失败", "failed");
          addChatMessage("assistant", error.message || "任务状态读取失败");
        });
    }

    function clearChatSession() {
      if (pollTimer) window.clearTimeout(pollTimer);
      pollTimer = null;
      currentTaskId = null;
      chatSessionId = null;
      chatMessages.innerHTML = welcomeMarkup;
      chatPlan.hidden = true;
      chatConfirm.disabled = true;
      delete chatConfirm.dataset.taskId;
      setChatStatus("等待输入", "");
      syncClearButton();
      try { localStorage.removeItem(chatSessionKey); } catch (error) {}
    }

    if (thoughtToggle) {
      thoughtToggle.addEventListener("change", function () {
        thoughtList.hidden = !thoughtToggle.checked;
        try { localStorage.setItem(thoughtKey, String(thoughtToggle.checked)); } catch (error) {}
      });
    }

    if (chatClear) {
      chatClear.addEventListener("click", function () {
        if (chatClear.disabled) return;
        openModal("清空会话", "这会移除当前页面中的聊天记录和待确认计划，是否继续？", {
          confirm: true,
          danger: true,
          confirmLabel: "清空会话"
        }).then(function (confirmed) {
          if (confirmed) clearChatSession();
        });
      });
    }

    chatForm.addEventListener("submit", function (event) {
      event.preventDefault();
      if (chatForm.dataset.submitting === "true") return;
      var message = chatInput.value.trim();
      if (!message) {
        chatInput.focus();
        return;
      }
      chatForm.dataset.submitting = "true";
      var submitButton = chatForm.querySelector("button[type=submit]");
      setButtonBusy(submitButton, "解析中");
      addChatMessage("user", message);
      setChatStatus("解析中", "running");
      fetch("/api/chat/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ message: message, session_id: chatSessionId })
      })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok) throw new Error(data.detail || "需求解析失败");
            return data;
          });
        })
        .then(function (data) {
          chatSessionId = data.session_id;
          try { localStorage.setItem(chatSessionKey, chatSessionId); } catch (error) {}
          addChatMessage("assistant", data.reply);
          renderChatPlan(data.plan, data.fallback_notice, data.task_id);
          chatInput.value = "";
        })
        .catch(function (error) {
          setChatStatus("解析失败", "failed");
          addChatMessage("assistant", error.message || "需求解析失败");
        })
        .finally(function () {
          delete chatForm.dataset.submitting;
          restoreButton(submitButton);
          syncClearButton();
        });
    });

    chatConfirm.addEventListener("click", function () {
      var taskId = chatConfirm.dataset.taskId;
      if (!taskId || chatConfirm.disabled || chatConfirm.dataset.busy === "true") return;
      setButtonBusy(chatConfirm, "确认中");
      setChatStatus("已确认，准备执行", "queued");
      fetch("/api/tasks/" + encodeURIComponent(taskId) + "/confirm", {
        method: "POST",
        headers: { Accept: "application/json" }
      })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok) throw new Error(data.detail || "任务确认失败");
            return data;
          });
        })
        .then(function (data) {
          chatConfirm.disabled = true;
          restoreButton(chatConfirm);
          chatConfirm.disabled = true;
          pollChatTask(data.task_id);
        })
        .catch(function (error) {
          restoreButton(chatConfirm);
          chatConfirm.disabled = false;
          setChatStatus("确认失败", "failed");
          addChatMessage("assistant", error.message || "任务确认失败");
        });
    });

    function loadChatHistory() {
      if (!chatSessionId) {
        syncClearButton();
        return;
      }
      fetch("/api/chat/sessions/" + encodeURIComponent(chatSessionId) + "/messages", { headers: { Accept: "application/json" } })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (data) {
          if (!data || !Array.isArray(data.messages) || !data.messages.length) return;
          chatMessages.innerHTML = "";
          data.messages.forEach(function (message) {
            addChatMessage(message.role, message.content);
          });
          syncClearButton();
        })
        .catch(function () { syncClearButton(); });
    }

    setThoughtStep("parse");
    loadChatHistory();
    syncClearButton();
  }

  syncSidebarControls();
})();