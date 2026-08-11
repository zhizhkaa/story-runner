function flashButton(button, text) {
  const original = button.innerHTML;
  button.textContent = text;
  const status = document.getElementById("ui-status");
  if (status) status.textContent = text;
  setTimeout(() => { button.innerHTML = original; }, 1500);
}

function russianCount(value, one, few, many) {
  const mod100 = value % 100;
  const mod10 = value % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

window.storyRunnerIcons?.createIcons();

async function copyWithFeedback(button, action) {
  try {
    await action();
    flashButton(button, "Скопировано");
  } catch (_error) {
    flashButton(button, "Не удалось скопировать");
    const status = document.getElementById("ui-status");
    if (status) status.textContent = "Не удалось скопировать. Выделите и скопируйте текст вручную.";
  }
}

function escapedHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formattedCopyHtml(value) {
  return value.split("\n").map((line) => {
    const leadingSpaces = line.match(/^ */)[0].replaceAll(" ", "&nbsp;");
    const content = escapedHtml(line.trimStart()).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    return `${leadingSpaces}${content}`;
  }).join("<br>");
}

async function copyFormattedText(value) {
  if (navigator.clipboard.write && window.ClipboardItem) {
    const item = new ClipboardItem({
      "text/html": new Blob([formattedCopyHtml(value)], { type: "text/html" }),
    });
    await navigator.clipboard.write([item]);
    return;
  }
  await navigator.clipboard.writeText(value);
}

let pendingConfirmTarget = null;

function openConfirmDialog(target) {
  const dialog = document.getElementById("confirm-dialog");
  const title = dialog?.querySelector("#confirm-dialog-title");
  const body = dialog?.querySelector("#confirm-dialog-body");
  const action = dialog?.querySelector("[data-confirm-dialog-action]");
  if (!dialog || !title || !body || !action) return;

  pendingConfirmTarget = target;
  title.textContent = target.dataset.confirmTitle || target.getAttribute("aria-label") || "Подтвердить действие?";
  body.textContent = target.dataset.confirm;
  action.textContent = target.dataset.confirmAction || "Продолжить";
  const danger = target.dataset.confirmTone === "danger";
  action.className = danger
    ? "rounded-xl bg-red-700 px-4 py-2 text-sm font-medium text-white hover:bg-red-800"
    : "rounded-xl bg-zinc-950 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800";
  dialog.showModal();
}

document.addEventListener("click", async (event) => {
  const confirmTarget = event.target.closest("[data-confirm]");
  if (confirmTarget) {
    event.preventDefault();
    openConfirmDialog(confirmTarget);
    return;
  }

  const confirmAction = event.target.closest("[data-confirm-dialog-action]");
  if (confirmAction) {
    const target = pendingConfirmTarget;
    pendingConfirmTarget = null;
    confirmAction.closest("dialog")?.close();
    if (target?.form) {
      target.form.requestSubmit(target);
    } else if (target?.href) {
      window.location.assign(target.href);
    }
    return;
  }

  const dialogOpen = event.target.closest("[data-dialog-open]");
  if (dialogOpen) {
    const dialog = document.getElementById(dialogOpen.dataset.dialogOpen);
    dialog?.querySelector("form")?.reset();
    dialog?.showModal();
    return;
  }

  const dialogClose = event.target.closest("[data-dialog-close]");
  if (dialogClose) {
    dialogClose.closest("dialog")?.close();
    return;
  }

  const copyText = event.target.closest(".copy-text");
  if (copyText) {
    const target = document.getElementById(copyText.dataset.copyTarget);
    await copyWithFeedback(copyText, () => copyFormattedText(target.value));
    return;
  }

  const copyLink = event.target.closest(".copy-link");
  if (copyLink) {
    const url = new URL(copyLink.dataset.copyUrl, window.location.origin).href;
    await copyWithFeedback(copyLink, () => navigator.clipboard.writeText(url));
    return;
  }

  const copyCurrentLink = event.target.closest(".copy-current-link");
  if (copyCurrentLink) {
    const url = copyCurrentLink.dataset.copyUrl || window.location.href;
    await copyWithFeedback(copyCurrentLink, () => navigator.clipboard.writeText(url));
  }
});

document.querySelectorAll("[data-bulk-reset-form]").forEach((form) => {
  const boxes = Array.from(document.querySelectorAll("[data-bulk-reset-box]"))
    .filter((box) => box.form === form);
  const submit = form.querySelector("[data-bulk-reset-submit]");

  const updateSubmit = () => {
    const selectedCount = boxes.filter((box) => box.checked).length;
    submit.disabled = selectedCount === 0;
    submit.textContent = selectedCount
      ? `${submit.dataset.label} (${selectedCount})`
      : submit.dataset.label;
  };

  boxes.forEach((box) => box.addEventListener("change", updateSubmit));
  form.addEventListener("reset", () => window.setTimeout(updateSubmit));
  updateSubmit();
});

document.querySelectorAll("[data-dialog]").forEach((dialog) => {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
});

document.querySelectorAll(".claim-tree").forEach((tree) => {
  const rows = Array.from(tree.querySelectorAll("[data-claim-depth]"));
  const form = tree.closest("form");
  const submit = form?.querySelector("[data-claim-submit]");
  const selected = form?.querySelector("[data-claim-selected]");
  const search = form?.querySelector("[data-claim-search]");
  const empty = tree.querySelector("[data-claim-empty]");
  const sections = Array.from(tree.querySelectorAll("[data-claim-section]"));

  const setRowSelected = (row, checkbox) => {
    row.classList.toggle("bg-zinc-100", checkbox.checked || checkbox.indeterminate);
  };

  const descendants = (index) => {
    const parentDepth = Number(rows[index].dataset.claimDepth);
    const result = [];
    for (let cursor = index + 1; cursor < rows.length; cursor += 1) {
      const depth = Number(rows[cursor].dataset.claimDepth);
      if (depth <= parentDepth) break;
      result.push(rows[cursor]);
    }
    return result;
  };

  const updateGroupState = (index) => {
    const checkbox = rows[index].querySelector(".claim-node");
    if (!checkbox || checkbox.disabled || rows[index].dataset.claimKind === "check") return;
    const leaves = descendants(index)
      .filter((row) => row.dataset.claimKind === "check")
      .map((row) => row.querySelector(".claim-node"))
      .filter((input) => input && !input.disabled);
    const selectedCount = leaves.filter((input) => input.checked).length;
    checkbox.checked = leaves.length > 0 && selectedCount === leaves.length;
    checkbox.indeterminate = selectedCount > 0 && selectedCount < leaves.length;
    setRowSelected(rows[index], checkbox);
  };

  const updateAncestors = (index) => {
    let childDepth = Number(rows[index].dataset.claimDepth);
    for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
      const depth = Number(rows[cursor].dataset.claimDepth);
      if (depth < childDepth) {
        updateGroupState(cursor);
        childDepth = depth;
      }
    }
  };

  const updateSelection = () => {
    const checked = rows.filter((row) => row.dataset.claimKind === "check")
      .map((row) => row.querySelector(".claim-node"))
      .filter((input) => input && !input.disabled && input.checked).length;
    if (selected) {
      selected.textContent = checked
        ? `Выбрано: ${checked} ${russianCount(checked, "пункт", "пункта", "пунктов")}`
        : "Пункты не выбраны";
    }
    if (submit) {
      submit.disabled = checked === 0;
      const label = submit.querySelector("span");
      if (label) label.textContent = checked ? `Начать · ${checked}` : "Начать";
    }
  };

  const filterRows = () => {
    const query = search?.value.trim().toLocaleLowerCase("ru") || "";
    const visible = new Set();
    if (query) {
      rows.forEach((row, index) => {
        if (!(row.dataset.claimText || "").includes(query)) return;
        visible.add(row);
        let childDepth = Number(row.dataset.claimDepth);
        for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
          const depth = Number(rows[cursor].dataset.claimDepth);
          if (depth < childDepth) {
            visible.add(rows[cursor]);
            childDepth = depth;
          }
          if (childDepth === 0) break;
        }
      });
    }

    rows.forEach((row) => { row.hidden = Boolean(query) && !visible.has(row); });
    let visibleSections = 0;
    sections.forEach((section) => {
      const hasVisibleRows = Array.from(section.querySelectorAll("[data-claim-depth]"))
        .some((row) => !row.hidden);
      section.hidden = Boolean(query) && !hasVisibleRows;
      if (!section.hidden) visibleSections += 1;
      if (query && hasVisibleRows) section.open = true;
    });
    if (empty) empty.classList.toggle("hidden", visibleSections > 0 || !query);
  };

  tree.addEventListener("change", (event) => {
    const checkbox = event.target.closest(".claim-node");
    if (!checkbox || checkbox.disabled) return;
    const row = checkbox.closest("[data-claim-depth]");
    const index = rows.indexOf(row);
    if (index === -1) return;

    checkbox.indeterminate = false;
    if (row.dataset.claimKind !== "check") {
      descendants(index).forEach((descendant) => {
        const child = descendant.querySelector(".claim-node");
        if (child && !child.disabled) {
          child.checked = checkbox.checked;
          child.indeterminate = false;
          setRowSelected(descendant, child);
        }
      });
    }
    setRowSelected(row, checkbox);
    updateAncestors(index);
    updateSelection();
  });

  rows.forEach((row, index) => {
    const checkbox = row.querySelector(".claim-node");
    if (checkbox) setRowSelected(row, checkbox);
    updateGroupState(index);
  });
  search?.addEventListener("input", filterRows);
  updateSelection();
  filterRows();
});

