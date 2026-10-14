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

  function render(data) {
    var url = pictureOf(data);
    if (!url || !data.text) {
      return data.text;
    }
    var wrap = $('<span class="pvp-pick"></span>');
    var img = $('<img class="pvp-pick__thumb" alt="" loading="lazy">').attr("src", url);
    var label = $("<span></span>").text(data.text);
    return wrap.append(img).append(label);
  }

  $.fn.select2.defaults.set("templateResult", render);
  $.fn.select2.defaults.set("templateSelection", render);
})();
