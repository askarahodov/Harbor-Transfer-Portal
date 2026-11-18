/* Shared offline theme control for Portal and /docs/ (same origin and storage key).
 * An absent or unavailable localStorage entry means "follow OS preference".
 */
(function () {
  'use strict'

  var storageKey = 'htp.ui.theme.v1'
  function normalize(value) {
    return value === 'dark' || value === 'light' ? value : 'system'
  }
  function read() {
    try {
      return normalize(window.localStorage.getItem(storageKey))
    } catch {
      return 'system'
    }
  }

  var choice = read()
  function apply() {
    if (choice === 'system') {
      document.documentElement.removeAttribute('data-theme')
    } else {
      document.documentElement.setAttribute('data-theme', choice)
    }
    window.dispatchEvent(new window.CustomEvent('htp-theme-change', {
      detail: { choice: choice },
    }))
  }

  function set(choiceRequested) {
    choice = normalize(choiceRequested)
    try {
      if (choice === 'system') {
        window.localStorage.removeItem(storageKey)
      } else {
        window.localStorage.setItem(storageKey, choice)
      }
    } catch {
      // Theme remains usable for this tab when storage is blocked.
    }
    apply()
  }

  window.HtpTheme = { get: function () { return choice }, set: set }
  window.addEventListener('storage', function (event) {
    if (event.key === storageKey || event.key === null) {
      choice = read()
      apply()
    }
  })
  apply()
})()