document.querySelectorAll(".claim-work-tree").forEach((tree) => {
  const rows = Array.from(tree.querySelectorAll("[data-work-depth]"));
  const form = tree.closest("form");
  const submit = form?.querySelector("[data-work-submit]");
  const summary = form?.querySelector("[data-work-summary]");
  const changes = form?.querySelector("[data-work-changes]");
  const leaves = rows.filter((row) => row.dataset.workKind === "check");
  leaves.forEach((row) => {
    row.dataset.initialAction = row.querySelector(".claim-item-action:checked")?.value || "";
    row.dataset.initialNote = row.querySelector(".claim-note")?.value || "";
  });

  const descendants = (index) => {
    const parentDepth = Number(rows[index].dataset.workDepth);
    const result = [];
    for (let cursor = index + 1; cursor < rows.length; cursor += 1) {
      const depth = Number(rows[cursor].dataset.workDepth);
      if (depth <= parentDepth) break;
      result.push(rows[cursor]);
    }
    return result;
  };

  const leafRows = (index) => descendants(index).filter((row) => row.dataset.workKind === "check");

  const updateGroupStatus = (index) => {
    const groupRadios = Array.from(rows[index].querySelectorAll(".claim-group-action"));
    const selected = leafRows(index)
      .map((row) => row.querySelector(".claim-item-action:checked")?.value || "");
    const sharedValue = selected.length > 0 && selected.every((value) => value && value === selected[0])
      ? selected[0]
      : "";
    groupRadios.forEach((radio) => { radio.checked = radio.value === sharedValue; });
  };

  const updateGroupNote = (index) => {
    const groupNote = rows[index].querySelector(".claim-note");
    const notes = leafRows(index).map((row) => row.querySelector(".claim-note")?.value || "");
    groupNote.value = notes.length > 0 && notes.every((value) => value === notes[0]) ? notes[0] : "";
  };

  const refreshGroups = () => {
    for (let index = rows.length - 1; index >= 0; index -= 1) {
      if (rows[index].dataset.workKind === "group") {
        updateGroupStatus(index);
        updateGroupNote(index);
      }
    }
  };

  const updateSummary = () => {
    const completed = leaves.filter((row) => row.querySelector(".claim-item-action:checked")).length;
    const changed = leaves.filter((row) => {
      const action = row.querySelector(".claim-item-action:checked")?.value || "";
      const note = row.querySelector(".claim-note")?.value || "";
      return action !== row.dataset.initialAction || note !== row.dataset.initialNote;
    }).length;
    if (summary) summary.textContent = `Заполнено ${completed} из ${leaves.length}`;
    if (changes) changes.textContent = changed ? `Изменено: ${changed}` : "Изменений нет";
    if (submit) {
      submit.disabled = completed !== leaves.length;
      const label = submit.querySelector("span");
      if (label) {
        const remaining = leaves.length - completed;
        label.textContent = remaining
          ? `Осталось: ${remaining}`
          : `Отправить ${leaves.length} ${russianCount(leaves.length, "результат", "результата", "результатов")}`;
      }
    }
  };

  tree.addEventListener("change", (event) => {
    const radio = event.target.closest("input[type='radio']");
    if (!radio) return;
    const row = radio.closest("[data-work-depth]");
    const index = rows.indexOf(row);
    if (index === -1) return;

    if (row.dataset.workKind === "group") {
      leafRows(index).forEach((leaf) => {
        const target = leaf.querySelector(`.claim-item-action[value="${radio.value}"]`);
        if (target) target.checked = true;
      });
    }
    refreshGroups();
    updateSummary();
  });

  tree.addEventListener("input", (event) => {
    const note = event.target.closest(".claim-note");
    if (!note) return;
    const row = note.closest("[data-work-depth]");
    const index = rows.indexOf(row);
    if (index === -1) return;

    if (row.dataset.workKind === "group") {
      descendants(index).forEach((descendant) => {
        const childNote = descendant.querySelector(".claim-note");
        if (childNote) childNote.value = note.value;
      });
    }
    refreshGroups();
    updateSummary();
  });

  refreshGroups();
  updateSummary();
});

