/*
 * The PvP guide page's text boxes (admin_pvp.py):
 *
 *  - a Preview toggle under every Markdown box (textarea[data-pvp-markdown]),
 *    rendered with marked (static/admin/vendor/marked.min.js) plus the two
 *    inline marks the site understands: ==words== in red and [[Skill name]]
 *    as a chip. Raw HTML is escaped first, as the site does (no rehype-raw).
 *  - skill-name suggestions in every box that may mention a skill
 *    ([data-pvp-mentions]): typing "[[" opens a list from the guide admin's
 *    skill-names endpoint; Enter or a click completes "[[Name]]".
 *
 * Plain DOM code, no bundler, like spaces-image-picker.js. Rows added with
 * "Add another" are picked up through Django's formset:added event.
 */
(function () {
  "use strict";

  var ENDPOINT = "skill-names/";
  var MENTION_BEFORE_CARET = /\[\[([^\[\]\n]*)$/;

  // ── Markdown preview ──────────────────────────────────────────────────

  function escapeHtml(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // marked extensions for the two marks; both inline, both before emphasis.
  var highlightExtension = {
    name: "pvpHighlight",
    level: "inline",
    start: function (src) {
      return src.indexOf("==");
    },
    tokenizer: function (src) {
      var match = /^==([^=\n]+)==/.exec(src);
      if (match) {
        return {
          type: "pvpHighlight",
          raw: match[0],
          tokens: this.lexer.inlineTokens(match[1]),
        };
      }
      return undefined;
    },
    renderer: function (token) {
      return '<mark class="pvp-red">' + this.parser.parseInline(token.tokens) + "</mark>";
    },
  };

  var mentionExtension = {
    name: "pvpMention",
    level: "inline",
    start: function (src) {
      return src.indexOf("[[");
    },
    tokenizer: function (src) {
      var match = /^\[\[([^\[\]\n]+)\]\]/.exec(src);
      if (match) {
        return { type: "pvpMention", raw: match[0], name: match[1].trim() };
      }
      return undefined;
    },
    renderer: function (token) {
      return '<span class="pvp-skill">' + escapeHtml(token.name) + "</span>";
    },
  };

  var markedReady = false;
  function render(markdown) {
    if (!window.marked) {
      return "<p>Preview needs marked.min.js, which did not load.</p>";
    }
    if (!markedReady) {
      window.marked.use({ gfm: true, breaks: false, extensions: [highlightExtension, mentionExtension] });
      markedReady = true;
    }
    return window.marked.parse(escapeHtml(markdown));
  }

  function addPreview(textarea) {
    if (textarea.dataset.pvpPreviewReady || /__prefix__/.test(textarea.name)) {
      return;
    }
    textarea.dataset.pvpPreviewReady = "1";

    var bar = document.createElement("div");
    bar.className = "pvp-editor-bar";
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = "Preview";
    button.setAttribute("aria-pressed", "false");
    bar.appendChild(button);

    var box = document.createElement("div");
    box.className = "pvp-preview";
    box.hidden = true;

    textarea.insertAdjacentElement("afterend", bar);
    bar.insertAdjacentElement("afterend", box);

    function refresh() {
      box.innerHTML = render(textarea.value || "");
    }
    button.addEventListener("click", function () {
      var open = box.hidden;
      box.hidden = !open;
      button.setAttribute("aria-pressed", open ? "true" : "false");
      button.textContent = open ? "Hide preview" : "Preview";
      if (open) {
        refresh();
      }
    });
    textarea.addEventListener("input", function () {
      if (!box.hidden) {
        refresh();
      }
    });
  }

  // ── [[Skill suggestions ───────────────────────────────────────────────

  var list = null; // one shared dropdown
  var active = null; // {field, start, items, index}

  function endpointUrl() {
    // The guide page lives at .../pvpguide/<id>/change/ (or /add/); the
    // endpoint is a sibling of the changelist: .../pvpguide/skill-names/.
    var match = /^(.*\/pvpguide\/)/.exec(window.location.pathname);
    return match ? match[1] + ENDPOINT : null;
  }

  function buildList() {
    list = document.createElement("ul");
    list.className = "pvp-suggest";
    list.hidden = true;
    list.setAttribute("role", "listbox");
    document.body.appendChild(list);
    list.addEventListener("mousedown", function (event) {
      var item = event.target.closest("li[data-name]");
      if (item) {
        event.preventDefault();
        complete(item.dataset.name);
      }
    });
  }

  function closeList() {
    if (list) {
      list.hidden = true;
      list.innerHTML = "";
    }
    active = null;
  }

  function placeList(field) {
    var rect = field.getBoundingClientRect();
    list.style.left = rect.left + window.scrollX + "px";
    list.style.top = rect.bottom + window.scrollY + 4 + "px";
    list.style.minWidth = Math.min(rect.width, 420) + "px";
  }

  function showItems(items) {
    list.innerHTML = "";
    if (!items.length) {
      var empty = document.createElement("li");
      empty.className = "pvp-suggest__empty";
      empty.textContent = "No skill on global matches";
      list.appendChild(empty);
    }
    items.forEach(function (item, index) {
      var li = document.createElement("li");
      li.dataset.name = item.name;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", index === 0 ? "true" : "false");
      if (item.picture) {
        var img = document.createElement("img");
        img.src = item.picture;
        img.alt = "";
        li.appendChild(img);
      }
      li.appendChild(document.createTextNode(item.name));
      list.appendChild(li);
    });
    list.hidden = false;
  }

  function highlight(index) {
    if (!active) {
      return;
    }
    var options = list.querySelectorAll("li[data-name]");
    if (!options.length) {
      return;
    }
    active.index = (index + options.length) % options.length;
    options.forEach(function (li, i) {
      li.setAttribute("aria-selected", i === active.index ? "true" : "false");
    });
    options[active.index].scrollIntoView({ block: "nearest" });
  }

  function complete(name) {
    if (!active) {
      return;
    }
    var field = active.field;
    var value = field.value;
    var caret = field.selectionStart;
    var before = value.slice(0, active.start) + "[[" + name + "]]";
    var after = value.slice(caret);
    // Skip a "]]" the editor may already have typed after the caret.
    if (after.indexOf("]]") === 0) {
      after = after.slice(2);
    }
    field.value = before + after;
    field.selectionStart = field.selectionEnd = before.length;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    closeList();
    field.focus();
  }

  var pending = null;
  function query(field, term, start) {
    var url = endpointUrl();
    if (!url) {
      return;
    }
    if (pending) {
      clearTimeout(pending);
    }
    pending = setTimeout(function () {
      fetch(url + "?q=" + encodeURIComponent(term), { credentials: "same-origin" })
        .then(function (response) { return response.ok ? response.json() : { results: [] }; })
        .then(function (data) {
          if (!active || active.field !== field || active.start !== start) {
            return;
          }
          active.items = data.results || [];
          active.index = 0;
          placeList(field);
          showItems(active.items);
        })
        .catch(closeList);
    }, 150);
  }

  function onInput(event) {
    var field = event.target;
    var caret = field.selectionStart;
    var before = field.value.slice(0, caret);
    var match = MENTION_BEFORE_CARET.exec(before);
    if (!match) {
      if (active && active.field === field) {
        closeList();
      }
      return;
    }
    if (!list) {
      buildList();
    }
    active = { field: field, start: caret - match[0].length, items: [], index: 0 };
    query(field, match[1], active.start);
  }

  function onKeydown(event) {
    if (!active || active.field !== event.target || list.hidden) {
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      highlight(active.index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      highlight(active.index - 1);
    } else if (event.key === "Enter" || event.key === "Tab") {
      var chosen = active.items[active.index];
      if (chosen) {
        event.preventDefault();
        complete(chosen.name);
      }
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeList();
    }
  }

  function addSuggestions(field) {
    if (field.dataset.pvpMentionsReady || /__prefix__/.test(field.name)) {
      return;
    }
    field.dataset.pvpMentionsReady = "1";
    field.setAttribute("autocomplete", "off");
    field.addEventListener("input", onInput);
    field.addEventListener("keydown", onKeydown);
    field.addEventListener("blur", function () {
      // Let a click on the list land first (its mousedown completes).
      setTimeout(function () {
        if (active && active.field === field) {
          closeList();
        }
      }, 120);
    });
  }

  // ── Wiring ────────────────────────────────────────────────────────────

  function enhance(root) {
    root.querySelectorAll("textarea[data-pvp-markdown]").forEach(addPreview);
    root.querySelectorAll("[data-pvp-mentions]").forEach(addSuggestions);
  }

  function ready() {
    enhance(document);
    document.addEventListener("formset:added", function (event) {
      var target = event.target && event.target.querySelectorAll ? event.target : document;
      enhance(target);
    });
    window.addEventListener("resize", function () {
      if (active && list && !list.hidden) {
        placeList(active.field);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ready);
  } else {
    ready();
  }
})();
