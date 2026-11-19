/** Loaded before the SPA by /theme-control.js; also shared with /docs/. */
type HtpThemeChoice = 'system' | 'light' | 'dark'

interface HtpThemeController {
  get(): HtpThemeChoice
  set(choice: HtpThemeChoice): void
}

interface Window {
  HtpTheme?: HtpThemeController
}