document.querySelectorAll("[data-result-view]").forEach((view) => {
  const buttons = Array.from(view.querySelectorAll("[data-result-filter]"));
  const sections = Array.from(view.querySelectorAll("[data-result-section]"));
  const empty = view.querySelector("[data-result-empty]");

  const applyFilter = (filter) => {
    let visibleChecks = 0;
    sections.forEach((section) => {
      const rows = Array.from(section.querySelectorAll("[data-result-row]"));
      const visible = new Set();
      rows.forEach((row, index) => {
        if (row.dataset.resultKind !== "check") return;
        const tokens = (row.dataset.resultTokens || "").split(" ");
        const matches = filter === "all"
          || (filter === "exception" && ["not_ok", "skip", "note"].some((token) => tokens.includes(token)))
          || tokens.includes(filter);
        if (!matches) return;
        visible.add(row);
        visibleChecks += 1;
        let childDepth = Number(row.dataset.resultDepth);
        for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
          const depth = Number(rows[cursor].dataset.resultDepth);
          if (depth < childDepth) {
            visible.add(rows[cursor]);
            childDepth = depth;
          }
        }
      });
      rows.forEach((row) => { row.hidden = !visible.has(row); });
      section.hidden = visible.size === 0;
      if (filter !== "all" && visible.size) section.open = true;
    });
    if (empty) empty.classList.toggle("hidden", visibleChecks > 0);
    buttons.forEach((button) => {
      const active = button.dataset.resultFilter === filter;
      button.setAttribute("aria-pressed", String(active));
      button.classList.toggle("ring-2", active);
      button.classList.toggle("ring-zinc-950", active);
      button.classList.toggle("ring-offset-2", active);
    });
  };

  buttons.forEach((button) => button.addEventListener("click", () => applyFilter(button.dataset.resultFilter)));
  applyFilter(buttons.find((button) => button.getAttribute("aria-pressed") === "true")?.dataset.resultFilter || "all");
});

