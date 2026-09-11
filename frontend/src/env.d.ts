/// <reference types="vite/client" />

type PortalContour = 'SOURCE' | 'TARGET'

interface Window {
  __HTP_CONFIG__?: {
    contour?: PortalContour
  }
}
