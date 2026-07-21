function flashButton(button, text) {
  const original = button.textContent;
  button.textContent = text;
  setTimeout(() => { button.textContent = original; }, 1500);
}

document.addEventListener("click", async (event) => {
  const confirmTarget = event.target.closest("[data-confirm]");
  if (confirmTarget && !window.confirm(confirmTarget.dataset.confirm)) {
    event.preventDefault();
    return;
  }

  const copyText = event.target.closest(".copy-text");
  if (copyText) {
    const target = document.getElementById(copyText.dataset.copyTarget);
    await navigator.clipboard.writeText(target.value);
    flashButton(copyText, "Скопировано");
  }

  const copyLink = event.target.closest(".copy-link");
  if (copyLink) {
    const url = new URL(copyLink.dataset.copyUrl, window.location.origin).href;
    await navigator.clipboard.writeText(url);
    flashButton(copyLink, "Ссылка скопирована");
  }

  const copyCurrentLink = event.target.closest(".copy-current-link");
  if (copyCurrentLink) {
    await navigator.clipboard.writeText(copyCurrentLink.dataset.copyUrl || window.location.href);
    flashButton(copyCurrentLink, "Ссылка скопирована");
  }
});

document.querySelectorAll(".claim-tree").forEach((tree) => {
  const rows = Array.from(tree.querySelectorAll("[data-claim-depth]"));

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
  });
});

document.querySelectorAll(".claim-work-tree").forEach((tree) => {
  const rows = Array.from(tree.querySelectorAll("[data-work-depth]"));

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
    } else {
      refreshGroups();
    }
  });

  refreshGroups();
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
        const sourceRange = source.scrollHeight - source.clientHeight;
        const targetRange = target.scrollHeight - target.clientHeight;
        const ratio = sourceRange > 0 ? source.scrollTop / sourceRange : 0;
        target.scrollTop = ratio * Math.max(targetRange, 0);
        renderActiveLine();
        requestAnimationFrame(() => { syncingScroll = false; });
      });
    };
    connectScroll(editor, preview);
    connectScroll(preview, editor);
  }
});