const CYRILLIC_LETTERS = Array.from("абвгдежзиклмнопрстуфхцчшщэюя");

function cyrillicPosition(value) {
  let result = "";
  const base = CYRILLIC_LETTERS.length;
  while (value > 0) {
    const remainder = (value - 1) % base;
    result = CYRILLIC_LETTERS[remainder] + result;
    value = Math.floor((value - 1) / base);
  }
  return result;
}

function outlineCode(path) {
  const parts = path.map((position, depth) => depth === 2 ? cyrillicPosition(position) : String(position));
  const code = parts.join(".");
  return path.length === 1 ? `${code}.` : code;
}

function buildOutlinePreview(value) {
  const stack = [];
  const siblingCounts = new Map();
  return value.replaceAll("\t", "  ").split("\n").map((rawLine) => {
    if (!rawLine.trim()) return "";
    const indent = rawLine.length - rawLine.trimStart().length;
    while (stack.length && indent <= stack.at(-1).indent) stack.pop();
    const parentPath = stack.length ? stack.at(-1).path : [];
    const parentKey = parentPath.join(".");
    const position = (siblingCounts.get(parentKey) || 0) + 1;
    siblingCounts.set(parentKey, position);
    const path = [...parentPath, position];
    stack.push({ indent, path });
    const depth = path.length - 1;
    return `${"  ".repeat(depth)}${outlineCode(path)} ${rawLine.trim()}`;
  }).join("\n");
}

