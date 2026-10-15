/*
 * Pictures in the admin's autocomplete pickers.
 *
 * The autocomplete JSON carries a `picture` URL per result
 * (calculatorapi.admin_pvp.PictureAutocompleteJsonView), and the option
 * already chosen when a page loads carries it as data-picture
 * (PictureAutocompleteSelect). Both are drawn here through select2's
 * templateResult / templateSelection, set as DEFAULTS before Django's
 * autocomplete.js initialises the widgets at DOM ready: this file is listed
 * after select2 in the picker widget's Media, so it runs in between.
 *
 * A result without a picture renders as plain text, so every other select2
 * on the page (style dropdowns, filters) is unchanged.
 */
(function () {
  "use strict";

  if (!window.django || !window.django.jQuery) {
    return;
  }
  var $ = window.django.jQuery;
  if (!$.fn.select2 || !$.fn.select2.defaults) {
    return;
  }

  function pictureOf(data) {
    if (data.picture) {
      return data.picture;
    }
    if (data.element && data.element.dataset && data.element.dataset.picture) {
      return data.element.dataset.picture;
    }
    return "";
  }

  function versionOf(data) {
    if (data.version) {
      return data.version;
    }
    if (data.element && data.element.dataset && data.element.dataset.version) {
      return data.element.dataset.version;
    }
    return "";
  }

  function render(data) {
    var url = pictureOf(data);
    var version = versionOf(data);
    if ((!url && !version) || !data.text) {
      return data.text;
    }
    var wrap = $('<span class="pvp-pick"></span>');
    if (url) {
      wrap.append($('<img class="pvp-pick__thumb" alt="" loading="lazy">').attr("src", url));
    }
    wrap.append($("<span></span>").text(data.text));
    if (version) {
      // "○ white" / "◎ gold" / "evolved": which version of the effect this is.
      wrap.append($('<span class="pvp-pick__version"></span>').text(version));
    }
    return wrap;
  }

  $.fn.select2.defaults.set("templateResult", render);
  $.fn.select2.defaults.set("templateSelection", render);

  /*
   * Pickers on a hidden tab wait for their tab.
   *
   * Django's autocomplete.js initialises every .admin-autocomplete at DOM
   * ready through $.fn.djangoAdminSelect2. A full guide has 150 or more,
   * nearly all on tabs that are not open, and select2 takes a few ms each,
   * so the page sat for half a second after it had drawn (measured in
   * headless Chrome on a fast machine; a laptop pays more) before it would
   * respond. The helper is wrapped here (this file loads after
   * autocomplete.js, before DOM ready): a picker on a tab that is not open
   * is held back (opening the tab then costs about 0.4 s for 30), and the
   * hash naming its tab (the tab links are #<tab> anchors, and "Save and
   * continue" lands on one) initialises it then, on the next animation
   * frame, once Alpine has shown the tab, so select2 measures a visible
   * element. The tabs are the links in #pvp-tabs and each tab's wrapper is
   * #<tab>-group (templates/admin/calculatorapi/pvpguide/change_form.html);
   * a picker outside every wrapper (the Guide tab's scenario) and every
   * picker on a page without the tab strip initialise as before. Rows added
   * with "Add another" come through the same helper (formset:added), on the
   * open tab, so they initialise at once.
   */
  var initialise = $.fn.djangoAdminSelect2;
  if (!initialise) {
    return;
  }
  var pending = {};

  function tabKeys() {
    return $("#pvp-tabs a[href^='#']").map(function () {
      return this.hash.slice(1);
    }).get();
  }

  function currentTab() {
    var hash = window.location.hash.replace(/^#/, "");
    return tabKeys().indexOf(hash) >= 0 ? hash : "general";
  }

  function tabOf(element) {
    var keys = tabKeys();
    for (var i = 0; i < keys.length; i++) {
      var group = document.getElementById(keys[i] + "-group");
      if (group && group.contains(element)) {
        return keys[i];
      }
    }
    return null;
  }

  $.fn.djangoAdminSelect2 = function () {
    var open = currentTab();
    var now = this.filter(function () {
      var tab = tabOf(this);
      if (tab === null || tab === open) {
        return true;
      }
      (pending[tab] = pending[tab] || []).push(this);
      return false;
    });
    initialise.call(now);
    return this;
  };

  function wake(tab) {
    var selects = pending[tab];
    if (!selects) {
      return;
    }
    delete pending[tab];
    initialise.call($(selects));
  }

  window.addEventListener("hashchange", function () {
    var tab = currentTab();
    window.requestAnimationFrame(function () {
      wake(tab);
    });
  });
})();