document.querySelectorAll(".outline-editor").forEach((editor) => {
  const preview = document.querySelector(".outline-preview");
  const editorHighlight = editor.closest(".outline-field")?.querySelector(".outline-active-line");
  const previewHighlight = preview?.closest(".outline-field")?.querySelector(".outline-active-line");
  let activeLine = 0;

  const lineFromCaret = (field) => field.value.slice(0, field.selectionStart).split("\n").length - 1;

  const positionHighlight = (field, highlight) => {
    if (!field || !highlight) return;
    const styles = window.getComputedStyle(field);
    const lineHeight = Number.parseFloat(styles.lineHeight);
    const paddingTop = Number.parseFloat(styles.paddingTop);
    const top = paddingTop + activeLine * lineHeight - field.scrollTop;
    const visible = top + lineHeight > 0 && top < field.clientHeight;
    highlight.classList.toggle("hidden", !visible);
    highlight.style.height = `${lineHeight}px`;
    highlight.style.transform = `translateY(${top}px)`;
  };

  const renderActiveLine = () => {
    positionHighlight(editor, editorHighlight);
    positionHighlight(preview, previewHighlight);
  };

  const activateLineFrom = (field) => {
    activeLine = lineFromCaret(field);
    renderActiveLine();
  };

  const updatePreview = () => {
    if (preview) preview.value = buildOutlinePreview(editor.value);
    activeLine = lineFromCaret(editor);
    renderActiveLine();
  };

  editor.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    event.preventDefault();

    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const value = editor.value;
    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    const lineEndIndex = value.indexOf("\n", end);
    const lineEnd = lineEndIndex === -1 ? value.length : lineEndIndex;
    const block = value.slice(lineStart, lineEnd);
    const lines = block.split("\n");
    const hasSelection = start !== end;
    let replacement;
    let caretPosition;

    if (event.shiftKey) {
      const changed = lines.map((line) => line.replace(/^ {1,2}/, ""));
      replacement = changed.join("\n");
      const removedFromFirstLine = lines[0].length - changed[0].length;
      const removedBeforeCaret = Math.min(removedFromFirstLine, start - lineStart);
      caretPosition = start - removedBeforeCaret;
    } else {
      replacement = lines.map((line) => `  ${line}`).join("\n");
      caretPosition = start + 2;
    }

    editor.setRangeText(replacement, lineStart, lineEnd, "start");
    if (hasSelection) {
      editor.setSelectionRange(lineStart, lineStart + replacement.length);
    } else {
      editor.setSelectionRange(caretPosition, caretPosition);
    }
    editor.dispatchEvent(new Event("input"));
  });

  editor.addEventListener("input", updatePreview);
  ["click", "keyup", "select", "focus"].forEach((eventName) => {
    editor.addEventListener(eventName, () => activateLineFrom(editor));
  });
  updatePreview();

  if (preview) {
    ["click", "keyup", "select", "focus"].forEach((eventName) => {
      preview.addEventListener(eventName, () => activateLineFrom(preview));
    });
    let syncingScroll = false;
    const connectScroll = (source, target) => {
      source.addEventListener("scroll", () => {
        if (syncingScroll) return;
        syncingScroll = true;
        target.scrollTop = source.scrollTop;
        renderActiveLine();
        requestAnimationFrame(() => { syncingScroll = false; });
      });
    };
    connectScroll(editor, preview);
    connectScroll(preview, editor);
  }
});
